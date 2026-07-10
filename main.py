"""
AstrBot Plugin - 抖音 AI Bot 1.0.0
自动回复评论、好感度、记忆、心情、主动刷视频、分享解析。
"""
import asyncio
import json
import os
import random
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.message_components import Image, Plain
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest

from .core import config as _config
from .core.config import (
    AFFECTION_STRANGER, AFFECTION_FAN, AFFECTION_ACQUAINTANCE,
    AFFECTION_FRIEND, AFFECTION_INTERACT, AFFECTION_LIKE,
    AFFECTION_REPLY_POSITIVE, AFFECTION_REPLY_NEGATIVE,
    AFFECTION_INSULT, COOKIE_CHECK_INTERVAL,
    PROACTIVE_DEFAULT_POOLS, LLM_MAX_RETRIES,
    LLM_CONSECUTIVE_FAIL_LIMIT, LLM_COOLDOWN_SECONDS,
)
from .core.douyin_api import DouyinAPI
from .core.reply import ReplyEngine
from .core.utils import (
    load_json, save_json, is_sleep_time,
    random_schedule_time, get_affection_level,
    format_duration, truncate_text,
)


@register(
    "astrbot_plugin_douyin_ai_bot",
    "凌溪",
    "抖音 AI Bot — 自动回复评论、好感度、语义记忆、主动刷视频、分享解析",
    "1.0.0",
    "https://github.com/qsbb/astrbot_plugin_douyin_ai_bot",
)
class DouyinBot(Star):
    """抖音 AI Bot 插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        # 初始化数据目录（延迟到有了 StarTools 之后）
        _config.init_data_dir(StarTools.get_data_dir())

        # 运行状态
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._proactive_task: Optional[asyncio.Task] = None
        self._last_cookie_check = 0
        self._first_poll = True

        # 回复去重
        self._replied_at: set = set()

        # 好感度缓存
        self._affection: dict = {}
        if self.config.get("OWNER_UID"):
            owner = str(self.config["OWNER_UID"]).strip()
            if owner:
                self._affection[owner] = 100

        # LLM 熔断
        self._consecutive_llm_failures = 0
        self._llm_cooldown_until = 0

        # 定时调度状态
        self._proactive_times: list[tuple[int, int]] = []
        self._proactive_triggered: set[str] = set()

        # 初始化核心组件（优先从 config 读取，其次从文件读取）
        cookie = (self.config.get("DOUYIN_COOKIE") or "").strip()
        if not cookie and _config.COOKIE_FILE and _config.COOKIE_FILE.exists():
            try:
                cookie = _config.COOKIE_FILE.read_text(encoding="utf-8").strip()
                if cookie:
                    logger.info("[DouyinBot] 从文件加载 Cookie")
            except Exception:
                pass
        self.api = DouyinAPI(cookie)
        self._affection_file = _config.AFFECTION_FILE
        self._replied_at_file = _config.REPLIED_AT_FILE
        self.reply_engine = ReplyEngine(
            self,
            affection_file=_config.AFFECTION_FILE,
            replied_at_file=_config.REPLIED_AT_FILE,
            mood_file=_config.MOOD_FILE,
            memory_file=_config.MEMORY_FILE,
            blacklist_file=_config.BLACKLIST_FILE,
        )

        # 自动加载已回复集合
        self._replied_at = set(load_json(_config.REPLIED_AT_FILE, []))

        # 自动启动（如果有 Cookie）
        if self.api.has_cookie:
            asyncio.create_task(self._auto_start())

        logger.info(
            f"[DouyinBot] 初始化完成 | Cookie: {'已配置' if self.api.has_cookie else '未配置'} | "
            f"主人: {self.config.get('OWNER_NAME', '未设置')}"
        )

        # ── Web 管理面板 API ──
        try:
            from astrbot.api.web import json_response, error_response
            self._WEB_AVAILABLE = True
        except ImportError:
            self._WEB_AVAILABLE = False
            json_response = error_response = None
        if self._WEB_AVAILABLE:
            try:
                self._register_web_apis(context)
                logger.info("[DouyinBot] 已注册 Web 管理面板 API")
            except Exception as e:
                logger.warning(f"[DouyinBot] Web API 注册失败: {e}")
        else:
            logger.info("[DouyinBot] 当前 AstrBot 版本不支持 Plugin Pages，跳过 Web 面板")

    # ── 生命周期 ──

    async def terminate(self):
        """插件卸载时清理。"""
        await self._stop_bot()
        await self.api.close()
        logger.info("[DouyinBot] 已卸载")

    # ── 自动启动 ──

    async def _auto_start(self):
        """延迟启动，等待其他组件就绪。"""
        await asyncio.sleep(3)
        valid, info = await self.api.check_cookie()
        if valid:
            await self._start_bot()
            logger.info(f"[DouyinBot] 自动启动 | {info}")
        else:
            logger.warning(f"[DouyinBot] Cookie 无效，请使用 /dy 登录 更新")

    # ── 启动/停止 ──

    async def _start_bot(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        logger.info("[DouyinBot] 已启动")

    async def _stop_bot(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self._proactive_task and not self._proactive_task.done():
            self._proactive_task.cancel()
            self._proactive_task = None
        logger.info("[DouyinBot] 已停止")

    # ── 主循环 ──

    async def _main_loop(self):
        logger.info("[DouyinBot] 主循环开始")
        while self._running:
            try:
                h = datetime.now().hour
                ss = self.config.get("SLEEP_START", 2)
                se = self.config.get("SLEEP_END", 8)
                if ss <= h < se:
                    await asyncio.sleep(60)
                    continue

                # 定期检查 Cookie
                ci = COOKIE_CHECK_INTERVAL
                if time.time() - self._last_cookie_check > ci:
                    valid, info = await self.api.check_cookie()
                    if not valid:
                        logger.warning(f"[DouyinBot] Cookie 可能需要刷新: {info}")
                    self._last_cookie_check = time.time()

                # 评论轮询
                if self.config.get("ENABLE_REPLY", True):
                    await self._poll_replies()

                # 主动行为调度
                if self.config.get("ENABLE_PROACTIVE", False):
                    await self._check_proactive_schedule()

                await asyncio.sleep(self.config.get("POLL_INTERVAL", 30))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DouyinBot] 主循环出错: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(30)
        self._running = False

    # ── 评论轮询 ──

    async def _poll_replies(self):
        """轮询获取评论通知并处理。"""
        try:
            replies = await self.api.get_comment_replies()
            if not replies:
                if self._first_poll:
                    logger.debug("[DouyinBot] 首次轮询，暂无通知")
                    self._first_poll = False
                return
            self._first_poll = False

            # 按时间戳排序（旧的优先）
            replies.sort(key=lambda x: x.get("timestamp", 0))
            processed = 0
            for comment in replies:
                # 冷却检查
                if processed >= 1:
                    cooldown = self.config.get("REPLY_COOLDOWN", 15)
                    await asyncio.sleep(max(5, cooldown))

                # 去重检查
                should, reason = self.reply_engine.should_reply(comment)
                if not should:
                    logger.debug(f"[DouyinBot] 跳过回复: {reason} | {truncate_text(comment.get('content', ''), 50)}")
                    self.reply_engine.mark_replied(str(comment.get("notice_id", "")))
                    continue

                # 获取视频信息（用于上下文）
                video_info = None
                aweme_id = comment.get("aweme_id", "")
                if aweme_id:
                    try:
                        video_info = await self.api.get_video_detail(aweme_id)
                    except Exception:
                        pass

                # 生成回复
                success = await self._generate_and_reply(comment, video_info)
                if success:
                    self.reply_engine.mark_replied(str(comment.get("notice_id", "")))
                    processed += 1

        except Exception as e:
            logger.error(f"[DouyinBot] 轮询评论异常: {e}")

    async def _generate_and_reply(self, comment: dict, video_info: Optional[dict] = None) -> bool:
        """生成回复并发表评论。"""
        try:
            # 检查 LLM 熔断
            if self._llm_cooldown_until > time.time():
                logger.warning(f"[DouyinBot] LLM 冷却中，跳过回复")
                return False

            # 构建提示词
            prompt = await self.reply_engine.build_reply_prompt(comment, video_info)

            # 调用 LLM 生成回复
            reply_text = await self._call_llm(prompt)
            if not reply_text:
                logger.warning("[DouyinBot] LLM 未生成回复，跳过")
                return False

            # 回复评论
            aweme_id = comment.get("aweme_id", "")
            comment_id = comment.get("comment_id", "")
            if not aweme_id or not comment_id:
                logger.warning("[DouyinBot] 缺少视频/评论 ID，无法回复")
                return False

            success = await self.api.reply_comment(aweme_id, comment_id, reply_text)
            if success:
                # 好感度变更
                if self.config.get("ENABLE_AFFECTION", True):
                    user_id = comment.get("from_user_id", "")
                    if user_id:
                        self.reply_engine.update_affection(user_id, AFFECTION_INTERACT)

                # 保存对话记忆
                if self.config.get("ENABLE_MEMORY", True):
                    await self._save_memory(
                        user_id=comment.get("from_user_id", ""),
                        user_name=comment.get("from_user_name", "用户"),
                        comment=comment.get("content", ""),
                        reply=reply_text,
                    )

                logger.info(
                    f"[DouyinBot] 回复成功 | → {comment.get('from_user_name', '')}: "
                    f"\"{truncate_text(reply_text, 50)}\""
                )
            return success
        except Exception as e:
            logger.error(f"[DouyinBot] 生成回复异常: {e}")
            return False

    async def _call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM 生成文本。"""
        try:
            # 获取合适的 provider
            provider_id = (self.config.get("LLM_PROVIDER_ID") or "").strip()
            if not provider_id:
                # 尝试使用 AstrBot 当前对话模型
                try:
                    pid = await self.context.get_current_chat_provider_id()
                    if pid:
                        provider_id = pid
                except Exception:
                    pass
            if not provider_id:
                # 兜底
                pm = getattr(self.context, "provider_manager", None)
                if pm:
                    providers = getattr(pm, "providers", None) or []
                    for p in providers:
                        pid = getattr(p, "id", None) or getattr(p, "name", None)
                        if pid:
                            provider_id = str(pid)
                            break

            if not provider_id:
                logger.warning("[DouyinBot] 无可用 LLM Provider")
                return None

            # 构造请求
            req = ProviderRequest(prompt=prompt)
            # 通过 provider_manager 获取 LLM 响应
            pm = getattr(self.context, "provider_manager", None)
            if pm is None:
                logger.warning("[DouyinBot] provider_manager 不可用")
                return None

            provider = pm.get_provider(provider_id)
            if provider is None:
                logger.warning(f"[DouyinBot] Provider {provider_id} 未找到")
                return None

            # 重试机制
            for attempt in range(LLM_MAX_RETRIES):
                try:
                    response = await provider.text_chat(prompt)
                    if response and hasattr(response, "completion_text"):
                        text = response.completion_text.strip()
                        if text:
                            self._consecutive_llm_failures = 0
                            return text
                    elif isinstance(response, str) and response.strip():
                        self._consecutive_llm_failures = 0
                        return response.strip()
                except Exception as e:
                    logger.warning(f"[DouyinBot] LLM 调用失败 (第{attempt+1}次): {e}")
                    if attempt < LLM_MAX_RETRIES - 1:
                        await asyncio.sleep(2 ** attempt)

            # 连续失败计数
            self._consecutive_llm_failures += 1
            if self._consecutive_llm_failures >= LLM_CONSECUTIVE_FAIL_LIMIT:
                self._llm_cooldown_until = time.time() + LLM_COOLDOWN_SECONDS
                logger.warning(f"[DouyinBot] LLM 连续失败 {LLM_CONSECUTIVE_FAIL_LIMIT} 次，冷却 {LLM_COOLDOWN_SECONDS}s")
            return None
        except Exception as e:
            logger.error(f"[DouyinBot] LLM 调用异常: {e}")
            return None

    # ── 记忆管理 ──

    async def _save_memory(self, user_id: str, user_name: str, comment: str, reply: str):
        """保存对话记忆。"""
        if not user_id:
            return
        memories = load_json(_config.MEMORY_FILE, [])
        # 简单关键词提取
        keywords = [w for w in comment.split() if len(w) >= 2][:5]
        entry = {
            "user_id": str(user_id),
            "user_name": user_name,
            "comment": truncate_text(comment, 200),
            "reply": truncate_text(reply, 200),
            "keywords": keywords,
            "timestamp": time.time(),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        memories.append(entry)
        # 只保留最近 2000 条
        save_json(_config.MEMORY_FILE, memories[-2000:])

    # ── 主动行为调度 ──

    async def _check_proactive_schedule(self):
        """检查并触发主动刷视频行为。"""
        now_dt = datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")
        sched = load_json(_config.SCHEDULE_FILE, {})

        if sched.get("date") != today_str:
            times_count = self.config.get("PROACTIVE_TIMES_COUNT", 3)
            self._proactive_times = random_schedule_time(times_count)
            self._proactive_triggered = set()
            save_json(_config.SCHEDULE_FILE, {
                "date": today_str,
                "times": self._proactive_times,
                "triggered": list(self._proactive_triggered),
            })
            logger.info(f"[DouyinBot] 新的一天！主动时间：{[f'{h}:{m:02d}' for h,m in self._proactive_times]}")
        elif not self._proactive_times:
            self._proactive_times = [tuple(t) for t in sched.get("times", [])]
            self._proactive_triggered = set(sched.get("triggered", []))

        for ph, pm in self._proactive_times:
            key = f"{ph}:{pm:02d}"
            if key not in self._proactive_triggered and (
                now_dt.hour > ph or (now_dt.hour == ph and now_dt.minute >= pm)
            ):
                if self._proactive_task is None or self._proactive_task.done():
                    self._proactive_task = asyncio.create_task(self._run_proactive())
                    self._proactive_triggered.add(key)
                    save_json(_config.SCHEDULE_FILE, {
                        "date": today_str,
                        "times": self._proactive_times,
                        "triggered": list(self._proactive_triggered),
                    })
                    trigger_log = load_json(_config.PROACTIVE_TRIGGER_LOG_FILE, [])
                    trigger_log.append({
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "type": "proactive",
                        "scheduled": key,
                        "status": "triggered",
                    })
                    save_json(_config.PROACTIVE_TRIGGER_LOG_FILE, trigger_log[-200:])
                    logger.info(f"[DouyinBot] 触发主动刷视频（{key}）")
                    break

    async def _run_proactive(self):
        """执行一次主动刷视频行为。"""
        try:
            count = self.config.get("PROACTIVE_VIDEO_COUNT", 3)
            feed = await self.api.get_feed("recommend", count=count)
            if not feed:
                logger.info("[DouyinBot] 主动刷视频：未获取到视频")
                return

            logger.info(f"[DouyinBot] 主动刷视频：获取到 {len(feed)} 个视频")
            for video in feed:
                try:
                    aweme_id = video.get("aweme_id", "")
                    desc = (video.get("desc") or "无描述")[:100]
                    author = video.get("author", {}).get("nickname", "未知")
                    logger.info(f"[DouyinBot]   📹 {author}: {truncate_text(desc, 60)}")

                    # 调用 LLM 评价
                    prompt = (
                        f"你正在刷抖音，看到一个视频：\n"
                        f"作者：{author}\n"
                        f"描述：{desc}\n\n"
                        f"请给出一个简短的评价（10-30字），"
                        f"并给出一个 1-10 的评分（格式：评分: X）"
                    )
                    evaluation = await self._call_llm(prompt)
                    if not evaluation:
                        continue

                    # 提取评分
                    score = 5
                    score_m = re.search(r"评分:\s*(\d+)", evaluation)
                    if score_m:
                        score = int(score_m.group(1))
                        score = max(1, min(10, score))

                    # 根据评分执行操作
                    if score >= 6 and self.config.get("PROACTIVE_LIKE", True):
                        await self.api.digg_aweme(aweme_id)
                        logger.info(f"[DouyinBot]   👍 点赞 (评分: {score})")

                    if score >= 7 and self.config.get("PROACTIVE_COMMENT", True):
                        comment_prompt = (
                            f"你正在刷抖音，看到一个视频：\n"
                            f"作者：{author}\n"
                            f"描述：{desc}\n"
                            f"你的评分：{score}/10\n\n"
                            f"请写一条抖音评论（10-30字），自然口语化，不要提及评分。"
                        )
                        comment_text = await self._call_llm(comment_prompt)
                        if comment_text:
                            comment_text_clean = re.sub(r'^["\']|["\']$', '', comment_text.strip())
                            # 主动评论使用顶级评论接口
                            params = {"aid": "24", "app_name": "aweme"}
                            form_data = {
                                "aweme_id": aweme_id,
                                "text": comment_text_clean,
                                "now": int(time.time() * 1000),
                            }
                            from .core.douyin_api import API_REPLY
                            session = await self.api._get_session()
                            async with session.post(
                                API_REPLY + "?" + urlencode(params),
                                data=urlencode(form_data),
                                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
                            ) as resp:
                                result = await resp.json()
                                if result.get("status_code") == 0:
                                    logger.info(f"[DouyinBot]   💬 已评论: {comment_text_clean[:30]}")

                    if score >= 8 and self.config.get("PROACTIVE_FOLLOW", True):
                        author_id = str(video.get("author_user_id", video.get("author_id", "")))
                        if author_id:
                            await self.api.follow_user(author_id)
                            logger.info(f"[DouyinBot]   ➕ 已关注: {author}")

                    # 保存观看历史
                    history = load_json(_config.WATCH_HISTORY_FILE, [])
                    history.append({
                        "aweme_id": aweme_id,
                        "desc": desc,
                        "author": author,
                        "score": score,
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    save_json(_config.WATCH_HISTORY_FILE, history[-500:])

                    await asyncio.sleep(5)  # 操作间隔
                except Exception as e:
                    logger.error(f"[DouyinBot] 处理视频异常: {e}")
                    continue
        except Exception as e:
            logger.error(f"[DouyinBot] 主动行为异常: {e}")

    # ── 工具方法 ──

    def _get_bot_user_id(self) -> str:
        """获取 Bot 自身抖音 UID。"""
        return self.config.get("DEDE_USER_ID", "") or self.api._user_id or ""

    def _save_cookie(self, cookie: str):
        """保存 Cookie 到 AstrBot 配置和本地文件。"""
        self.config["DOUYIN_COOKIE"] = cookie
        self.api.update_cookie(cookie)
        if _config.COOKIE_FILE:
            try:
                _config.COOKIE_FILE.write_text(cookie, encoding="utf-8")
            except Exception as e:
                logger.warning(f"[DouyinBot] Cookie 写入文件失败: {e}")

    # ══════════════════════════════════════════
    # 指令组
    # ══════════════════════════════════════════

    @filter.command_group("dy")
    def dy(self):
        """抖音插件指令组。"""
        pass

    @dy.command("状态")
    async def status(self, event: AstrMessageEvent):
        """查看运行状态。"""
        lines = [
            "📊 抖音 Bot 状态",
            "━━━━━━━━━━━━━━",
        ]
        # Cookie 状态
        cookie_ok = self.api.has_cookie
        lines.append(f"Cookie: {'✅ 已配置' if cookie_ok else '❌ 未配置'}")
        if cookie_ok:
            uname = await self.api.get_user_name()
            uid = await self.api.get_user_id()
            lines.append(f"登录用户: {uname or uid or '未知'}")

        # 运行状态
        lines.append(f"运行中: {'✅' if self._running else '❌'}")
        lines.append(f"评论回复: {'✅' if self.config.get('ENABLE_REPLY', True) else '❌'}")
        lines.append(f"主动行为: {'✅' if self.config.get('ENABLE_PROACTIVE', False) else '❌'}")
        lines.append(f"好感度: {'✅' if self.config.get('ENABLE_AFFECTION', True) else '❌'}")
        lines.append(f"心情: {'✅' if self.config.get('ENABLE_MOOD', True) else '❌'}")

        # LLM Provider
        pid = self.config.get("LLM_PROVIDER_ID", "")
        lines.append(f"LLM: {'✅' if pid else '⚠️ 使用默认'}")
        lines.append(f"主人: {self.config.get('OWNER_NAME', '未设置')}")

        # 统计
        replies_count = len(self._replied_at)
        lines.append(f"已回复评论: {replies_count}")
        aff = load_json(_config.AFFECTION_FILE, {})
        lines.append(f"好感度记录: {len(aff)} 人")

        lines.append("━━━━━━━━━━━━━━")
        lines.append("/dy 启动 - 启动 Bot | /dy 停止 - 停止 Bot")
        lines.append("/dy 帮助 - 查看全部命令")

        yield event.plain_result("\n".join(lines))

    @dy.command("启动")
    async def start(self, event: AstrMessageEvent):
        """启动 Bot。"""
        if not self.api.has_cookie:
            yield event.plain_result("❌ 请先配置抖音 Cookie（/dy Cookie）")
            return
        valid, info = await self.api.check_cookie()
        if not valid:
            yield event.plain_result(f"❌ Cookie 无效: {info}\n请更新 Cookie（/dy Cookie <Cookie>）")
            return
        await self._start_bot()
        yield event.plain_result(f"✅ 抖音 Bot 已启动！\n{info}")

    @dy.command("停止")
    async def stop(self, event: AstrMessageEvent):
        """停止 Bot。"""
        await self._stop_bot()
        yield event.plain_result("🛑 抖音 Bot 已停止")

    @dy.command("Cookie")
    async def set_cookie(self, event: AstrMessageEvent, cookie: str = ""):
        """设置抖音 Cookie。"""
        if not cookie:
            yield event.plain_result(
                "📝 请提供 Cookie 字符串\n"
                "用法: /dy Cookie <完整的Cookie>\n\n"
                "💡 获取方式：浏览器打开抖音并登录，按 F12 "
                "→ Application → Cookies → 复制全部 Cookie 字符串"
            )
            return
        self._save_cookie(cookie)
        valid, info = await self.api.check_cookie()
        if valid:
            yield event.plain_result(f"✅ Cookie 设置成功！\n{info}")
        else:
            yield event.plain_result(f"⚠️ Cookie 已设置但验证失败: {info}")

    @dy.command("帮助")
    async def help(self, event: AstrMessageEvent):
        """查看帮助。"""
        text = (
            "📖 抖音 Bot 帮助\n"
            "━━━━━━━━━━━━━━\n"
            "【基础命令】\n"
            "/dy 状态  - 查看运行状态\n"
            "/dy 启动  - 启动 Bot\n"
            "/dy 停止  - 停止 Bot\n"
            "/dy Cookie <cookie>  - 设置抖音 Cookie\n\n"
            "【互动命令】\n"
            "/dy 主动  - 立刻触发一次主动刷视频\n"
            "/dy 好感 [UID]  - 查看好感度\n"
            "/dy 记忆 <关键词>  - 搜索记忆\n"
            "/dy 心情  - 查看今日心情\n"
            "/dy 拉黑 <UID>  - 拉黑用户\n"
            "/dy 黑名单  - 查看黑名单\n\n"
            "【数据命令】\n"
            "/dy 日志  - 查看主动行为日志\n"
            "/dy 统计  - 查看统计数据\n"
            "/dy 帮助  - 显示本帮助\n"
            "━━━━━━━━━━━━━━\n"
            "💡 命令前缀 /dy，所有子命令不带斜杠"
        )
        yield event.plain_result(text)

    @dy.command("主动")
    async def proactive(self, event: AstrMessageEvent):
        """手动触发主动刷视频。"""
        if not self.config.get("ENABLE_PROACTIVE", False):
            yield event.plain_result("⚠️ 主动行为功能未启用（可在配置中开启）")
            return
        if self._proactive_task and not self._proactive_task.done():
            yield event.plain_result("⏳ 已有主动任务正在运行，请等待完成")
            return
        yield event.plain_result("🔄 正在触发主动刷视频...")
        await self._run_proactive()
        yield event.plain_result("✅ 主动刷视频完成！使用 /dy 日志 查看详情")

    @dy.command("好感")
    async def affection(self, event: AstrMessageEvent, uid: str = ""):
        """查看好感度。"""
        aff = load_json(_config.AFFECTION_FILE, {})
        if uid:
            score = aff.get(str(uid), 0)
            level = get_affection_level(score)
            level_names = {
                "owner": "💖 主人", "close": "✨ 好友",
                "friend": "😊 熟人", "normal": "👋 粉丝",
                "stranger": "🌙 陌生人", "cold": "🖤 厌恶",
            }
            yield event.plain_result(
                f"用户 {uid} 好感度: {score} 分 ({level_names.get(level, level)})"
            )
        else:
            if not aff:
                yield event.plain_result("📝 暂无好感度记录")
                return
            # 按分数排序显示前 10
            sorted_aff = sorted(aff.items(), key=lambda x: -x[1])
            lines = ["💛 好感度排行 (Top 10):"]
            for i, (uid, score) in enumerate(sorted_aff[:10], 1):
                level = get_affection_level(score)
                emoji = {"owner": "💖", "close": "✨", "friend": "😊",
                         "normal": "👋", "stranger": "🌙", "cold": "🖤"}.get(level, "🌙")
                lines.append(f"{i}. {emoji} {uid}: {score}分")
            yield event.plain_result("\n".join(lines))

    @dy.command("记忆")
    async def memory(self, event: AstrMessageEvent, keyword: str = ""):
        """搜索记忆。"""
        if not keyword:
            yield event.plain_result("用法: /dy 记忆 <关键词>")
            return
        memories = load_json(_config.MEMORY_FILE, [])
        if not memories:
            yield event.plain_result("📝 暂无记忆")
            return
        relevant = []
        kw_lower = keyword.lower()
        for m in memories:
            comment = (m.get("comment", "") or "").lower()
            reply = (m.get("reply", "") or "").lower()
            m_kws = [k.lower() for k in (m.get("keywords") or [])]
            if kw_lower in comment or kw_lower in reply or any(kw_lower in k for k in m_kws):
                relevant.append(m)
        if not relevant:
            yield event.plain_result(f"🔍 未找到与「{keyword}」相关的记忆")
            return
        lines = [f"🔍 找到 {len(relevant)} 条相关记忆:\n"]
        for m in relevant[-10:]:
            lines.append(f"👤 {m.get('user_name', '用户')}:")
            lines.append(f"  💬 {truncate_text(m.get('comment', ''), 60)}")
            lines.append(f"  🤖 {truncate_text(m.get('reply', ''), 60)}")
            lines.append(f"  📅 {m.get('date', '')}\n")
        yield event.plain_result("\n".join(lines))

    @dy.command("心情")
    async def mood(self, event: AstrMessageEvent):
        """查看今日心情。"""
        mood = self.reply_engine.get_or_refresh_mood()
        yield event.plain_result(f"🎭 今日心情: {mood}")

    @dy.command("拉黑")
    async def blacklist_add(self, event: AstrMessageEvent, uid: str = ""):
        """拉黑用户。"""
        if not uid:
            yield event.plain_result("用法: /dy 拉黑 <用户UID>")
            return
        self.reply_engine.add_blacklist(uid)
        self.reply_engine.update_affection(uid, -100)
        yield event.plain_result(f"⛔ 用户 {uid} 已被拉黑")

    @dy.command("黑名单")
    async def blacklist_list(self, event: AstrMessageEvent):
        """查看黑名单。"""
        bl = load_json(_config.BLACKLIST_FILE, [])
        if not bl:
            yield event.plain_result("📝 黑名单为空")
            return
        yield event.plain_result("⛔ 黑名单:\n" + "\n".join(f"• {uid}" for uid in bl))

    @dy.command("日志")
    async def log(self, event: AstrMessageEvent):
        """查看主动行为日志。"""
        logs = load_json(_config.PROACTIVE_TRIGGER_LOG_FILE, [])
        if not logs:
            yield event.plain_result("📝 暂无日志")
            return
        recent = logs[-20:]
        lines = ["📋 最近日志 (最多20条):"]
        for log_entry in recent:
            lines.append(
                f"[{log_entry.get('time', '')}] "
                f"{log_entry.get('type', '')} "
                f"({log_entry.get('status', '')})"
            )
        yield event.plain_result("\n".join(lines))

    @dy.command("统计")
    async def stats(self, event: AstrMessageEvent):
        """查看统计数据。"""
        aff = load_json(_config.AFFECTION_FILE, {})
        memories = load_json(_config.MEMORY_FILE, [])
        history = load_json(_config.WATCH_HISTORY_FILE, [])
        bl = load_json(_config.BLACKLIST_FILE, [])
        text = (
            "📊 抖音 Bot 统计\n"
            f"━━━━━━━━━━━━━━\n"
            f"已回复评论: {len(self._replied_at)}\n"
            f"好感度记录: {len(aff)} 人\n"
            f"记忆条目: {len(memories)}\n"
            f"观看历史: {len(history)}\n"
            f"黑名单: {len(bl)} 人\n"
            f"运行时长: {'运行中' if self._running else '已停止'}"
        )
        yield event.plain_result(text)

    # ── LLM 请求钩子（分享解析 + 记忆注入） ──

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        """解析消息中的抖音分享链接（在 LLM 处理前拦截）。"""
        if not self.config.get("ENABLE_SHARE_PARSE", False):
            return
        try:
            msg_str = event.get_message_str()
        except Exception:
            return
        if not msg_str:
            return

        # 检测抖音分享链接
        patterns = [
            r"https?://v\.douyin\.com/\w+",
            r"https?://www\.douyin\.com/video/\d+",
            r"https?://www\.douyin\.com/note/\d+",
            r"https?://vm\.douyin\.com/\w+",
        ]
        for p in patterns:
            m = re.search(p, msg_str)
            if m:
                url = m.group(0)
                info = await self.api.resolve_share_url(url)
                if info:
                    text = (
                        f"📹 抖音视频分享\n"
                        f"━━━━━━━━━━\n"
                        f"作者：{info['author']}\n"
                        f"描述：{truncate_text(info['title'], 100)}\n"
                        f"播放：{info['play_count']} | 点赞：{info['digg_count']}\n"
                        f"━━━━━━━━━━\n"
                        f"{url}"
                    )
                    yield event.plain_result(text)
                    return

    # ══════════════════════════════════════════
    # Web 管理面板 API
    # ══════════════════════════════════════════

    PLUGIN_NAME = "astrbot_plugin_douyin_ai_bot"

    def _register_web_apis(self, context: Context) -> None:
        """注册 Web 管理面板 API 路由。"""
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/status", self._web_status, ["GET"], "插件状态"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/stats", self._web_stats, ["GET"], "插件统计"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/qrcode", self._web_qrcode, ["GET"], "获取登录二维码"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/qrcode/check", self._web_qrcode_check, ["GET"], "检查二维码状态"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/cookie", self._web_get_cookie, ["GET"], "获取当前 Cookie"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/cookie", self._web_set_cookie, ["POST"], "设置 Cookie"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/logs", self._web_logs, ["GET"], "插件日志"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/start", self._web_start, ["POST"], "启动 Bot"
        )
        context.register_web_api(
            f"/{self.PLUGIN_NAME}/stop", self._web_stop, ["POST"], "停止 Bot"
        )

    async def _web_status(self):
        """返回插件状态 JSON。"""
        from astrbot.api.web import json_response, error_response
        try:
            valid = False
            user_info = None
            if self.api.has_cookie:
                try:
                    valid, msg = await self.api.check_cookie()
                except Exception:
                    valid = False
                try:
                    uname = await self.api.get_user_name()
                    uid = await self.api.get_user_id()
                    user_info = {"user_id": uid or "", "nickname": uname or ""}
                except Exception:
                    user_info = None

            return json_response({
                "cookie_configured": self.api.has_cookie,
                "cookie_valid": valid,
                "user_info": user_info,
                "running": self._running,
                "reply_enabled": self.config.get("ENABLE_REPLY", True),
                "proactive_enabled": self.config.get("ENABLE_PROACTIVE", False),
                "affection_enabled": self.config.get("ENABLE_AFFECTION", True),
                "memory_enabled": self.config.get("ENABLE_MEMORY", True),
                "mood_enabled": self.config.get("ENABLE_MOOD", True),
                "share_parse_enabled": self.config.get("ENABLE_SHARE_PARSE", False),
                "owner_name": self.config.get("OWNER_NAME", ""),
                "llm_provider": self.config.get("LLM_PROVIDER_ID", ""),
                "poll_interval": self.config.get("POLL_INTERVAL", 30),
                "reply_probability": self.config.get("REPLY_PROBABILITY_PERCENT", 80),
                "mood": self.reply_engine.get_or_refresh_mood() if hasattr(self, "reply_engine") else "",
                "replied_count": len(self._replied_at),
            })
        except Exception as e:
            logger.error(f"[DouyinBot] Web 状态 API 异常: {e}")
            return error_response(str(e), status_code=500)

    async def _web_stats(self):
        """返回插件统计数据。"""
        aff = load_json(_config.AFFECTION_FILE, {})
        memories = load_json(_config.MEMORY_FILE, [])
        history = load_json(_config.WATCH_HISTORY_FILE, [])
        bl = load_json(_config.BLACKLIST_FILE, [])
        logs = load_json(_config.PROACTIVE_TRIGGER_LOG_FILE, [])

        from astrbot.api.web import json_response
        return json_response({
            "replied_count": len(self._replied_at),
            "affection_users": len(aff),
            "memory_entries": len(memories),
            "watch_history": len(history),
            "blacklist_count": len(bl),
            "proactive_logs": len(logs),
            "running": self._running,
            "uptime": int(time.time()) if self._running else 0,
        })

    async def _web_qrcode(self):
        """获取登录二维码（不可用，返回说明）。"""
        from astrbot.api.web import json_response
        return json_response({
            "ok": False,
            "message": "抖音 SSO 存在反爬机制，不支持服务端自动扫码。请手动获取 Cookie：\n"
                       "1. 浏览器打开 https://www.douyin.com 并登录\n"
                       "2. 按 F12 → Application → Cookies → www.douyin.com\n"
                       "3. 复制全部 Cookie 值\n"
                       "4. 在下方「手动输入 Cookie」处粘贴保存",
        })

    async def _web_qrcode_check(self):
        """检查二维码扫描状态（不可用）。"""
        from astrbot.api.web import json_response, request
        _ = request.query.get("token", "")
        return json_response({"status": -1, "status_msg": "QR 码登录不可用"})

    async def _web_get_cookie(self):
        """获取当前 Cookie（部分掩码）。"""
        from astrbot.api.web import json_response
        cookie = self.api._cookie or ""
        masked = cookie[:30] + "******" if len(cookie) > 30 else cookie
        file_exists = _config.COOKIE_FILE and _config.COOKIE_FILE.exists()
        return json_response({
            "configured": bool(cookie),
            "cookie_masked": masked,
            "cookie_length": len(cookie),
            "cookie_file": str(_config.COOKIE_FILE) if _config.COOKIE_FILE else "",
            "cookie_file_exists": file_exists,
        })

    async def _web_set_cookie(self):
        """设置 Cookie（来自 QR 扫码登录或手动输入）。"""
        from astrbot.api.web import json_response, error_response, request
        try:
            payload = await request.json(default={}) or {}
            cookie = (payload.get("cookie") or "").strip()
            if not cookie:
                return error_response("缺少 cookie 字段", status_code=400)

            # 保存到配置和文件
            self._save_cookie(cookie)

            # 验证
            valid, msg = await self.api.check_cookie()
            if valid:
                # 自动保存到配置（持久化由 AstrBot 负责）
                uname = await self.api.get_user_name()
                return json_response({
                    "ok": True,
                    "valid": True,
                    "message": f"Cookie 设置成功！用户: {uname}",
                    "user_name": uname,
                })
            else:
                return json_response({
                    "ok": True,
                    "valid": False,
                    "message": f"Cookie 已设置但验证失败: {msg}",
                })
        except Exception as e:
            logger.error(f"[DouyinBot] 设置 Cookie API 异常: {e}")
            return error_response(str(e), status_code=500)

    async def _web_logs(self):
        """返回插件日志。"""
        from astrbot.api.web import json_response
        # 读取 astrbot 日志文件中与本插件相关的最近日志
        log_lines = []
        try:
            # 尝试从插件数据目录读取日志
            log_file = _config.REPLIED_AT_FILE.parent / "plugin.log"
            if log_file.exists():
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    log_lines = lines[-100:]  # 最近 100 行
        except Exception:
            pass
        return json_response({"logs": log_lines, "count": len(log_lines)})

    async def _web_start(self):
        """启动 Bot。"""
        from astrbot.api.web import json_response, error_response
        if self._running:
            return json_response({"ok": True, "message": "Bot 已在运行"})
        valid, info = await self.api.check_cookie()
        if not valid:
            return error_response(f"Cookie 无效: {info}", status_code=400)
        await self._start_bot()
        return json_response({"ok": True, "message": "Bot 已启动"})

    async def _web_stop(self):
        """停止 Bot。"""
        from astrbot.api.web import json_response
        await self._stop_bot()
        return json_response({"ok": True, "message": "Bot 已停止"})

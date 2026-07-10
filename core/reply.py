"""评论回复处理逻辑模块。

负责构建回复提示词、调用 LLM 生成回复、管理回复去重与冷却。
"""

import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

from .config import (
    AFFECTION_PROMPTS,
    AFFECTION_STRANGER, AFFECTION_FAN,
    AFFECTION_ACQUAINTANCE, AFFECTION_FRIEND,
    MOOD_TEMPLATES, FESTIVAL_MOODS,
)
from .utils import load_json, save_json, get_affection_level, truncate_text


class ReplyEngine:
    """评论回复引擎：构建提示词、调用 LLM、管理状态。"""

    def __init__(
        self,
        plugin,
        affection_file: Path,
        replied_at_file: Path,
        mood_file: Path,
        memory_file: Path,
        blacklist_file: Path,
    ):
        self.plugin = plugin
        self._affection_file = affection_file
        self._replied_at_file = replied_at_file
        self._mood_file = mood_file
        self._memory_file = memory_file
        self._blacklist_file = blacklist_file
        self._mood: str = ""
        self._mood_date: str = ""
        self._replied_at: set = set()

    # ── 心情系统 ──

    def get_or_refresh_mood(self) -> str:
        """获取当日心情，若未生成则随机生成。"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._mood_date != today:
            # 检查节日彩蛋
            now = datetime.now()
            festival_key = (now.month, now.day)
            festival_mood = FESTIVAL_MOODS.get(festival_key)
            if festival_mood:
                self._mood = festival_mood
            else:
                self._mood = random.choice(MOOD_TEMPLATES)
            self._mood_date = today
            save_json(self._mood_file, {"mood": self._mood, "date": today})
        return self._mood

    # ── 好感度管理 ──

    def get_affection(self, user_id: str) -> int:
        """获取用户好感度。"""
        aff = load_json(self._affection_file, {})
        return aff.get(str(user_id), 0)

    def update_affection(self, user_id: str, delta: int) -> int:
        """更新用户好感度，返回新分数。"""
        aff = load_json(self._affection_file, {})
        uid = str(user_id)
        current = aff.get(uid, 0)
        new_score = max(-100, min(100, current + delta))
        aff[uid] = new_score
        save_json(self._affection_file, aff)
        return new_score

    def get_affection_prompt(self, user_id: str) -> str:
        """根据用户好感度获取回复语气提示词。"""
        # 检测是否是主人
        owner_uid = (self.plugin.config or {}).get("OWNER_UID", "")
        if owner_uid and str(user_id) == str(owner_uid):
            return AFFECTION_PROMPTS["owner"]
        level = get_affection_level(self.get_affection(user_id))
        return AFFECTION_PROMPTS.get(level, AFFECTION_PROMPTS["stranger"])

    # ── 黑名单管理 ──

    def is_blacklisted(self, user_id: str) -> bool:
        """检查用户是否在黑名单中。"""
        bl = load_json(self._blacklist_file, [])
        return str(user_id) in bl

    def add_blacklist(self, user_id: str) -> None:
        """将用户加入黑名单。"""
        bl = load_json(self._blacklist_file, [])
        uid = str(user_id)
        if uid not in bl:
            bl.append(uid)
            save_json(self._blacklist_file, bl)

    # ── 回复构建 ──

    async def build_reply_prompt(
        self,
        comment: dict,
        video_info: Optional[dict] = None,
    ) -> str:
        """构建评论回复的 LLM 提示词。
        
        Args:
            comment: 评论通知字典
            video_info: 可选，被评论视频的信息

        Returns:
            str: LLM 提示词
        """
        config = self.plugin.config or {}
        user_id = comment.get("from_user_id", "")
        user_name = comment.get("from_user_name", "用户")
        content = comment.get("content", "")
        label = comment.get("label", "评论")
        owner_name = config.get("OWNER_NAME", "主人")

        # 好感度提示
        aff_prompt = self.get_affection_prompt(user_id)

        # 心情
        mood = self.get_or_refresh_mood()

        # 系统人设
        if config.get("USE_ASTRBOT_PERSONA", True):
            system_prompt = None  # 使用 AstrBot 默认人设
        else:
            system_prompt = config.get("CUSTOM_SYSTEM_PROMPT", "")

        # 记忆注入
        memory_context = ""
        if config.get("ENABLE_MEMORY", True):
            memory_context = await self._get_memory_context(user_id, content)

        # 自定义回复指令
        custom_instruction = config.get("CUSTOM_REPLY_INSTRUCTION", "")

        # 构建完整 prompt
        parts = []

        if system_prompt:
            parts.append(f"[系统设定] {system_prompt}")

        # 角色设定
        parts.append(f"[当前状态] 心情: {mood}")

        # 视频上下文
        if video_info:
            desc = video_info.get("desc", "") or video_info.get("title", "")
            author = video_info.get("author", {})
            author_name = author.get("nickname", "") if isinstance(author, dict) else ""
            parts.append(f"[视频信息] 作者: {author_name} | 描述: {truncate_text(desc, 200)}")

        # 评论上下文
        parts.append(f"[互动信息] {label}来自「{user_name}」: \"{truncate_text(content, 200)}\"")

        # 好感度
        parts.append(f"[关系状态] {aff_prompt}")

        # 记忆
        if memory_context:
            parts.append(f"[记忆参考] {memory_context}")

        # 自定义指令
        if custom_instruction:
            parts.append(f"[补充指令] {custom_instruction}")

        # 输出约束
        parts.append(
            "[回复要求]\n"
            "1. 用口语化的中文回复，符合抖音评论区的氛围\n"
            "2. 回复不要太长，20-60字之间\n"
            "3. 不要提及自己是AI或机器人\n"
            "4. 回复要自然，像是在和人聊天\n"
            "5. 如果评论涉及辱骂攻击，可以冷淡回应"
        )

        return "\n\n".join(parts)

    async def _get_memory_context(self, user_id: str, query: str) -> str:
        """获取与用户相关的记忆上下文（简单关键词匹配）。"""
        memories = load_json(self._memory_file, [])
        if not memories:
            return ""
        relevant = []
        uid = str(user_id)
        for m in memories:
            if m.get("user_id") == uid:
                score = 0
                kw = m.get("keywords", [])
                for k in kw:
                    if k.lower() in query.lower():
                        score += 1
                if score > 0:
                    relevant.append((score, m))
        relevant.sort(key=lambda x: -x[0])
        if not relevant:
            return ""
        texts = []
        for _, m in relevant[:3]:
            texts.append(f"之前互动: {m.get('content', '')[:100]}")
        return " | ".join(texts)

    # ── 回复去重 ──

    def load_replied_set(self) -> set:
        """加载已回复的通知 ID 集合。"""
        path = self._replied_at_file
        data = load_json(path, [])
        if isinstance(data, list):
            self._replied_at = set(data)
        return self._replied_at

    def mark_replied(self, notice_id: str) -> None:
        """标记通知为已回复。"""
        self._replied_at.add(notice_id)
        path = self._replied_at_file
        save_json(path, list(self._replied_at)[-5000:])

    def should_reply(self, comment: dict) -> tuple[bool, str]:
        """判断是否应该回复此评论。返回 (是否回复, 原因)。"""
        config = self.plugin.config or {}
        user_id = str(comment.get("from_user_id", ""))
        content = comment.get("content", "")
        notice_id = str(comment.get("notice_id", ""))

        # 黑名单
        if self.is_blacklisted(user_id):
            return False, "用户已拉黑"

        # 已回复
        if notice_id in self._replied_at:
            return False, "已回复过"

        # 自检：不回复自己的评论
        bot_user_id = self.plugin._get_bot_user_id()
        if bot_user_id and user_id == bot_user_id:
            return False, "自己的评论"

        # 必回白名单
        always_uids = config.get("REPLY_ALWAYS_UIDS", [])
        if user_id in [str(u) for u in always_uids]:
            return True, "必回白名单"

        # @ 通知必回
        if comment.get("type") == 2:
            return True, "@提及"

        # 好感度优先
        if config.get("ENABLE_AFFECTION", True):
            score = self.get_affection(user_id)
            if score >= AFFECTION_ACQUAINTANCE:
                return True, "高好感用户"

        # 概率回复
        prob = config.get("REPLY_PROBABILITY_PERCENT", 80)
        if random.randint(1, 100) <= prob:
            return True, f"概率命中 ({prob}%)"

        return False, "概率未命中"

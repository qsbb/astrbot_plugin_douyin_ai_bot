"""抖音 API 封装模块。

提供通过 Cookie 模拟登录、获取通知/评论、回复评论、获取视频信息等能力。
所有接口调用基于抖音 Web 版内部 API（https://www.douyin.com）。
"""

import asyncio
import time
import json
import re
import hashlib
import random
from typing import Optional
from urllib.parse import urlencode, unquote

import aiohttp

from astrbot.api import logger


# ── 常量 ──

DOUYIN_BASE = "https://www.douyin.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# API 端点（Web 版内部接口）
API_NOTICE = "https://www.douyin.com/aweme/v1/web/notice/?"
API_NOTICE_NEW = "https://www.douyin.com/aweme/v1/web/notice/new/?build_number=1.0.1"
API_REPLY = "https://www.douyin.com/aweme/v1/web/comment/"
API_USER_INFO = "https://www.douyin.com/aweme/v1/web/user/profile/other/"
API_FEED = "https://www.douyin.com/aweme/v1/web/feed/?"
API_HOT_SEARCH = "https://www.douyin.com/aweme/v1/web/hot/search/list/"
API_VIDEO_DETAIL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?"
API_COMMENT_LIST = "https://www.douyin.com/aweme/v1/web/comment/list/?"
API_FOLLOW = "https://www.douyin.com/aweme/v1/web/commit/follow/user/?"
API_DIGG = "https://www.douyin.com/aweme/v1/web/commit/item/digg/?"
API_SHARE_INFO = "https://www.douyin.com/aweme/v1/web/aweme/share/info/?"
API_LIVE_INFO = "https://www.douyin.com/aweme/v1/web/live/info/?"

# QR 码登录 API（使用 SSO 端点，返回 base64 图片）
API_QR_CODE = "https://sso.douyin.com/get_qrcode/"
API_QR_CHECK = "https://sso.douyin.com/check_qrcode/"

# Cookie 有效性检查 URL
COOKIE_CHECK_URL = "https://www.douyin.com/aweme/v1/web/notice/"


class DouyinAPI:
    """抖音 Web API 封装。"""

    def __init__(self, cookie: str = ""):
        self._cookie = cookie
        self._session: Optional[aiohttp.ClientSession] = None
        self._user_id: Optional[str] = None
        self._user_name: Optional[str] = None

    # ── 会话管理 ──

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": USER_AGENT,
                    "Cookie": self._cookie,
                    "Referer": DOUYIN_BASE + "/",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def update_cookie(self, cookie: str):
        self._cookie = cookie
        # 强制重建 session 以更新 Cookie
        if self._session and not self._session.closed:
            self._session.headers.update({"Cookie": cookie})

    @property
    def has_cookie(self) -> bool:
        return bool(self._cookie and self._cookie.strip())

    # ── 通用请求 ──

    async def _request(self, method: str, url: str, **kwargs) -> Optional[dict]:
        """发起 API 请求，返回 JSON 或 None。"""
        try:
            session = await self._get_session()
            async with session.request(method, url, **kwargs) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"[DouyinAPI] HTTP {resp.status} | {url[:80]}: {text[:200]}")
                    return None
                data = await resp.json()
                return data
        except asyncio.TimeoutError:
            logger.warning(f"[DouyinAPI] 请求超时: {url[:80]}")
            return None
        except Exception as e:
            logger.error(f"[DouyinAPI] 请求异常: {e}", exc_info=True)
            return None

    async def _get(self, url: str, params: dict = None) -> Optional[dict]:
        if params:
            sep = "&" if "?" in url else "?"
            full_url = url + sep + urlencode(params)
        else:
            full_url = url
        return await self._request("GET", full_url)

    async def _post(self, url: str, data: dict = None, json_data: dict = None) -> Optional[dict]:
        return await self._request("POST", url, data=data, json=json_data)

    # ── Cookie 有效性检查 ──

    async def check_cookie(self) -> tuple[bool, str]:
        """检查 Cookie 是否有效。返回 (是否有效, 描述信息)。"""
        if not self.has_cookie:
            return False, "未配置 Cookie"
        params = {
            "aid": "24",
            "app_name": "aweme",
            "count": 1,
            "build_number": "1.0.1",
        }
        data = await self._get(COOKIE_CHECK_URL, params)
        if data is None:
            return False, "网络请求失败"
        if data.get("status_code") == 0:
            # 尝试提取用户信息
            if not self._user_id:
                await self._fetch_self_info()
            return True, f"Cookie 有效 | 用户: {self._user_name or self._user_id or '未知'}"
        return False, f"Cookie 失效: {data.get('status_msg', '未知错误')}"

    async def _fetch_self_info(self):
        """从通知接口提取当前登录用户信息。"""
        data = await self._get(API_NOTICE_NEW)
        if data and data.get("status_code") == 0:
            try:
                info = data.get("data", {})
                if info:
                    self._user_id = str(info.get("user_id", ""))
                    self._user_name = info.get("nickname", "")
            except Exception:
                pass

    # ── 通知与评论 ──

    async def get_notifications(self, cursor: int = 0) -> Optional[dict]:
        """获取评论通知列表（最近收到的评论/@）。
        
        Returns:
            通知列表数据，包含 notifications 列表和 cursor
        """
        params = {
            "aid": "24",
            "app_name": "aweme",
            "cursor": cursor,
            "count": 20,
            "build_number": "1.0.1",
        }
        data = await self._get(API_NOTICE, params)
        if data and data.get("status_code") == 0:
            return data
        return None

    async def get_notice_new_count(self) -> int:
        """获取未读通知数量。"""
        data = await self._get(API_NOTICE_NEW)
        if data and data.get("status_code") == 0:
            return data.get("data", {}).get("notice_new_count", 0)
        return 0

    async def get_comment_replies(self, cursor: int = 0) -> list[dict]:
        """获取需要回复的评论通知（评论 + @）。"""
        data = await self.get_notifications(cursor)
        if not data:
            return []
        notices = data.get("data", {}).get("notifications", [])
        replies = []
        for n in notices:
            n_type = n.get("type", "")
            # type=1: 评论, type=2: @, type=3: 回复
            if n_type in (1, 2, 3):
                replies.append({
                    "notice_id": n.get("id", ""),
                    "type": n_type,
                    "group_id": n.get("group_id", ""),   # 视频/作品 ID
                    "aweme_id": n.get("aweme_id", n.get("group_id", "")),
                    "comment_id": n.get("comment_id", ""),
                    "from_user_id": n.get("from_user_id", ""),
                    "from_user_name": n.get("from_user_name", ""),
                    "content": n.get("content", n.get("comment_content", "")),
                    "timestamp": n.get("timestamp", 0),
                    "label": {1: "评论", 2: "@提及", 3: "回复"}.get(n_type, "未知"),
                })
        return replies

    async def reply_comment(self, aweme_id: str, comment_id: str, text: str) -> bool:
        """回复评论。
        
        Args:
            aweme_id: 视频/作品 ID
            comment_id: 被回复的评论 ID
            text: 回复内容

        Returns:
            bool: 是否成功
        """
        params = {
            "aid": "24",
            "app_name": "aweme",
        }
        form_data = {
            "aweme_id": aweme_id,
            "comment_id": comment_id,
            "text": text,
            "now": int(time.time() * 1000),
        }
        # 模拟 x-www-form-urlencoded
        data_str = urlencode(form_data)
        session = await self._get_session()
        try:
            async with session.post(
                API_REPLY + "?" + urlencode(params),
                data=data_str,
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            ) as resp:
                result = await resp.json()
                if result.get("status_code") == 0:
                    logger.info(f"[DouyinAPI] 评论回复成功: {text[:30]}...")
                    return True
                logger.warning(f"[DouyinAPI] 评论回复失败: {result.get('status_msg', '')}")
                return False
        except Exception as e:
            logger.error(f"[DouyinAPI] 评论回复异常: {e}")
            return False

    # ── 视频/作品 ──

    async def get_feed(self, feed_type: str = "recommend", count: int = 6) -> list[dict]:
        """获取视频 feed 流。
        
        Args:
            feed_type: recommend / hot / follow
            count: 数量
        """
        params = {
            "aid": "24",
            "app_name": "aweme",
            "count": count,
            "type": 0 if feed_type == "recommend" else (1 if feed_type == "hot" else 2),
            "refresh_index": random.randint(0, 100),
        }
        data = await self._get(API_FEED, params)
        if data and data.get("status_code") == 0:
            return data.get("aweme_list", [])
        return []

    async def get_video_detail(self, aweme_id: str) -> Optional[dict]:
        """获取视频详细信息。"""
        params = {
            "aid": "24",
            "app_name": "aweme",
            "aweme_id": aweme_id,
        }
        data = await self._get(API_VIDEO_DETAIL, params)
        if data and data.get("status_code") == 0:
            return data.get("aweme_detail", {})
        return None

    async def get_comments(self, aweme_id: str, cursor: int = 0, count: int = 20) -> Optional[dict]:
        """获取视频评论列表。"""
        params = {
            "aid": "24",
            "app_name": "aweme",
            "aweme_id": aweme_id,
            "cursor": cursor,
            "count": count,
        }
        data = await self._get(API_COMMENT_LIST, params)
        if data and data.get("status_code") == 0:
            return data
        return None

    # ── 互动操作 ──

    async def digg_aweme(self, aweme_id: str) -> bool:
        """点赞视频。"""
        params = {"aid": "24", "app_name": "aweme"}
        form_data = {
            "aweme_id": aweme_id,
            "type": "1",
            "now": int(time.time() * 1000),
        }
        session = await self._get_session()
        try:
            async with session.post(
                API_DIGG + urlencode(params),
                data=urlencode(form_data),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            ) as resp:
                result = await resp.json()
                return result.get("status_code") == 0
        except Exception:
            return False

    async def follow_user(self, user_id: str) -> bool:
        """关注用户。"""
        params = {"aid": "24", "app_name": "aweme"}
        form_data = {
            "follow_type": "1",
            "from_type": "11",
            "now": int(time.time() * 1000),
        }
        session = await self._get_session()
        try:
            async with session.post(
                f"{API_FOLLOW}user_id={user_id}&{urlencode(params)}",
                data=urlencode(form_data),
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            ) as resp:
                result = await resp.json()
                return result.get("status_code") == 0
        except Exception:
            return False

    # ── 用户信息 ──

    async def get_user_info(self, user_id: str) -> Optional[dict]:
        """获取用户信息。"""
        params = {
            "aid": "24",
            "app_name": "aweme",
            "user_id": user_id,
        }
        data = await self._get(API_USER_INFO, params)
        if data and data.get("status_code") == 0:
            return data.get("user_info", {})
        return None

    # ── 分享解析 ──

    async def resolve_share_url(self, url: str) -> Optional[dict]:
        """解析抖音分享链接，获取视频信息。
        
        支持: 抖音分享短链 (v.douyin.com/xxx) 和完整链接。
        """
        # 提取短链 key
        patterns = [
            r"v\.douyin\.com/(\w+)",
            r"douyin\.com/video/(\d+)",
            r"douyin\.com/note/(\d+)",
        ]
        match = None
        for p in patterns:
            m = re.search(p, url)
            if m:
                match = m
                break
        if not match:
            return None

        key = match.group(1)
        # 如果是短链，需要先获取重定向
        if "v.douyin.com" in url:
            session = await self._get_session()
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    final_url = str(resp.url)
                    m = re.search(r"video/(\d+)", final_url)
                    if m:
                        aweme_id = m.group(1)
                    else:
                        m = re.search(r"note/(\d+)", final_url)
                        aweme_id = m.group(1) if m else key
            except Exception:
                aweme_id = key
        else:
            aweme_id = key

        detail = await self.get_video_detail(aweme_id)
        if not detail:
            return None

        return {
            "aweme_id": aweme_id,
            "title": (detail.get("desc") or ""),
            "author": (detail.get("author", {}).get("nickname", "")),
            "author_id": str(detail.get("author_id", "")),
            "cover_url": (detail.get("video", {}).get("cover", {}).get("url_list") or [""])[0],
            "duration": detail.get("duration", 0),
            "play_count": detail.get("statistics", {}).get("play_count", 0),
            "digg_count": detail.get("statistics", {}).get("digg_count", 0),
            "comment_count": detail.get("statistics", {}).get("comment_count", 0),
        }

    async def get_user_id(self) -> str:
        """获取当前登录用户 ID。"""
        if not self._user_id:
            await self._fetch_self_info()
        return self._user_id or ""

    async def get_user_name(self) -> str:
        """获取当前登录用户名。"""
        if not self._user_name:
            await self._fetch_self_info()
        return self._user_name or ""

    # ── 热搜 ──

    async def get_hot_search(self) -> list[dict]:
        """获取抖音热搜榜单。"""
        data = await self._get(API_HOT_SEARCH, {"aid": "24", "app_name": "aweme"})
        if data and data.get("status_code") == 0:
            return data.get("data", {}).get("word_list", [])
        return []

    # ── 直播信息 ──

    async def get_live_info(self, user_id: str) -> Optional[dict]:
        """获取用户直播信息。"""
        params = {
            "aid": "24",
            "app_name": "aweme",
            "user_id": user_id,
        }
        data = await self._get(API_LIVE_INFO, params)
        if data and data.get("status_code") == 0:
            rooms = data.get("data", {}).get("rooms", [])
            return rooms[0] if rooms else None
        return None

    # ── QR 码登录 ──

    async def get_qrcode(self) -> Optional[dict]:
        """获取登录二维码。

        Douyin SSO 端点在服务端存在 JS 反爬质询，无法直连获取二维码。
        此方法返回固定信息，引导用户手动获取 Cookie。
        """
        logger.warning(
            "[DouyinAPI] 抖音 SSO QR 码接口存在反爬 JS 质询，"
            "不支持服务端自动获取。请通过 Web 面板手动获取 Cookie。"
        )
        return None

    async def check_qrcode(self, token: str) -> dict:
        """占位：二维码状态检查（当前不可用）。"""
        return {"status": -1, "status_msg": "QR 码登录不可用，请手动输入 Cookie"}

    async def get_cookie_from_session(self) -> str:
        """从当前 session 中提取完整 Cookie 字符串。"""
        session = await self._get_session()
        cookies = {}
        for cookie in session.cookie_jar:
            if cookie.key and cookie.value:
                cookies[cookie.key] = cookie.value
        if cookies:
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
        return self._cookie or ""

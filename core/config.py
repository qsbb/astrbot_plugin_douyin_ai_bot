"""抖音插件常量与数据路径配置。

注意：不要在此模块顶层调用 StarTools.get_data_dir()，
因为模块导入时插件尚未初始化。DATA_DIR 通过 init_data_dir() 延迟初始化。
"""

from pathlib import Path
from typing import Optional

# 数据目录（延迟初始化）
_DATA_DIR: Optional[Path] = None


def init_data_dir(data_dir: Path) -> None:
    """初始化数据目录并创建所有文件路径属性。"""
    global _DATA_DIR, \
        REPLIED_AT_FILE, AFFECTION_FILE, MEMORY_FILE, \
        MOOD_FILE, PERSONALITY_FILE, SCHEDULE_FILE, \
        PROACTIVE_TRIGGER_LOG_FILE, WATCH_HISTORY_FILE, \
        BLACKLIST_FILE, USER_PROFILE_FILE, CONSOLIDATION_FILE
    _DATA_DIR = data_dir
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    REPLIED_AT_FILE = _DATA_DIR / "replied_at.json"
    AFFECTION_FILE = _DATA_DIR / "affection.json"
    MEMORY_FILE = _DATA_DIR / "memory.json"
    MOOD_FILE = _DATA_DIR / "mood.json"
    PERSONALITY_FILE = _DATA_DIR / "personality.json"
    SCHEDULE_FILE = _DATA_DIR / "schedule.json"
    PROACTIVE_TRIGGER_LOG_FILE = _DATA_DIR / "proactive_trigger_log.json"
    DYNAMIC_SCHEDULE_FILE = _DATA_DIR / "dynamic_schedule.json"
    WATCH_HISTORY_FILE = _DATA_DIR / "watch_history.json"
    BLACKLIST_FILE = _DATA_DIR / "blacklist.json"
    USER_PROFILE_FILE = _DATA_DIR / "user_profiles.json"
    CONSOLIDATION_FILE = _DATA_DIR / "consolidation.json"


# 数据文件（在 init_data_dir 中被赋值）
REPLIED_AT_FILE: Path = None
AFFECTION_FILE: Path = None
MEMORY_FILE: Path = None
MOOD_FILE: Path = None
PERSONALITY_FILE: Path = None
SCHEDULE_FILE: Path = None
PROACTIVE_TRIGGER_LOG_FILE: Path = None
DYNAMIC_SCHEDULE_FILE: Path = None
WATCH_HISTORY_FILE: Path = None
BLACKLIST_FILE: Path = None
USER_PROFILE_FILE: Path = None
CONSOLIDATION_FILE: Path = None


# 好感度等级阈值
AFFECTION_STRANGER = 10       # 陌生人 ≤ 10
AFFECTION_FAN = 30            # 粉丝 ≤ 30
AFFECTION_ACQUAINTANCE = 50   # 熟人 ≤ 50
AFFECTION_FRIEND = 80         # 好友 ≤ 80
AFFECTION_OWNER = 100         # 主人

# 好感度变化常量
AFFECTION_INTERACT = 1        # 每次互动 +1
AFFECTION_LIKE = 2            # 被点赞 +2
AFFECTION_FOLLOW = 3          # 被关注 +3
AFFECTION_REPLY_POSITIVE = 2  # 积极回复 +2
AFFECTION_REPLY_NEGATIVE = -3 # 负面回复 -3
AFFECTION_INSULT = -10        # 辱骂 -10

# 心情模板
MOOD_TEMPLATES = [
    "元气满满", "平静如水", "略带忧伤", "兴奋激动",
    "慵懒放松", "专注认真", "有点小烦", "温柔似水",
    "调皮活泼", "深沉思考", "充满好奇", "感恩满足",
]

# 节日彩蛋（月,日 → 心情描述）
FESTIVAL_MOODS = {
    (1, 1): "新年快乐！充满希望的一天✨",
    (2, 14): "今天是情人节，有点甜甜的感觉💕",
    (3, 8): "女神节快乐！今天要做最靓的仔🌸",
    (4, 1): "愚人节快乐~今天说话可能不太靠谱哦😝",
    (5, 1): "劳动节！但今天只想摸鱼🐟",
    (6, 1): "儿童节！谁还不是个宝宝了呢🎈",
    (10, 1): "国庆节！欢度假期🎉",
    (12, 24): "平安夜~圣诞老人会来吗🎄",
    (12, 25): "圣诞节！Merry Christmas🎅",
    (12, 31): "跨年夜！和过去说拜拜，迎接新一年🎆",
}

# 好感度等级提示词
AFFECTION_PROMPTS = {
    "owner": "这是你的主人。内心：深深的喜爱和依恋。外在：随意、自然、可以撒娇。语气：宠溺、温柔、像亲人。",
    "close": "这是你的好友（好感度高）。内心：真诚关心。外在：温柔亲近。语气：温暖、真实、可以调皮。",
    "friend": "这是熟悉的粉丝（好感度中）。内心：放松和信任。外在：自然，话变多。语气：友好、轻松、偶尔调侃。",
    "normal": "这是普通粉丝（好感度低）。保持善意，温和有礼但保持距离。语气：简洁、客气。",
    "stranger": "这是陌生人。保持礼貌和善意，简洁客气。",
    "cold": "这个人多次恶意攻击你。平静坚定划清界限，回复极简短，不恶语相向。",
}

# LLM 熔断保护
LLM_MAX_RETRIES = 3
LLM_CONSECUTIVE_FAIL_LIMIT = 5
LLM_COOLDOWN_SECONDS = 300  # 5 分钟

# Cookie 刷新间隔（秒）
COOKIE_CHECK_INTERVAL = 6 * 3600  # 6 小时

# 视频池默认配置
PROACTIVE_DEFAULT_POOLS = ["recommend", "hot"]

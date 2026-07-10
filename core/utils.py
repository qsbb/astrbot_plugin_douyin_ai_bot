"""抖音插件的工具函数模块。"""

import json
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from astrbot.api import logger


def load_json(file_path: Path, default: Any = None) -> Any:
    """从 JSON 文件加载数据。"""
    if not file_path.exists():
        return default if default is not None else {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"读取 JSON 文件失败 {file_path.name}: {e}")
        return default if default is not None else {}


def save_json(file_path: Path, data: Any) -> bool:
    """保存数据到 JSON 文件。"""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"保存 JSON 文件失败 {file_path.name}: {e}")
        return False


def is_sleep_time(sleep_start: int = 2, sleep_end: int = 8) -> bool:
    """判断当前是否为睡眠时间。"""
    h = datetime.now().hour
    return sleep_start <= h < sleep_end


def random_schedule_time(count: int, start_hour: int = 8, end_hour: int = 23) -> list[tuple[int, int]]:
    """生成随机调度时间列表。
    
    Args:
        count: 需要生成的时间点数量
        start_hour: 起始小时
        end_hour: 结束小时

    Returns:
        [(hour, minute), ...] 列表
    """
    if count <= 0:
        return []
    # 将时间范围分成 count 段，每段内随机取一个时间
    total_minutes = (end_hour - start_hour) * 60
    segment_minutes = total_minutes // count
    times = []
    for i in range(count):
        seg_start = start_hour * 60 + i * segment_minutes
        seg_end = seg_start + segment_minutes - 1
        if i == count - 1:
            seg_end = end_hour * 60 - 1
        if seg_end <= seg_start:
            seg_end = seg_start + 1
        chosen = random.randint(seg_start, seg_end)
        times.append((chosen // 60, chosen % 60))
    return sorted(times, key=lambda x: (x[0], x[1]))


def get_affection_level(score: int) -> str:
    """根据好感度分数返回等级名称。
    
    - 主人: 100 (特殊)
    - 好友: > 50
    - 熟人: > 30
    - 粉丝: > 10
    - 陌生人: >= 0
    - 厌恶: < 0
    """
    if score >= 100:
        return "owner"
    if score > 50:
        return "close"
    if score > 30:
        return "friend"
    if score > 10:
        return "normal"
    if score >= 0:
        return "stranger"
    return "cold"


def format_duration(seconds: int) -> str:
    """将秒数格式化为可读时长。"""
    if seconds < 60:
        return f"{seconds}秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分{seconds % 60}秒"
    hours = minutes // 60
    return f"{hours}时{minutes % 60}分"


def truncate_text(text: str, max_len: int = 100) -> str:
    """截断文本到指定长度。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."

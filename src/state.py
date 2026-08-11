# -*- encoding: utf-8 -*-
"""
全局运行状态模块
================

供 WebUI 读取的进程内状态：
- 每个录制任务的实时状态（等待/录制中/未开播/错误）
- 滚动日志缓冲

main.py 在各关键节点写入，webui 线程读取；不共享时保持安全默认。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

_lock = threading.Lock()

# 任务状态常量
WAITING = 'waiting'       # 等待检测
RECORDING = 'recording'   # 正在录制
OFFLINE = 'offline'       # 未开播（等待中）
ERROR = 'error'           # 解析出错
STOPPED = 'stopped'       # 已停止/已注释


@dataclass
class TaskStatus:
    url: str = ''
    quality: str = ''
    name: str = ''                  # 配置中的名称（可能含主播前缀）
    platform: str = ''              # 平台显示名
    anchor: str = ''                # 实际主播名
    status: str = WAITING
    started_at: float = 0.0         # 任务创建时间
    recording_since: float = 0.0    # 本次录制开始时间
    last_check: float = 0.0
    message: str = ''
    file: str = ''                  # 当前录制文件路径

    def to_dict(self) -> dict:
        return {
            'url': self.url,
            'quality': self.quality,
            'name': self.name,
            'platform': self.platform,
            'anchor': self.anchor,
            'status': self.status,
            'started_at': self.started_at,
            'recording_since': self.recording_since,
            'last_check': self.last_check,
            'message': self.message,
            'file': self.file,
            'recording_seconds': int(time.time() - self.recording_since) if self.recording_since else 0,
        }


# url -> TaskStatus
_tasks: dict[str, TaskStatus] = {}

# (timestamp, level, message)
_log_buffer: list[tuple[float, str, str]] = []
MAX_LOG_LINES = 500


# ---------------------------------------------------------------------------
# 任务状态
# ---------------------------------------------------------------------------


def register_task(url: str, quality: str = '', name: str = '') -> None:
    with _lock:
        if url not in _tasks:
            _tasks[url] = TaskStatus(url=url, quality=quality, name=name, started_at=time.time())


def update_task(url: str, **kwargs) -> None:
    with _lock:
        task = _tasks.get(url)
        if task is None:
            task = TaskStatus(url=url, started_at=time.time())
            _tasks[url] = task
        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)
        if 'last_check' not in kwargs:
            task.last_check = time.time()


def remove_task(url: str) -> None:
    with _lock:
        _tasks.pop(url, None)


def get_tasks() -> list[dict]:
    with _lock:
        return [t.to_dict() for t in _tasks.values()]


def get_task(url: str) -> Optional[dict]:
    with _lock:
        t = _tasks.get(url)
        return t.to_dict() if t else None


def count_recording() -> int:
    with _lock:
        return sum(1 for t in _tasks.values() if t.status == RECORDING)


# ---------------------------------------------------------------------------
# 日志缓冲
# ---------------------------------------------------------------------------


def add_log(message: str, level: str = 'INFO') -> None:
    with _lock:
        _log_buffer.append((time.time(), level, str(message)))
        if len(_log_buffer) > MAX_LOG_LINES:
            del _log_buffer[:len(_log_buffer) - MAX_LOG_LINES]


def get_logs(limit: int = 200) -> list[dict]:
    with _lock:
        logs = [
            {'time': ts, 'level': lv, 'message': msg}
            for ts, lv, msg in _log_buffer[-limit:]
        ]
    return logs

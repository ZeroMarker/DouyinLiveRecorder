# -*- encoding: utf-8 -*-
"""
URL 配置解析模块
================

替代 main.py 中内嵌的 URL_config.ini 解析逻辑：

- 纯函数式解析一行 → TaskEntry（画质/URL/名称）
- 平台校验复用适配器注册表（registry.match），不再硬编码 host 列表
- 自动清理 URL（clean_url 平台去 query、小红书 host_id 保留）
- 未知链接自动注释、重复行自动去重（写回文件，保持原行为）
- 提供 add / remove / set_commented 供 WebUI 增删任务

URL_config.ini 每行格式（兼容原版）：
    URL
    画质,URL
    URL,名称
    画质,URL,名称
    任意行首加 # 表示注释（暂停录制）
    名称可含"主播: xxx"前缀（用于自动更新直播间地址）
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from src.adapters import registry

# 合法画质
QUALITIES = ("原画", "蓝光", "超清", "高清", "标清", "流畅")
DEFAULT_QUALITY = "原画"

_URL_PATTERN = re.compile(r"(https?://)?(www\.)?[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(:\d+)?(/.*)?")


def contains_url(string: str) -> bool:
    return _URL_PATTERN.search(string) is not None


def normalize_quality(quality: str) -> str:
    return quality if quality in QUALITIES else DEFAULT_QUALITY


def normalize_url(url: str) -> str:
    return 'https://' + url if '://' not in url else url


def parse_entry(line: str, default_quality: str = DEFAULT_QUALITY) -> Optional[TaskEntry]:
    """解析一行内容（不含行首 #）为 TaskEntry；无法解析返回 None。"""
    line = line.strip()
    if not line:
        return None

    # 合并多余的主播前缀（原逻辑：仅保留最后一个 主播: ）
    parts = line.split('主播: ')
    if len(parts) > 2:
        line = f'{parts[0]}主播: {parts[-1]}'

    if re.search('[,，]', line):
        split_line = re.split('[,，]', line)
    else:
        split_line = [line, '']

    if len(split_line) == 1:
        url = split_line[0]
        quality, name = default_quality, ''
    elif len(split_line) == 2:
        if contains_url(split_line[0]):
            quality = default_quality
            url, name = split_line
        else:
            quality, url = split_line
            name = ''
    else:
        quality, url, name = split_line

    quality = normalize_quality(quality.strip())
    url = url.strip()
    if not url:
        return None

    # 兼容"主播: 名称"段
    name = ','.join(split_line[2:]) if len(split_line) > 2 else name

    return TaskEntry(quality=quality, url=normalize_url(url), name=name.strip())


@dataclass
class TaskEntry:
    quality: str
    url: str
    name: str = ''
    commented: bool = False


class TaskStore:
    """URL_config.ini 文件读写与解析。"""

    def __init__(self, path: str, default_quality: str = DEFAULT_QUALITY):
        self.path = path
        self.default_quality = default_quality

    # ---------- 文件 IO ----------

    def _read_lines(self) -> list[str]:
        if not os.path.isfile(self.path):
            return []
        with open(self.path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            return f.readlines()

    def _write_lines(self, lines: list[str]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w', encoding='utf-8-sig') as f:
            f.writelines(lines)

    # ---------- 解析 ----------

    def load(self) -> tuple[list[TaskEntry], list[str]]:
        """解析 URL 文件。

        副作用（与原 main.py 行为一致）：
        - 重复整行 / 重复 URL 自动删除
        - 未知平台链接自动注释（# 前缀）
        - clean_url 平台去除 query 参数
        - 小红书链接保留 host_id 参数

        :return: (有效任务列表, 未知链接列表)
        """
        lines = self._read_lines()
        seen_raw: set[str] = set()
        seen_url: set[str] = set()
        entries: list[TaskEntry] = []
        unknown: list[str] = []
        new_lines: list[str] = []

        for raw in lines:
            if raw in seen_raw:
                continue  # 删除重复整行
            seen_raw.add(raw)

            stripped = raw.strip()
            if len(stripped) < 18:
                new_lines.append(raw)
                continue

            is_comment = stripped.startswith('#')
            content = stripped.lstrip('#').strip()

            entry = parse_entry(content, self.default_quality)
            if entry is None:
                new_lines.append(raw)
                continue

            if entry.url in seen_url:
                continue  # 删除重复 URL
            seen_url.add(entry.url)
            entry.commented = is_comment

            adapter = registry.match(entry.url)
            if adapter is None:
                unknown.append(entry.url)
                if not is_comment:
                    new_lines.append(f'# {content}\n')  # 自动注释未知链接
                else:
                    new_lines.append(raw)
                continue

            # URL 清理
            cleaned = entry.url
            if adapter.clean_url:
                cleaned = cleaned.split('?')[0]
            if 'xiaohongshu' in cleaned:
                m = re.search(r'&host_id=(.*?)(?=&|$)', cleaned)
                if m:
                    cleaned = cleaned.split('?')[0] + f'?host_id={m.group(1)}'

            if cleaned != entry.url:
                entry.url = cleaned
                new_lines.append(self._format_line(entry))
            else:
                new_lines.append(raw)

            entries.append(entry)

        if new_lines != lines:
            self._write_lines(new_lines)
        return entries, unknown

    # ---------- 写回 ----------

    def _format_line(self, entry: TaskEntry) -> str:
        line = f'{entry.quality},{entry.url}'
        if entry.name:
            line += f',{entry.name}'
        if entry.commented:
            line = '# ' + line
        return line + '\n'

    def add(self, url: str, quality: str = '', name: str = '') -> bool:
        """新增任务。URL 不合法/平台不支持返回 False。"""
        url = url.strip()
        if not url:
            return False
        url = normalize_url(url)
        if registry.match(url) is None:
            return False
        entry = TaskEntry(quality=normalize_quality(quality or self.default_quality),
                          url=url, name=name.strip())
        with open(self.path, 'a', encoding='utf-8-sig') as f:
            f.write(self._format_line(entry))
        return True

    def remove(self, url: str) -> bool:
        """删除包含该 URL 的行。"""
        lines = self._read_lines()
        new_lines = [l for l in lines if url not in l]
        if len(new_lines) == len(lines):
            return False
        self._write_lines(new_lines)
        return True

    def set_commented(self, url: str, commented: bool) -> bool:
        """注释（暂停）/取消注释（恢复）某任务。"""
        lines = self._read_lines()
        found = False
        new_lines: list[str] = []
        for l in lines:
            if url in l:
                found = True
                s = l.strip()
                is_c = s.startswith('#')
                if commented and not is_c:
                    l = '# ' + s + '\n'
                elif not commented and is_c:
                    l = s.lstrip('#').strip() + '\n'
            new_lines.append(l)
        if found:
            self._write_lines(new_lines)
        return found

    def replace_url(self, old_url: str, new_line: str) -> bool:
        """用 new_line 替换包含 old_url 的行（start_record 自动更新直播间地址用）。"""
        lines = self._read_lines()
        found = False
        new_lines: list[str] = []
        for l in lines:
            if old_url in l:
                found = True
                new_lines.append(new_line + '\n')
            else:
                new_lines.append(l)
        if found:
            self._write_lines(new_lines)
        return found

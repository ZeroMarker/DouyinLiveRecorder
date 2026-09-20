# -*- encoding: utf-8 -*-
"""部署完整性校验：安装清单（manifest）生成与比对。

问题
----
``deploy/install.sh`` 把源码复制到安装目录（默认 ``/opt/DouyinLiveRecorder``）。
如果复制出的文件来自不同版本——例如 ``webui/app.py`` 是新版、``src/url_config.py``
还是旧版——一侧按新接口调用、另一侧仍按旧接口返回，只会在某条操作路径上炸出
运行时错误（曾出现：``TaskStore.remove()`` 返回 ``bool`` 被当列表遍历 → ``TypeError``，
配置文件里的行已经删掉但停止录制请求没发出去，任务在界面上消失了、ffmpeg 却继续录）。

做法
----
安装时对每个代码文件计算 sha256 写入 ``<install_dir>/.deploy-manifest.json``；
安装结束、主进程启动、WebUI 创建时各比对一次：

- 文件内容不符 / 文件缺失 → ``skew``（跨版本混装，会导致接口不匹配）
- 清单外的多余代码文件 → ``extra``（上次安装的残留或被手工放进来的文件）
- 有安装标记但清单丢失 → ``unverified``（防"删掉清单即绕过校验"）
- 既无清单也无安装标记 → ``no_manifest``（源码直接运行、容器部署等，跳过校验）

哈希取自**源码目录**（``make --source``），清单描述"源码应该长什么样"，因此
安装后的比对是真实比对；旧版从安装目录自身生成清单，比对必然通过，残留文件会
被写进清单"洗白"。清单只覆盖项目条目（源码顶层条目），运维自己放进安装目录的
目录/文件既不参与校验，也不会被安装脚本清理。

运行数据（``config/ downloads/ logs/ backup_config/ .venv/ ffmpeg/ build/ dist/``）
不参与校验，也不会被安装脚本覆盖。安装脚本用 ``excludes`` / ``prune`` 子命令取
这份目录名单与待清理条目，避免两处各写一份。

定位（重要）
------------
这是**运维一致性检查**，不是防篡改：清单是明文 JSON，和服务代码同处一个目录，
且安装脚本会把整个目录 chown 给服务用户，因此凡是能写这个目录的人都能同时改代码
和清单让校验通过。它能发现的是"部署出错"：复制不完整、残留旧文件、手工把不同版本
的文件混在一起、事后有人改了安装目录里的文件；它**不能**替代权限控制、只读挂载或
签名校验。要更强的保证需要把清单放在服务用户不可写的位置（例如 /var/lib 下 root
所有）并对代码目录只读。

命令行
------
``python -m src.deploy_check make     --dir DIR --source SRC [--commit C]``
``python -m src.deploy_check verify   --dir DIR [--json]``
``python -m src.deploy_check prune    --dir DIR --source SRC``
``python -m src.deploy_check sentinel --dir DIR``
``python -m src.deploy_check excludes``

``verify`` 退出码：0 一致；1 混装/无法校验（致命）；2 无清单；3 存在清单外文件
（提示性质，``--strict`` 时按 1 处理）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from typing import Any, Optional

MANIFEST_NAME = '.deploy-manifest.json'
# 安装标记：由 install.sh 写入。清单可能被误删（例如在安装目录里执行 git clean -xfd），
# 只剩标记时说明"这里确实装过"，缺失清单必须告警而不是静默跳过校验。
SENTINEL_NAME = '.deploy-installed'
MANIFEST_SCHEMA = 1

# 「存在清单外文件」的退出码：与真正的失败（skew/unverified=1、no_manifest=2）区分开，
# 供安装脚本"告警但不中止"。
EXTRA_EXIT_CODE = 3

# 顶层目录：安装时不覆盖（运行数据 / 环境 / 构建产物），校验时不参与比对
RUNTIME_ENTRIES = ('config', 'downloads', 'logs', 'backup_config', 'ffmpeg',
                   'build', 'dist', '.git', '.venv')
# 任意层级都跳过的目录
_SKIP_DIRS = frozenset({'__pycache__', '.git', '.venv', '.mypy_cache', '.pytest_cache',
                        'node_modules', 'logs'})
_SKIP_SUFFIXES = ('.pyc', '.pyo', '.tmp')
_READ_CHUNK = 1 << 20

# 同一进程内只比对一次（main.py 与 webui 会各自调用）
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 文件枚举与哈希
# ---------------------------------------------------------------------------


def iter_code_files(root: str, entries: Optional[set[str]] = None) -> list[str]:
    """列出参与校验的代码文件（相对 root 的 posix 路径，已排序）。

    :param entries: 只看这些顶层条目（``None`` = 除运行数据外的全部）。
        安装目录里可能有运维自己放的东西，必须能区分"本项目代码"和"别的东西"，
        否则清单会把这些文件也记进去、清理时连带删掉。
    """
    root = os.path.abspath(root)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        at_root = os.path.abspath(dirpath) == root
        dirnames[:] = sorted(
            name for name in dirnames
            if name not in _SKIP_DIRS
            and not (at_root and entries is None and name in RUNTIME_ENTRIES)
            and not (at_root and entries is not None and name not in entries)
        )
        for name in filenames:
            # 根目录下的普通文件同样要按 entries 过滤：运维放在安装目录根部的
            # .env / start.sh 这类文件不属于本项目，不能参与校验（否则会被算成
            # 「清单外文件」，而清理时又按设计保留它们，形成永远无法消除的告警）
            if at_root and entries is not None and name not in entries:
                continue
            if name in (MANIFEST_NAME, SENTINEL_NAME) or name.endswith(_SKIP_SUFFIXES):
                continue
            found.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, '/'))
    found.sort()
    return found


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(_READ_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root: str, entries: Optional[set[str]] = None) -> dict[str, str]:
    """当前代码文件 → sha256（读不到的文件记为 ``<unreadable>`` 以便报出来）。"""
    result: dict[str, str] = {}
    for rel in iter_code_files(root, entries):
        try:
            result[rel] = sha256_file(os.path.join(root, rel))
        except OSError:
            result[rel] = '<unreadable>'
    return result


# ---------------------------------------------------------------------------
# 清单读写
# ---------------------------------------------------------------------------


def is_safe_component(name: str) -> bool:
    """顶层条目名是否可安全地参与路径拼接与逐行传输。

    只接受不含路径分隔符、控制字符（含换行）、且不是 ``.`` / ``..`` / 空串的名字。
    名字来自安装目录的 ``os.listdir``，安装脚本会用它拼出 ``rm -rf`` 的目标；
    带换行的名字在逐行读取时会被拆成两段，拼出的目标可能变成受保护的运行目录，
    因此这类名字必须让安装直接失败，而不是"尽力处理"。
    """
    if not name or name in ('.', '..') or '/' in name or os.sep in name:
        return False
    return all(ch >= ' ' and ch != '\x7f' for ch in name)


def unsafe_entries(root: str) -> list[str]:
    """安装目录顶层中名字不安全的条目（用于让安装脚本提前失败）。"""
    if not os.path.isdir(root):
        return []
    return sorted(name for name in os.listdir(root) if not is_safe_component(name))


def manifest_path(root: str) -> str:
    return os.path.join(os.path.abspath(root), MANIFEST_NAME)


def project_entries(root: str, source: str = '', manifest: Optional[dict] = None) -> Optional[set[str]]:
    """安装目录里属于本项目代码的顶层条目。

    取源码目录顶层与上一次安装清单所记录顶层路径的并集，再排除运行数据。
    清单只覆盖这些条目：运维自己放进安装目录的目录/文件既不参与哈希校验，
    也不会被安装脚本清理。

    源码未知且没有旧清单时返回 None（无法判断，退化为"除运行数据外全部"）。
    """
    recorded = {p.split('/', 1)[0] for p in ((manifest or load_manifest(root)) or {}).get('files', {})}
    src_is_dir = bool(source) and os.path.isdir(source)
    in_source = set(os.listdir(source)) if src_is_dir else set()
    reserved = set(RUNTIME_ENTRIES) | {MANIFEST_NAME, SENTINEL_NAME}
    if not recorded and not src_is_dir:
        return None          # 既无源码也无旧清单：无法区分，退化为"除运行数据外全部"
    entries = (in_source | recorded) - reserved
    if not entries and not src_is_dir:
        return None
    return entries


def manifest_entries(manifest: Optional[dict]) -> Optional[set[str]]:
    """清单记录的顶层项目条目（``None`` = 清单没记，或当时无法判断）。"""
    if not manifest:
        return None
    entries = manifest.get('entries')
    if entries is None:
        return None
    return set(entries)


def make_manifest(root: str, source: str = '', meta: Optional[dict] = None) -> dict:
    """生成/覆盖安装清单，返回清单内容。

    ``source`` 非空时，文件集合与哈希都取自**源码目录**（相对路径一一对应），
    安装目录只作为清单的落盘位置。这是刻意的：安装后要能拿安装目录和清单比对，
    如果清单是从安装目录自身生成的，比对必然通过，清理失败的残留文件会被写进
    清单"洗白"。所以清单描述的是"源码应该长什么样"。
    """
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    if source and os.path.isdir(source):
        entries = project_entries(root, source)
        files = snapshot(os.path.abspath(source), entries)
    else:
        entries = project_entries(root, source)
        files = snapshot(root, entries)
    info: dict[str, Any] = {
        'schema': MANIFEST_SCHEMA,
        'installed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'source': os.path.abspath(source) if source else '',
        'root': root,
        'entries': sorted(entries) if entries is not None else None,
        'file_count': len(files),
        'files': files,
    }
    if meta:
        info.update(meta)
    target = manifest_path(root)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write('\n')
    os.replace(tmp, target)
    return info


def load_manifest(root: str) -> Optional[dict]:
    """读取安装清单；缺失或损坏返回 None。"""
    try:
        with open(manifest_path(root), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('files'), dict):
        return None
    return data


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------


def verify(root: str, manifest: Optional[dict] = None) -> dict:
    """比对安装目录与清单。

    :return: 结果字典，``state`` 为 ``ok`` / ``skew`` / ``extra`` / ``no_manifest``。
    """
    root = os.path.abspath(root)
    manifest = load_manifest(root) if manifest is None else manifest
    result: dict[str, Any] = {
        'state': 'no_manifest',
        'root': root,
        'manifest': manifest_path(root),
        'checked_at': time.time(),
        'file_count': 0,
        'mismatched': [],
        'missing': [],
        'extra': [],
        'installed_at': '',
        'source': '',
        'commit': '',
        'branch': '',
        'dirty': None,
    }
    if not manifest:
        # 清单缺失：装过（有安装标记）就必须告警，否则校验会被静默绕过
        result['state'] = 'unverified' if os.path.isfile(os.path.join(root, SENTINEL_NAME)) else 'no_manifest'
        return result

    expected: dict[str, str] = manifest['files']
    # 只比对项目条目：运维自建目录既不参与校验，也不报「清单外文件」。
    # 优先用清单记录的条目，避免源码目录被删/被移动后校验范围漂移
    # （那会把运维自建目录误报为清单外文件）；清单没记时才回退到现场推断。
    entries = manifest_entries(manifest)
    if entries is None:
        entries = project_entries(root, str(manifest.get('source', '')), manifest)
    actual = snapshot(root, entries)
    for rel, want in sorted(expected.items()):
        got = actual.get(rel)
        if got is None:
            result['missing'].append(rel)
        elif got != want:
            result['mismatched'].append({'path': rel, 'expected': want, 'actual': got})
    result['extra'] = sorted(set(actual) - set(expected))

    if result['mismatched'] or result['missing']:
        result['state'] = 'skew'
    elif result['extra']:
        result['state'] = 'extra'
    else:
        result['state'] = 'ok'

    result['file_count'] = len(actual)
    result['installed_at'] = str(manifest.get('installed_at', ''))
    result['source'] = str(manifest.get('source', ''))
    result['commit'] = str(manifest.get('commit', ''))
    result['branch'] = str(manifest.get('branch', ''))
    result['dirty'] = manifest.get('dirty')
    return result


def verify_cached(root: str) -> dict:
    """按根目录缓存比对结果（代码文件在运行期不会变，无需每次重新哈希）。

    缓存以"清单文件的 mtime + 大小"为键：重装会重写清单，mtime 变化后自动重新比对，
    否则进程会一直拿着旧的 skew 结论（例如运维重新部署修好了，界面仍然红着）。
    """
    if not root:
        return {}
    key = os.path.abspath(root)
    try:
        stat = os.stat(manifest_path(key))
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit.get('_stamp') == stamp:
            return hit
        fresh = verify(key)
        fresh['_stamp'] = stamp
        _cache[key] = fresh
        return fresh


def invalidate_cache(root: str = '') -> None:
    """丢弃比对缓存（root 为空则全部丢弃）。"""
    with _cache_lock:
        if root:
            _cache.pop(os.path.abspath(root), None)
        else:
            _cache.clear()


def files_lost_on_copy(root: str, source: str = '', manifest: Optional[dict] = None) -> list[str]:
    """升级时会被删除、且源码里没有的文件（安装目录内的"非本项目"文件）。

    项目目录是整体替换的（先删旧代码再从源码复制），因此放在项目目录内部、又不在
    源码里的文件会在升级时消失。这类文件无法自动区分于"上一版残留的代码文件"
    （那正是要清掉的东西），所以不保留，但必须提前列出来让运维自己搬走。
    运行数据目录（config/ downloads/ …）与安装目录顶层的运维文件不在其列——
    它们本就不参与替换。
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return []
    manifest = load_manifest(root) if manifest is None else manifest
    entries = manifest_entries(manifest)
    if entries is None:
        entries = project_entries(root, source, manifest)
    if entries is None:
        return []
    installed = set((manifest or {}).get('files', {}))
    src = os.path.abspath(source) if source and os.path.isdir(source) else ''

    lost: list[str] = []
    for entry in sorted(entries):
        target = os.path.join(root, entry)
        if os.path.isfile(target):
            if entry not in installed and not (src and os.path.exists(os.path.join(src, entry))):
                lost.append(entry)
            continue
        if not os.path.isdir(target):
            continue
        for dirpath, dirnames, filenames in os.walk(target):
            dirnames[:] = sorted(n for n in dirnames if n not in _SKIP_DIRS)
            for name in filenames:
                if name.endswith(_SKIP_SUFFIXES):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, '/')
                if rel in installed:
                    continue
                if src and os.path.exists(os.path.join(src, rel)):
                    continue          # 源码里有，会被重新复制
                lost.append(rel)
    return sorted(lost)


def prune_candidates(root: str, source: str = '', manifest: Optional[dict] = None
                     ) -> tuple[list[str], list[str]]:
    """安装前应删除的旧代码顶层条目，以及必须保留的未知条目。

    删除旧代码是为了让源码里已删除的文件不残留（历史上的 ``webui/static/app.py``
    就是这样留在安装目录里的）。但安装目录里可能有运维自己放的东西，所以只删
    "确实是本项目代码"的条目：

    - 出现在源码目录顶层，或
    - 出现在上一次安装清单记录的文件路径里

    两者都不是的条目（例如运维自建的 ``mybackup/``）一律保留，由调用方给出提示。

    :return: (待删除条目, 保留的未知条目)，均为顶层名字且已排序。
    """
    root = os.path.abspath(root)
    manifest = load_manifest(root) if manifest is None else manifest
    entries = project_entries(root, source, manifest) or set()
    in_source = set(os.listdir(source)) if source and os.path.isdir(source) else set()

    remove: list[str] = []
    preserved: list[str] = []
    for name in sorted(os.listdir(root)):
        if name in RUNTIME_ENTRIES or name in (MANIFEST_NAME, SENTINEL_NAME):
            continue
        if not is_safe_component(name):
            # 交给 unsafe_entries 报错并中止；这里绝不能让这种名字进入删除列表
            preserved.append(name)
            continue
        (remove if name in entries or name in in_source else preserved).append(name)
    return remove, preserved


def summarize(info: dict) -> dict:
    """WebUI 用的精简结果。

    ``/api/status`` 是未鉴权端点（前缀代理的 Basic Auth 是可选部署），因此这里只放
    横幅需要的计数与状态，不返回具体文件路径 / 提交号 / 分支名：详细清单留在服务端
    日志（``report()``）与本地 ``--check`` 输出里。``dirty`` 是布尔值，用于提示
    "该部署含本地改动"，保留。
    """
    if not info:
        return {'state': 'unknown', 'skew_count': 0, 'extra_count': 0, 'file_count': 0,
                'installed_at': '', 'dirty': None}
    skew = [item['path'] for item in info.get('mismatched', [])] + list(info.get('missing', []))
    return {
        'state': info.get('state', 'unknown'),
        'skew_count': len(skew),
        'extra_count': len(info.get('extra', [])),
        'file_count': info.get('file_count', 0),
        'installed_at': info.get('installed_at', ''),
        'dirty': info.get('dirty'),
    }


# ---------------------------------------------------------------------------
# 上报
# ---------------------------------------------------------------------------


def report(info: dict) -> str:
    """把比对结果转成告警文案（有异常时返回空串）。"""
    state = info.get('state')
    if state == 'skew':
        paths = [item['path'] for item in info.get('mismatched', [])] + list(info.get('missing', []))
        head = '、'.join(paths[:5]) + (' 等' if len(paths) > 5 else '')
        return (f'部署校验失败：{len(paths)} 个代码文件与安装清单不一致（{head}），'
                f'属于跨版本混装，接口可能不匹配；请重新执行 deploy/install.sh 并重启服务')
    if state == 'extra':
        extra = info.get('extra', [])
        head = '、'.join(extra[:5]) + (' 等' if len(extra) > 5 else '')
        return (f'部署目录存在 {len(extra)} 个清单外文件（{head}），'
                f'可能是上次安装的残留；建议重新执行 deploy/install.sh')
    if state == 'unverified':
        return (f'安装目录是 deploy/install.sh 安装的，但安装清单 {MANIFEST_NAME} 已丢失或被破坏，'
                f'部署校验无法进行；请重新执行 deploy/install.sh 重建清单')
    return ''


def dirty_notice(info: dict) -> str:
    """安装自含未提交改动的源码时的提示（没有则返回空串）。

    这种安装是自洽的（清单就是按那份源码生成的，校验会一直通过），但安装内容不对应
    任何提交，无法与上游对比，是最容易演变成跨版本混装的状态，必须显式告诉运维。
    """
    if info.get('dirty') and info.get('state') in ('ok', 'extra'):
        commit = str(info.get('commit', ''))[:12] or '未知'
        branch = info.get('branch') or '未知分支'
        return (f'当前部署安装自含未提交改动的源码目录（{branch} {commit} + 本地改动），'
                f'清单已按该内容生成：它能发现"事后被改坏"，但无法判断这些改动是否是你想要的')
    if info.get('state') == 'ok' and not info.get('commit'):
        return '当前部署未记录源码提交号（安装时源码目录不是 git 仓库），无法与上游版本对比'
    return ''


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _print_result(info: dict) -> None:
    state = info.get('state')
    label = {'ok': '一致', 'skew': '跨版本混装', 'extra': '存在清单外文件',
             'unverified': '无法校验（安装清单丢失）',
             'no_manifest': '无安装清单（跳过校验）'}.get(state, state)
    print(f'部署校验: {label}')
    print(f'  目录      : {info.get("root", "")}')
    print(f'  清单      : {info.get("manifest", "")}')
    if info.get('installed_at') or info.get('commit'):
        print(f'  安装时间  : {info.get("installed_at", "")}')
        print(f'  来源提交  : {info.get("commit", "")} '
              f'{info.get("branch", "")} {"(含未提交改动)" if info.get("dirty") else ""}'.rstrip())
    print(f'  代码文件  : {info.get("file_count", 0)} 个')
    for item in info.get('mismatched', [])[:10]:
        print(f'  ✗ 内容不符: {item["path"]}')
    for rel in info.get('missing', [])[:10]:
        print(f'  ✗ 文件缺失: {rel}')
    for rel in info.get('extra', [])[:10]:
        print(f'  ! 清单外文件: {rel}')
    tail = report(info)
    if tail:
        print(f'  → {tail}')


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m src.deploy_check',
                                     description='DouyinLiveRecorder 部署完整性校验')
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('excludes', help='打印安装时应跳过的顶层目录名（运行数据/环境）')

    p_mark = sub.add_parser('sentinel', help='写入安装标记（清单丢失时据此告警）')
    p_mark.add_argument('--dir', required=True, help='安装目录')
    p_mark.add_argument('--source', default='', help='源码目录')
    p_mark.add_argument('--commit', default='', help='源码提交号')
    p_mark.add_argument('--branch', default='', help='源码分支')

    p_prune = sub.add_parser('prune', help='打印安装前应删除的旧代码顶层条目（保留运维自建目录）')
    p_prune.add_argument('--dir', required=True, help='安装目录')
    p_prune.add_argument('--source', default='', help='源码目录（用于判断哪些条目属于本项目）')

    p_make = sub.add_parser('make', help='生成安装清单')
    p_make.add_argument('--dir', required=True, help='安装目录')
    p_make.add_argument('--source', default='', help='源码目录（记录来源）')
    p_make.add_argument('--commit', default='', help='源码提交号')
    p_make.add_argument('--branch', default='', help='源码分支')
    p_make.add_argument('--dirty', action='store_true', help='源码含未提交改动')

    p_verify = sub.add_parser('verify', help='比对安装目录与清单')
    p_verify.add_argument('--dir', required=True, help='安装目录')
    p_verify.add_argument('--json', action='store_true', dest='as_json', help='输出 JSON')
    p_verify.add_argument('--strict', action='store_true',
                          help='把「存在清单外文件」也视为失败（默认仅告警，退出码 3）')

    args = parser.parse_args(argv)

    if args.cmd == 'excludes':
        print('\n'.join(RUNTIME_ENTRIES))
        return 0

    if args.cmd == 'sentinel':
        os.makedirs(args.dir, exist_ok=True)
        with open(os.path.join(os.path.abspath(args.dir), SENTINEL_NAME), 'w', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
            f.write(f'source: {args.source}\ncommit: {args.commit}\nbranch: {args.branch}\n')
        return 0

    if args.cmd == 'prune':
        if not os.path.isdir(args.dir):
            return 0
        unsafe = unsafe_entries(args.dir)
        if unsafe:
            # 名字里带换行/分隔符时逐行传输会被拆开，拼出的删除目标可能命中受保护目录；
            # 直接失败让安装中止（install.sh 会检查本命令的退出码）
            print('安装目录存在名字不安全（含换行/控制字符/路径分隔符）的条目，已中止：', file=sys.stderr)
            for name in unsafe[:5]:
                print(f'  {name!r}', file=sys.stderr)
            return 3
        remove, preserved = prune_candidates(args.dir, args.source)
        lost = files_lost_on_copy(args.dir, args.source)
        if lost:
            # 这些文件随项目目录一起被替换掉，源码里没有；提前列出来供运维搬走
            print(f'提醒：以下 {len(lost)} 个文件位于项目目录内且源码中不存在，'
                  f'升级时会被删除（如需保留请先移出安装目录的项目目录）:', file=sys.stderr)
            for rel in lost[:20]:
                print(f'  {rel}', file=sys.stderr)
            if len(lost) > 20:
                print(f'  … 另有 {len(lost) - 20} 个', file=sys.stderr)
        for name in preserved:
            print(f'保留安装目录中的既有条目（不属于本项目代码，未删除）: {name}', file=sys.stderr)
        # 待删除条目走 stdout，供 install.sh 逐行读取
        for name in remove:
            print(name)
        if preserved:
            print(f'提示: {len(preserved)} 个条目已保留，如需清理请手动删除', file=sys.stderr)
        return 0

    if args.cmd == 'make':
        meta = {'commit': args.commit, 'branch': args.branch,
                'dirty': bool(args.dirty) or None}
        info = make_manifest(args.dir, args.source, meta)
        print(f'已写入安装清单: {manifest_path(args.dir)}（{info["file_count"]} 个代码文件）')
        return 0

    info = verify(args.dir)
    if args.as_json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
    else:
        _print_result(info)
    if info['state'] == 'extra':
        # 「清单外文件」是提示性质的：运维本就会往安装目录里放东西，清理时又按设计保留，
        # 因此不能和 skew/unverified 用同一个退出码，否则安装脚本会把一次正常的升级
        # 当成致命错误中止（代码已替换、服务却不会重启，正好造成"磁盘新代码 / 内存旧代码"）
        return 1 if args.strict else EXTRA_EXIT_CODE
    return {'ok': 0, 'skew': 1, 'unverified': 1, 'no_manifest': 2}.get(info['state'], 1)


if __name__ == '__main__':
    sys.exit(main())

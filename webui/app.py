# -*- encoding: utf-8 -*-
"""
WebUI - FastAPI 应用
====================

提供录制任务 / 配置 / 状态 / 日志 / 已录制文件的管理 API 与页面。

两种运行方式：
1. 内置：`python main.py --web`（与录制主程序同进程，可读实时状态）
2. 独立：`python -m webui`（仅文件级管理，不显示实时录制状态）
"""
from __future__ import annotations

import configparser
import mimetypes
import os
import shutil
import subprocess
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import state, utils
from src.adapters import registry
from src.url_config import DEFAULT_QUALITY, QUALITIES, TaskStore

WEBUI_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(WEBUI_DIR, 'static')

# 常见录制产物扩展名
VIDEO_EXTS = ('.ts', '.mp4', '.flv', '.mkv', '.m3u8', '.mp3', '.m4a', '.ass', '.srt')


class AddTaskBody(BaseModel):
    url: str
    quality: str = ''
    name: str = ''


class AddTaskByIdBody(BaseModel):
    """选平台 + 输 ID 方式添加任务。"""
    platform: str
    id: str
    quality: str = ''
    name: str = ''


def _walk_videos(root: str) -> list[dict]:
    """递归扫描录制产物，返回扁平文件列表。"""
    result: list[dict] = []
    if not os.path.isdir(root):
        return result
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if name.lower().endswith(VIDEO_EXTS):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace('\\', '/')
                try:
                    size = os.path.getsize(full)
                    mtime = os.path.getmtime(full)
                except OSError:
                    size, mtime = 0, 0
                result.append({'name': name, 'path': rel, 'size': size, 'mtime': mtime})
    return result


def _safe_path(root: str, relative_path: str) -> str | None:
    """将 URL 中的相对路径解析到 root，拒绝目录穿越。"""
    root = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, relative_path))
    try:
        if os.path.commonpath((root, full)) != root:
            return None
    except ValueError:
        return None
    return full


def _resolve_video_path(root: str, path: str) -> str:
    """解析状态里的录制路径。

    录制线程在知道最终文件名之前会先把输出目录写入 state，因此 WebUI
    可能拿到的是目录而不是文件。此时选择该目录下最近更新的录制文件。
    返回值始终是相对于下载目录的 URL 路径。
    """
    full = _safe_path(root, path)
    if not full:
        return ''
    if os.path.isfile(full):
        return os.path.relpath(full, root).replace('\\', '/')
    if os.path.isdir(full):
        files = _walk_videos(full)
        if files:
            latest = max(files, key=lambda item: item.get('mtime', 0))
            return os.path.relpath(os.path.join(full, latest['path']), root).replace('\\', '/')
    return ''


def _iter_ffmpeg_mp4(path: str):
    """把浏览器通常不支持的 TS/FLV/MKV 转成可渐进播放的 fragmented MP4。"""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('系统中未找到 ffmpeg，无法播放该格式')
    process = subprocess.Popen(
        [ffmpeg, '-hide_banner', '-loglevel', 'error', '-i', path,
         '-map', '0:v:0?', '-map', '0:a:0?',
         '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
         '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
         '-f', 'mp4', 'pipe:1'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        while True:
            chunk = process.stdout.read(1024 * 1024) if process.stdout else b''
            if not chunk:
                break
            yield chunk
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdout:
            process.stdout.close()


def create_app(config_file: str, url_config_file: str, downloads_path: str,
               script_path: str = '', version: str = 'v4.0.7') -> FastAPI:
    store = TaskStore(url_config_file)
    app = FastAPI(title='DouyinLiveRecorder WebUI', version=version)

    # 跨源仅放行本地开发端口与 Tauri webview 源（桌面端走插件转发，正常不触发）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            'http://localhost:1420', 'http://127.0.0.1:1420',
            'tauri://localhost', 'http://tauri.localhost',
        ],
        allow_methods=['*'],
        allow_headers=['*'],
    )

    def _config_default_quality() -> str:
        """读取 config.ini 的默认画质（video_record_quality）。

        新任务未选画质时写入该值，与录制进程保持同一规则：
        在 WebUI 改完配置后，新加任务立即按新默认画质生效，无需重启。
        """
        try:
            parser = configparser.RawConfigParser()
            parser.read(config_file, encoding='utf-8-sig')
            quality = parser.get('录制设置', 'video_record_quality').strip()
            if quality in QUALITIES:
                return quality
        except Exception:
            pass
        return DEFAULT_QUALITY

    # ------------------------------------------------------------------ 页面
    @app.get('/', response_class=HTMLResponse)
    def index():
        with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
            return f.read()

    # ------------------------------------------------------------- PWA 资源
    @app.get('/manifest.webmanifest')
    def manifest():
        """Web App Manifest（PWA 安装信息）。"""
        return FileResponse(
            os.path.join(STATIC_DIR, 'manifest.webmanifest'),
            media_type='application/manifest+json',
            headers={'Cache-Control': 'no-cache'},
        )

    @app.get('/sw.js')
    def service_worker():
        """Service Worker（离线缓存 / 断网回退）。no-cache 保证新版本尽快生效。"""
        return FileResponse(
            os.path.join(STATIC_DIR, 'sw.js'),
            media_type='text/javascript',
            headers={'Cache-Control': 'no-cache'},
        )

    @app.get('/icons/{name}')
    def icons(name: str):
        """PWA 图标。"""
        safe = os.path.basename(name)  # 拒绝目录穿越
        full = os.path.join(STATIC_DIR, 'icons', safe)
        if not os.path.isfile(full):
            raise HTTPException(404, '图标不存在')
        return FileResponse(
            full,
            media_type='image/png',
            headers={'Cache-Control': 'public, max-age=86400'},
        )

    @app.get('/api/platforms')
    def api_platforms():
        return {
            'platforms': [
                {'name': ad.name,
                 'hosts': list(ad.hosts or []) + list(ad.patterns or []),
                 'overseas': ad.overseas,
                 'url_template': ad.url_template,
                 'id_placeholder': ad.id_placeholder}
                for ad in registry.all()
            ],
            'qualities': list(QUALITIES),
        }

    # ------------------------------------------------------------------ 状态
    @app.get('/api/health')
    def api_health():
        """桌面端 sidecar 就绪探测。"""
        return {'ok': True, 'version': version}

    @app.get('/api/meta')
    def api_meta():
        """桌面端需要的路径信息（下载目录、配置目录）。"""
        return {
            'downloads_dir': downloads_path,
            'config_file': config_file,
            'url_config_file': url_config_file,
        }

    @app.get('/api/status')
    def api_status():
        entries, _ = store.load()
        disk = utils.check_disk_capacity(downloads_path)
        return {
            'running': True,
            'version': version,
            'recording_count': state.count_recording(),
            'task_count': len(entries),
            'disk_free_gb': round(disk, 2),
            'platform_count': len(registry.all()),
        }

    # ------------------------------------------------------------------ 任务
    @app.get('/api/tasks')
    def api_tasks():
        entries, unknown = TaskStore(
            url_config_file, default_quality=_config_default_quality()).load()
        run_map = {t['url']: t for t in state.get_tasks()}
        tasks = []
        for e in entries:
            st = run_map.get(e.url, {})
            tasks.append({
                'url': e.url,
                'quality': e.quality,
                'name': e.name,
                'commented': e.commented,
                'platform': st.get('platform', ''),
                'status': st.get('status', 'unknown'),
                'anchor': st.get('anchor', ''),
                'recording_seconds': st.get('recording_seconds', 0),
                'file': _resolve_video_path(downloads_path, st.get('file', '')),
                'message': st.get('message', ''),
                'last_check': st.get('last_check', 0),
            })
        return {'tasks': tasks, 'unknown': unknown}

    @app.post('/api/tasks')
    def api_add_task(body: AddTaskBody):
        ok = TaskStore(
            url_config_file, default_quality=_config_default_quality()).add(
                body.url, body.quality, body.name)
        if not ok:
            raise HTTPException(400, 'URL 无效或平台不支持')
        state.add_log(f'WebUI 新增任务: {body.url}', 'INFO')
        return {'ok': True}

    @app.post('/api/tasks/from-id')
    def api_add_task_by_id(body: AddTaskByIdBody):
        """选平台 + 输 ID，自动拼接完整直播间 URL 后添加。"""
        adapter = next((a for a in registry.all() if a.name == body.platform), None)
        if adapter is None:
            raise HTTPException(400, f'未知平台: {body.platform}')
        url = adapter.build_url(body.id)
        if not url:
            raise HTTPException(400, f'平台[{adapter.name}]不支持 ID 快捷添加，请粘贴完整网址')
        ok = TaskStore(
            url_config_file, default_quality=_config_default_quality()).add(
                url, body.quality, body.name)
        if not ok:
            raise HTTPException(400, 'URL 无效或平台不支持')
        state.add_log(f'WebUI 新增任务[{adapter.name}]: {url}', 'INFO')
        return {'ok': True, 'url': url}

    @app.delete('/api/tasks')
    def api_remove_task(url: str = Query(...)):
        ok = store.remove(url)
        if not ok:
            raise HTTPException(404, '未找到该任务')
        state.remove_task(url)
        state.add_log(f'WebUI 删除任务: {url}', 'INFO')
        return {'ok': True}

    @app.put('/api/tasks/comment')
    def api_comment_task(url: str = Query(...), commented: bool = Query(...)):
        ok = store.set_commented(url, commented)
        if not ok:
            raise HTTPException(404, '未找到该任务')
        state.update_task(
            url,
            status=state.STOPPED if commented else state.WAITING,
            recording_since=0,
            message='已暂停' if commented else '等待检测',
        )
        state.add_log(f'WebUI {"暂停" if commented else "恢复"}任务: {url}', 'INFO')
        return {'ok': True}

    # ------------------------------------------------------------------ 配置
    @app.get('/api/config')
    def api_config():
        if os.path.isfile(config_file):
            with open(config_file, encoding='utf-8-sig') as f:
                return PlainTextResponse(f.read())
        return PlainTextResponse('')

    @app.put('/api/config')
    def api_save_config(text: str = Body(..., embed=False)):
        import configparser
        try:
            parser = configparser.RawConfigParser()
            parser.read_string(text)
        except Exception as e:
            raise HTTPException(400, f'配置格式错误: {e}')
        # 原子写入：先写临时文件再 rename。录制进程与 WebUI 是两个独立进程，
        # 录制主循环每轮都会重新读这份文件；若中途读到半个文件会解析失败。
        tmp_file = config_file + '.tmp'
        with open(tmp_file, 'w', encoding='utf-8-sig') as f:
            f.write(text)
        os.replace(tmp_file, config_file)
        state.add_log('WebUI 已保存配置（新开任务自动生效，无需重启）', 'INFO')
        return {'ok': True}

    # ------------------------------------------------------------------ 日志
    @app.get('/api/logs')
    def api_logs(limit: int = 200):
        return {'logs': state.get_logs(max(1, min(limit, 1000)))}

    # ------------------------------------------------------------------ 视频
    @app.get('/api/videos')
    def api_videos():
        return {'files': _walk_videos(downloads_path)}

    @app.get('/api/videos/play/{path:path}')
    def api_play_video(path: str):
        """提供浏览器播放地址。

        Chrome/Firefox 不能直接播放录制默认使用的 TS、FLV、MKV。对这些
        格式通过 ffmpeg 即时封装为 fragmented MP4；MP4/音频文件则直接返回，
        因此不会再把文件误当成下载或打开空白页面。
        """
        full = _safe_path(downloads_path, path)
        if not full or not os.path.isfile(full):
            raise HTTPException(404, '录制文件不存在')

        ext = os.path.splitext(full)[1].lower()
        if ext in ('.mp4', '.mp3', '.m4a', '.webm', '.ogg'):
            media_type = mimetypes.guess_type(full)[0] or 'application/octet-stream'
            return FileResponse(full, media_type=media_type)

        if not shutil.which('ffmpeg'):
            raise HTTPException(503, '当前文件格式无法由浏览器直接播放，且系统中未找到 ffmpeg')
        try:
            stream = _iter_ffmpeg_mp4(full)
            return StreamingResponse(
                stream,
                media_type='video/mp4',
                headers={'Cache-Control': 'no-store'},
            )
        except OSError as exc:
            raise HTTPException(503, f'启动 ffmpeg 失败: {exc}') from exc

    os.makedirs(downloads_path, exist_ok=True)
    app.mount('/videos', StaticFiles(directory=downloads_path), name='videos')

    return app

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

import os
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import state, utils
from src.adapters import registry
from src.url_config import QUALITIES, TaskStore

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


def create_app(config_file: str, url_config_file: str, downloads_path: str,
               script_path: str = '', version: str = 'v4.0.7') -> FastAPI:
    store = TaskStore(url_config_file)
    app = FastAPI(title='DouyinLiveRecorder WebUI', version=version)

    # ------------------------------------------------------------------ 页面
    @app.get('/', response_class=HTMLResponse)
    def index():
        with open(os.path.join(STATIC_DIR, 'index.html'), encoding='utf-8') as f:
            return f.read()

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
        entries, unknown = store.load()
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
                'file': st.get('file', ''),
                'message': st.get('message', ''),
                'last_check': st.get('last_check', 0),
            })
        return {'tasks': tasks, 'unknown': unknown}

    @app.post('/api/tasks')
    def api_add_task(body: AddTaskBody):
        ok = store.add(body.url, body.quality, body.name)
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
        ok = store.add(url, body.quality, body.name)
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
        with open(config_file, 'w', encoding='utf-8-sig') as f:
            f.write(text)
        state.add_log('WebUI 已保存配置', 'INFO')
        return {'ok': True}

    # ------------------------------------------------------------------ 日志
    @app.get('/api/logs')
    def api_logs(limit: int = 200):
        return {'logs': state.get_logs(max(1, min(limit, 1000)))}

    # ------------------------------------------------------------------ 视频
    @app.get('/api/videos')
    def api_videos():
        return {'files': _walk_videos(downloads_path)}

    os.makedirs(downloads_path, exist_ok=True)
    app.mount('/videos', StaticFiles(directory=downloads_path), name='videos')

    return app

# -*- encoding: utf-8 -*-
"""
WebUI 启动入口
==============

- `python -m webui`            独立运行（默认 0.0.0.0:8000）
- `python -m webui --port 9000` 指定端口
- main.py 内置模式：`python main.py --web`（复用 create_app）
"""
from __future__ import annotations

import argparse
import os
import threading
import time

from webui.app import create_app

SCRIPT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 桌面端数据目录重定向（DLR_DATA_DIR 由 Tauri 壳注入），与 main.py 保持一致
DATA_DIR = os.environ.get('DLR_DATA_DIR') or SCRIPT_PATH
CONFIG_FILE = os.path.join(DATA_DIR, 'config', 'config.ini')
URL_CONFIG_FILE = os.path.join(DATA_DIR, 'config', 'URL_config.ini')
DOWNLOADS_PATH = os.path.join(DATA_DIR, 'downloads')


def build_app(version: str = 'v4.0.7'):
    return create_app(CONFIG_FILE, URL_CONFIG_FILE, DOWNLOADS_PATH, SCRIPT_PATH, version)


def start_in_background(port: int = 8000, host: str = '0.0.0.0', version: str = 'v4.0.7') -> threading.Thread:
    """在后台线程启动 uvicorn（供 main.py --web 使用）。

    port=0 时由系统自动分配空闲端口，就绪后向 stdout 打印一行
    ``DLR_WEBUI_READY:http://127.0.0.1:<port>``，供桌面端壳解析实际端口。
    """
    import uvicorn

    def run():
        import uvicorn.logging
        app = build_app(version)
        config = uvicorn.Config(app, host=host, port=port, log_level='warning')
        server = uvicorn.Server(config)

        def _serve():
            server.run()

        th = threading.Thread(target=_serve, daemon=True)
        th.start()
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        if server.started:
            try:
                actual_port = server.servers[0].sockets[0].getsockname()[1]
                print(f'DLR_WEBUI_READY:http://127.0.0.1:{actual_port}', flush=True)
            except Exception:
                pass
        th.join()

    t = threading.Thread(target=run, name='webui', daemon=True)
    t.start()
    return t


def main() -> None:
    parser = argparse.ArgumentParser(description='DouyinLiveRecorder WebUI')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    import uvicorn
    app = build_app()
    print(f'DouyinLiveRecorder WebUI 启动: http://{args.host}:{args.port}')
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()

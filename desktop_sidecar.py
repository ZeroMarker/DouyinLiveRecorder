# -*- encoding: utf-8 -*-
"""桌面端 sidecar 入口：以 headless 模式启动录制引擎 + 内置 WebUI。

供 Tauri 壳 spawn；也可直接 `python desktop_sidecar.py` 手动调试。

- sys.argv 注入 --web：main.py 模块级代码检测到后启动内置 WebUI
- DLR_NO_INPUT=1：URL 为空时不阻塞 stdin（任务由桌面端 WebUI 管理）
- DLR_NO_TUI=1：跳过 TUI 刷屏线程（清屏转义码会污染 stdout）
- WEBUI_PORT=0：系统分配空闲端口，就绪后向 stdout 打印
  ``DLR_WEBUI_READY:http://127.0.0.1:<port>`` 供壳解析
"""
import os
import sys

if '--web' not in sys.argv:
    sys.argv.append('--web')
os.environ.setdefault('DLR_NO_INPUT', '1')
os.environ.setdefault('DLR_NO_TUI', '1')
os.environ.setdefault('WEBUI_HOST', '127.0.0.1')
os.environ.setdefault('WEBUI_PORT', '0')  # 系统分配空闲端口，就绪行上报实际端口

import main  # noqa: E402,F401  模块级运行录制主循环

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包桌面端 sidecar（录制引擎 + 内置 WebUI）。

onefile 单文件产物：dist/recorder-sidecar
（Tauri externalBin 只支持单文件二进制；onefile 启动时自解压到临时目录）
构建：.venv/bin/pyinstaller --noconfirm --clean recorder-sidecar.spec
"""
import os

ROOT = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(ROOT, 'desktop_sidecar.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'i18n'), 'i18n'),                      # gettext 语言包
        (os.path.join(ROOT, 'webui', 'static'), 'webui/static'),   # 内置 WebUI 静态资源
    ],
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='recorder-sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

//! sidecar 生命周期管理：spawn 录制引擎（Python FastAPI），
//! 从输出流解析 DLR_WEBUI_READY 行拿到实际端口，退出时 kill。
//!
//! 两种模式：
//! - dev：仓库源码（venv python + main.py，cwd 为仓库根），tokio 直管
//! - release：Tauri externalBin（PyInstaller onefile），tauri-plugin-shell 管理，
//!   数据目录（config/downloads）重定向到系统用户数据目录

use std::process::Stdio;

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tokio::io::{AsyncBufReadExt, BufReader};

use crate::{BackendUrl, SidecarChild, SidecarProcess};

pub fn spawn_sidecar(app: &AppHandle) {
    let dev = tauri::is_dev();
    let repo_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();

    if dev {
        spawn_dev(app, &repo_root);
    } else {
        spawn_bundled(app);
    }
}

/// dev：venv python + 源码 main.py，tokio 直管（无权限 scope 约束）
fn spawn_dev(app: &AppHandle, repo_root: &std::path::Path) {
    let python = if repo_root.join(".venv/bin/python").exists() {
        repo_root.join(".venv/bin/python")
    } else {
        std::path::PathBuf::from("python3")
    };
    let mut cmd = tokio::process::Command::new(&python);
    cmd.current_dir(repo_root)
        .arg(repo_root.join("main.py"))
        .arg("--web")
        .env("DLR_NO_INPUT", "1")
        .env("DLR_NO_TUI", "1")
        .env("WEBUI_HOST", "127.0.0.1")
        .env("WEBUI_PORT", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[sidecar] 启动失败: {e}");
            return;
        }
    };
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    *app.state::<SidecarChild>().0.lock() = Some(SidecarProcess::Tokio(child));

    let app2 = app.clone();
    if let Some(stdout) = stdout {
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if let Some(rest) = line.strip_prefix("DLR_WEBUI_READY:") {
                    report_ready(&app2, rest.trim());
                    break;
                }
            }
        });
    }
    if let Some(stderr) = stderr {
        tauri::async_runtime::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                eprintln!("[sidecar] {line}");
            }
        });
    }
}

/// release：externalBin onefile sidecar，shell 插件管理
fn spawn_bundled(app: &AppHandle) {
    let mut cmd = match app.shell().sidecar("recorder-sidecar") {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[sidecar] 配置缺失: {e}");
            return;
        }
    };
    cmd = cmd
        .env("DLR_NO_INPUT", "1")
        .env("DLR_NO_TUI", "1")
        .env("WEBUI_HOST", "127.0.0.1")
        .env("WEBUI_PORT", "0");
    if let Ok(data_dir) = app.path().app_data_dir() {
        cmd = cmd.env("DLR_DATA_DIR", data_dir);
    }

    let (mut rx, child) = match cmd.spawn() {
        Ok(v) => v,
        Err(e) => {
            eprintln!("[sidecar] 启动失败: {e}");
            return;
        }
    };
    *app.state::<SidecarChild>().0.lock() = Some(SidecarProcess::Shell(child));

    let app2 = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let line = String::from_utf8_lossy(&line);
                    if let Some(rest) = line.strip_prefix("DLR_WEBUI_READY:") {
                        report_ready(&app2, rest.trim());
                        break;
                    }
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[sidecar] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    eprintln!("[sidecar] 退出: code={:?}", payload.code);
                    break;
                }
                _ => {}
            }
        }
    });
}

fn report_ready(app: &AppHandle, url: &str) {
    if url.is_empty() {
        return;
    }
    *app.state::<BackendUrl>().0.lock() = Some(url.to_string());
    let _ = app.emit("backend-ready", url.to_string());
    eprintln!("[sidecar] 就绪: {url}");
}

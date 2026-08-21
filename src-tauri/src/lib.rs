mod sidecar;

use parking_lot::Mutex;
use tauri::{Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_shell::process::CommandChild;

/// 存活 sidecar 子进程句柄，退出时 kill，避免残留 Python 进程。
pub enum SidecarProcess {
    /// dev 模式：tokio 直管
    Tokio(tokio::process::Child),
    /// release 模式：shell 插件管理
    Shell(CommandChild),
}

impl SidecarProcess {
    fn kill(self) {
        match self {
            SidecarProcess::Tokio(mut c) => {
                let _ = c.start_kill();
            }
            SidecarProcess::Shell(c) => {
                let _ = c.kill();
            }
        }
    }
}

pub struct SidecarChild(pub Mutex<Option<SidecarProcess>>);
/// sidecar FastAPI 基地址（如 http://127.0.0.1:43469），就绪后写入。
pub struct BackendUrl(pub Mutex<Option<String>>);

pub fn backend_base(app: &tauri::AppHandle) -> Option<String> {
    app.state::<BackendUrl>().0.lock().clone()
}

#[tauri::command]
fn backend_url(state: tauri::State<BackendUrl>) -> String {
    state.0.lock().clone().unwrap_or_default()
}

#[tauri::command]
async fn save_file(app: tauri::AppHandle, path: String, dest: String) -> Result<(), String> {
    // 从后端拿下载目录绝对路径，校验 path 无目录穿越后复制到用户选择位置
    let base = backend_base(&app).ok_or("后端未就绪")?;
    let meta: serde_json::Value = reqwest::get(format!("{base}/api/meta"))
        .await
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    let downloads = meta["downloads_dir"]
        .as_str()
        .ok_or("后端未返回 downloads_dir")?;
    let root = std::path::Path::new(downloads);
    let src = root.join(&path);
    let src = src.canonicalize().map_err(|e| format!("文件不存在: {e}"))?;
    if !src.starts_with(root.canonicalize().unwrap_or_else(|_| root.to_path_buf())) {
        return Err("非法路径".into());
    }
    tokio::fs::copy(&src, &dest).await.map_err(|e| e.to_string())?;
    Ok(())
}

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 二次启动：聚焦已有窗口
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .manage(SidecarChild(Mutex::new(None)))
        .manage(BackendUrl(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![backend_url, save_file])
        .on_window_event(|window, event| {
            // 关闭按钮 → 最小化到托盘（值守工具惯例）；托盘菜单"退出"真正结束
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .setup(|app| {
            sidecar::spawn_sidecar(app.handle());
            build_tray(app.handle())?;
            Ok(())
        });

    builder
        .build(tauri::generate_context!())
        .expect("构建 Tauri 应用失败")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                // 结束 sidecar，避免 Python 进程残留
                if let Some(child) = app_handle.state::<SidecarChild>().0.lock().take() {
                    child.kill();
                }
            }
        });
}

fn build_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let show = tauri::menu::MenuItem::with_id(app, "show", "显示 / 隐藏", true, None::<&str>)?;
    let quit = tauri::menu::MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = tauri::menu::Menu::with_items(app, &[&show, &quit])?;

    let toggle = |app: &tauri::AppHandle| {
        if let Some(w) = app.get_webview_window("main") {
            if w.is_visible().unwrap_or(false) {
                let _ = w.hide();
            } else {
                let _ = w.show();
                let _ = w.set_focus();
            }
        }
    };

    tauri::tray::TrayIconBuilder::new()
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                    let _ = w.set_focus();
                }
            }
            "quit" => {
                let _ = app.emit("app-quit", ());
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(move |tray, event| {
            if let tauri::tray::TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: tauri::tray::MouseButtonState::Up,
                ..
            } = event
            {
                toggle(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

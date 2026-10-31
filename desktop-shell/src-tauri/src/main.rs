//! CC Convert desktop shell.
//!
//! Responsibilities (and nothing else — business logic lives in the Manager):
//! 1. Resolve the Manager port from the profile store config.
//! 2. Ensure a Manager is running (spawn `ccconvert start` when possible).
//! 3. Point the window at the Manager's /desktop UI once it is up.
//! 4. Tray menu + system notifications for Manager/instance transitions.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::Read;
use std::path::PathBuf;
use std::process::Command;
use std::time::{Duration, Instant};

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WindowEvent,
};
use tauri_plugin_notification::NotificationExt;

const HTTP_TIMEOUT: Duration = Duration::from_secs(3);
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct ManagerEndpoint {
    port: u16,
}

impl ManagerEndpoint {
    fn base(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    fn reachable(&self) -> bool {
        let url = format!("{}/api/manager", self.base());
        match request_json(&url) {
            Some(body) => body.contains("pid"),
            None => false,
        }
    }
}

fn request_json(url: &str) -> Option<String> {
    let agent = ureq::AgentBuilder::new().timeout(HTTP_TIMEOUT).build();
    let mut body = String::new();
    match agent.get(url).call() {
        Ok(resp) => match resp.into_reader().take(1 << 20).read_to_string(&mut body) {
            Ok(_) => Some(body),
            Err(_) => None,
        },
        Err(_) => None,
    }
}

/// Manager port from `config.toml` ([manager] port), honouring
/// CCCONVERT_CONFIG_DIR. Falls back to the conventional 18406.
fn resolve_manager_port() -> u16 {
    let dir = std::env::var("CCCONVERT_CONFIG_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            let home = std::env::var("USERPROFILE")
                .or_else(|_| std::env::var("HOME"))
                .unwrap_or_default();
            PathBuf::from(home).join(".ccconvert")
        });
    if let Ok(text) = std::fs::read_to_string(dir.join("config.toml")) {
        if let Ok(value) = text.parse::<toml::Value>() {
            let port = value
                .get("manager")
                .and_then(|m| m.get("port"))
                .and_then(|p| p.as_integer())
                .or_else(|| value.get("port").and_then(|p| p.as_integer()));
            if let Some(port) = port.filter(|p| (1..=65535).contains(p)) {
                return port as u16;
            }
        }
    }
    18406
}

fn quiet(mut cmd: Command) -> Command {
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let _ = &mut cmd;
    cmd
}

/// Resolve the `ccconvert` CLI: a bundled exe next to ours first, then PATH,
/// then `uv run` (source checkout). Returns the configured Command.
fn manager_cli(args: &[&str]) -> Command {
    let bundled = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|d| d.join("ccconvert.exe")))
        .filter(|p| p.exists());
    if let Some(exe) = bundled {
        let mut cmd = Command::new(exe);
        cmd.args(args);
        return quiet(cmd);
    }
    // `ccconvert` on PATH?
    if Command::new("ccconvert")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        let mut cmd = Command::new("ccconvert");
        cmd.args(args);
        return quiet(cmd);
    }
    let mut cmd = Command::new("uv");
    cmd.args(["run", "ccconvert"]);
    cmd.args(args);
    quiet(cmd)
}

fn spawn_manager() -> Result<(), String> {
    manager_cli(&["start"])
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("启动 Manager 失败: {e}"))
}

fn wait_for_manager(endpoint: &ManagerEndpoint, deadline: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < deadline {
        if endpoint.reachable() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(800));
    }
    false
}

/// Background watcher: notify on Manager up/down transitions and on any
/// instance flipping into `error`. Polls only; never mutates state.
fn spawn_watcher(app: tauri::AppHandle, endpoint: ManagerEndpoint) {
    std::thread::spawn(move || {
        let mut manager_up = true;
        let mut last_status: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        loop {
            std::thread::sleep(Duration::from_secs(30));
            let up = endpoint.reachable();
            if up != manager_up {
                manager_up = up;
                let msg = if up {
                    format!("Manager 已恢复（端口 {}）", endpoint.port)
                } else {
                    "Manager 已离线".to_string()
                };
                let _ = app
                    .notification()
                    .builder()
                    .title("CC Convert")
                    .body(&msg)
                    .show();
            }
            if !up {
                continue;
            }
            if let Some(body) = request_json(&format!("{}/api/instances", endpoint.base())) {
                if let Ok(list) = serde_json::from_str::<serde_json::Value>(&body) {
                    if let Some(items) = list.as_array() {
                        for item in items {
                            let id = item
                                .get("id")
                                .and_then(|v| v.as_str())
                                .unwrap_or_default()
                                .to_string();
                            let name = item
                                .get("name")
                                .and_then(|v| v.as_str())
                                .unwrap_or(&id)
                                .to_string();
                            let status = item
                                .get("status")
                                .and_then(|v| v.as_str())
                                .unwrap_or("unknown")
                                .to_string();
                            let flipped_to_error = last_status
                                .get(&id)
                                .map(|p| p != &status)
                                .unwrap_or(false)
                                && status == "error";
                            if flipped_to_error {
                                let _ = app
                                    .notification()
                                    .builder()
                                    .title("CC Convert 实例异常")
                                    .body(format!("实例 {name} 进入异常状态"))
                                    .show();
                            }
                            last_status.insert(id, status);
                        }
                    }
                }
            }
        }
    });
}

fn ensure_manager_and_navigate(app: &tauri::AppHandle, endpoint: ManagerEndpoint) {
    let app = app.clone();
    std::thread::spawn(move || {
        if !endpoint.reachable() {
            if spawn_manager().is_err() || !wait_for_manager(&endpoint, Duration::from_secs(60)) {
                if let Some(win) = app.get_webview_window("main") {
                    let _ = win.show();
                    let _ = win.set_focus();
                }
                let _ = app
                    .notification()
                    .builder()
                    .title("CC Convert")
                    .body("Manager 启动失败，请在窗口中查看说明")
                    .show();
                return;
            }
        }
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.show();
            let _ = win.set_focus();
            let _ = win.eval(&format!(
                "window.location.replace('{}/desktop')",
                endpoint.base()
            ));
        }
        let _ = app
            .notification()
            .builder()
            .title("CC Convert")
            .body("Manager 已就绪")
            .show();
        spawn_watcher(app, endpoint);
    });
}

fn main() {
    let endpoint = ManagerEndpoint { port: resolve_manager_port() };

    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .setup(move |app| {
            let handle = app.handle();

            let show = MenuItem::with_id(app, "show", "显示面板", true, None::<&str>)?;
            let restart = MenuItem::with_id(app, "restart", "重启 Manager", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop", "停止 Manager", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出（保留 Manager）", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &restart, &stop, &quit])?;

            TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("CC Convert")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| {
                    match event.id.as_ref() {
                        "show" => {
                            if let Some(win) = app.get_webview_window("main") {
                                let _ = win.show();
                                let _ = win.set_focus();
                            }
                        }
                        "restart" => {
                            let _ = manager_cli(&["restart"]).output();
                            let endpoint =
                                ManagerEndpoint { port: resolve_manager_port() };
                            ensure_manager_and_navigate(app, endpoint);
                        }
                        "stop" => {
                            let _ = manager_cli(&["stop"]).output();
                        }
                        "quit" => app.exit(0),
                        _ => {}
                    }
                })
                .build(app)?;

            ensure_manager_and_navigate(handle, endpoint);
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window keeps the Manager (and the tray) running;
            // quitting happens from the tray menu.
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ccconvert-desktop");
}

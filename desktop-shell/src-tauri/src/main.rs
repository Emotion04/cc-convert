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

/// Manager location, resolved on every access.
///
/// The port must not be cached: on a first run the shell starts before the
/// manager has written its config, so the value read at startup belongs to a
/// different (or default) installation. Re-reading after each poll is what
/// lets a freshly created portable config take effect.
struct ManagerEndpoint;

impl ManagerEndpoint {
    fn port(&self) -> u16 {
        resolve_manager_port()
    }

    fn base(&self) -> String {
        format!("http://127.0.0.1:{}", self.port())
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

/// Config directories to read the manager port from, in the same priority
/// order the Python side uses: explicit override → portable `data/config`
/// (green build) → per-user `~/.ccconvert`.
fn config_dirs() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(override_dir) = std::env::var("CCCONVERT_CONFIG_DIR") {
        dirs.push(PathBuf::from(override_dir));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let portable = dir.join("data").join("config");
            if portable.is_dir() {
                dirs.push(portable);
            }
        }
    }
    let home = std::env::var("USERPROFILE")
        .or_else(|_| std::env::var("HOME"))
        .unwrap_or_default();
    if !home.is_empty() {
        dirs.push(PathBuf::from(home).join(".ccconvert"));
    }
    dirs
}

/// Manager port from `config.toml` ([manager] port). Falls back to the
/// conventional 18406.
fn resolve_manager_port() -> u16 {
    for dir in config_dirs() {
        let Ok(text) = std::fs::read_to_string(dir.join("config.toml")) else {
            continue;
        };
        let Ok(value) = text.parse::<toml::Value>() else {
            continue;
        };
        let port = value
            .get("manager")
            .and_then(|m| m.get("port"))
            .and_then(|p| p.as_integer())
            .or_else(|| value.get("port").and_then(|p| p.as_integer()));
        if let Some(port) = port.filter(|p| (1..=65535).contains(p)) {
            return port as u16;
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

/// Directories where the manager CLI may live, most authoritative first.
///
/// 1. Beside our own executable — this covers both the portable zip (core
///    shipped flat next to the shell) and the NSIS bundle (Tauri places
///    `externalBin` there).
/// 2. `%LOCALAPPDATA%\ccconvert\bin` — an opt-in install location.
fn manager_candidates(app: Option<&tauri::AppHandle>) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Some(app) = app {
        if let Ok(dir) = app.path().resource_dir() {
            out.push(dir.join("core").join("ccconvert.exe"));
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            out.push(dir.join("ccconvert.exe"));
            // Tauri keeps the target-triple suffix in some layouts.
            out.push(dir.join(format!("ccconvert-{}-pc-windows-msvc.exe", std::env::consts::ARCH)));
        }
    }
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        out.push(PathBuf::from(local).join("ccconvert").join("bin").join("ccconvert.exe"));
    }
    out
}

/// Resolve the `ccconvert` CLI to run.
///
/// Returns the command plus a human-readable description of what was chosen,
/// so a failure can tell the user exactly what was tried instead of a bare
/// "startup failed".
fn manager_cli(app: Option<&tauri::AppHandle>, args: &[&str]) -> (Command, String) {
    for candidate in manager_candidates(app) {
        if candidate.is_file() {
            let mut cmd = Command::new(&candidate);
            cmd.args(args);
            return (quiet(cmd), candidate.display().to_string());
        }
    }
    // A `ccconvert` on PATH (source install / user-managed).
    if Command::new("ccconvert")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
    {
        let mut cmd = Command::new("ccconvert");
        cmd.args(args);
        return (quiet(cmd), "ccconvert (PATH)".to_string());
    }
    // Last resort for developers: run from the checkout via uv.
    let mut cmd = Command::new("uv");
    cmd.args(["run", "ccconvert"]);
    cmd.args(args);
    (quiet(cmd), "uv run ccconvert".to_string())
}

/// Report what the launcher tried, for display in the offline page.
fn launcher_diagnosis(app: Option<&tauri::AppHandle>) -> String {
    let mut lines = vec!["未找到可用的 ccconvert 核心：".to_string()];
    for candidate in manager_candidates(app) {
        let mark = if candidate.is_file() { "存在" } else { "缺失" };
        lines.push(format!("  [{mark}] {}", candidate.display()));
    }
    lines.push("  [检查] PATH 中的 ccconvert".to_string());
    lines.push("  [检查] uv run ccconvert".to_string());
    lines.push(String::new());
    lines.push(
        "绿色版：请确认 ccconvert.exe 与 ccconvert-desktop.exe 在同一目录。".to_string(),
    );
    lines.join("\n")
}

fn spawn_manager(app: Option<&tauri::AppHandle>) -> Result<(), String> {
    let (mut cmd, source) = manager_cli(app, &["start"]);
    match cmd.spawn() {
        Ok(_) => Ok(()),
        Err(e) => Err(format!("启动 {source} 失败: {e}\n\n{}", launcher_diagnosis(app))),
    }
}

/// Tail of the manager log, for showing a real reason on the offline page.
fn manager_log_tail(lines: usize) -> String {
    let mut candidates = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("data").join("runtime").join("manager.log"));
        }
    }
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        candidates.push(PathBuf::from(local).join("ccconvert").join("manager.log"));
    }
    for path in candidates {
        if let Ok(text) = std::fs::read_to_string(&path) {
            let tail: Vec<&str> = text.lines().rev().take(lines).collect();
            let mut out: Vec<&str> = tail.into_iter().collect();
            out.reverse();
            return format!("{}\n\n— {}", out.join("\n"), path.display());
        }
    }
    String::new()
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
                    format!("Manager 已恢复（端口 {}）", endpoint.port())
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

/// Render a real failure reason into the offline page instead of leaving the
/// generic "not running" text. The details travel through `document.title`
/// only as a fallback; the visible block is appended to the page body.
fn show_failure(app: &tauri::AppHandle, reason: &str, log_tail: &str) {
    let Some(win) = app.get_webview_window("main") else {
        return;
    };
    let _ = win.show();
    let _ = win.set_focus();
    let payload = serde_json::json!({ "reason": reason, "log": log_tail });
    let _ = win.eval(&format!(
        r#"(function(){{
  var d = {payload};
  var box = document.getElementById('diagnosis');
  if (!box) {{
    box = document.createElement('pre');
    box.id = 'diagnosis';
    box.style.cssText = 'text-align:left;white-space:pre-wrap;font:12px/1.6 Consolas,monospace;'
      + 'background:#1f1f1f;border:1px solid #333;border-radius:10px;padding:14px;'
      + 'max-height:45vh;overflow:auto;color:#e7e5e4;margin-top:18px';
    (document.querySelector('.box') || document.body).appendChild(box);
  }}
  box.textContent = d.reason + (d.log ? '\n\n--- Manager 日志尾部 ---\n' + d.log : '');
}})();"#,
        payload = payload
    ));
}

fn ensure_manager_and_navigate(app: &tauri::AppHandle, endpoint: ManagerEndpoint) {
    let app = app.clone();
    std::thread::spawn(move || {
        let mut failure: Option<String> = None;
        if !endpoint.reachable() {
            match spawn_manager(Some(&app)) {
                Err(e) => failure = Some(e),
                Ok(()) => {
                    if !wait_for_manager(&endpoint, Duration::from_secs(60)) {
                        failure = Some(format!(
                            "已启动核心进程，但 60 秒内 http://127.0.0.1:{}/api/manager 无响应。\n\
                             可能原因：端口被占用、配置有误，或核心进程启动即退出。",
                            endpoint.port()
                        ));
                    }
                }
            }
        }
        if let Some(reason) = failure {
            show_failure(&app, &reason, &manager_log_tail(25));
            let _ = app
                .notification()
                .builder()
                .title("CC Convert")
                .body("Manager 启动失败：窗口内已显示具体原因")
                .show();
            return;
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
    let endpoint = ManagerEndpoint;

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
                            let _ = manager_cli(Some(app), &["restart"]).0.output();
                            let endpoint = ManagerEndpoint;
                            ensure_manager_and_navigate(app, endpoint);
                        }
                        "stop" => {
                            let _ = manager_cli(Some(app), &["stop"]).0.output();
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

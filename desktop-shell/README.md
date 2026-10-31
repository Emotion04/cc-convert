# Desktop shell (Tauri)

> Windows/macOS/Linux 桌面壳：窗口 + 托盘 + Manager 进程生命周期 + 系统通知。
> UI 本体（universal-ui SPA）由 Manager 在 `/desktop` 提供——壳只负责「装进一个窗口」。

## 架构边界（改代码前必读）

```
ccconvert-desktop.exe（本目录产物）
  ├─ Rust 壳：窗口、托盘、拉起/停止 Manager、离线兜底页、系统通知
  └─ WebView → http://127.0.0.1:<port>/desktop   ← 功能全部在这里（Python Manager）
```

- **壳里禁止出现业务逻辑**（实例管理、配置、用量都是 Manager 的 API 的事）。
- 前端源码在 `src/cc_convert/web/static/desktop/`（由 Manager 同源服务，CSRF 沿用
  现有 meta 机制，无 CORS 面）；壳的 `frontend/` 只是离线兜底页。
- Manager 端口从 `~/.ccconvert/config.toml`（或 `CCCONVERT_CONFIG_DIR`）解析。

## 开发

```bash
# 终端 1：起 Manager（前端随 Manager 服务）
uv run ccconvert restart
# 浏览器直接调 UI（无需 Tauri）：
#   http://127.0.0.1:18406/desktop

# 终端 2：起壳（首次会编译 Rust，较慢）
cd desktop-shell
npm install
npx tauri dev
```

## 打包

```bash
cd desktop-shell
npx tauri build        # 产物在 src-tauri/target/release/ccconvert-desktop.exe
```

分发 = 该 exe + 现有 `ccconvert`（PyInstaller onedir 或源码 uv 环境）。壳启动时会
按以下顺序找 Manager 命令：

1. 同目录的 `ccconvert.exe`（打包分发形态）；
2. `uv run ccconvert start`（源码开发形态）。

两者都没有时窗口停留在离线兜底页并发系统通知。

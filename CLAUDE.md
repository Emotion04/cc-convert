# cc-convert (CC Convert)

Anthropic ↔ OpenAI 协议转换代理，支持多实例管理、Web Dashboard、端口自动分配、上游格式自动探测。

## 架构

```
Claude Code ──→ CC Convert Worker (本地端口) ──→ 上游 API (OpenAI/Responses)
                    ↑
            CC Convert Manager (Dashboard, 生命周期管理)
```

- **Manager**：Web Dashboard + REST API，管理实例的增删改查和启停
- **Worker**：每个实例一个独立进程，监听各自端口，做协议转换
- **Supervisor**：Manager 内的子进程管理器，负责 Worker 的启停和健康检查

## 项目结构

```
src/cc_convert/
├── main.py              # FastAPI 应用入口，create_proxy_app() / create_manager_app()
├── config.py            # Settings 模型，从 TOML 加载，resolve_model()
├── profile_store.py     # 多实例配置存储，v1→v2 迁移，端口分配，format 探测
├── runtime_paths.py     # 跨平台路径（配置、运行时、日志）
├── runtime_state.py     # 每个 Worker 的请求计数和状态
├── supervisor.py        # Worker 子进程生命周期管理
├── worker_runner.py     # Worker 进程入口（读 stdin JSON 快照）
├── daemon.py            # Manager 守护进程管理（start/stop/restart）
├── daemon_runner.py     # Manager 进程入口（加载 store，启动 supervisor）
├── router.py            # API 路由：/v1/messages, /v1/chat/completions, /v1/responses
├── converter.py         # Anthropic ↔ OpenAI 格式转换，Claude Code 标记清理
├── protocol.py          # Pydantic 数据模型
├── responses_converter.py  # Responses API ↔ Chat Completions 转换
├── streaming/handler.py    # SSE 流式响应处理
├── tools/               # 服务端工具（web_search 等）
├── server_tools/        # 服务端工具注册和实现
├── web/
│   ├── routes.py        # Dashboard API（实例 CRUD、端口管理、格式探测）
│   └── templates/       # Jinja2 + Alpine.js 前端页面
└── utils/               # 工具函数
```

## 关键入口

```bash
ccconvert start      # 启动 Manager 后台进程
ccconvert stop -f    # 强制停止
ccconvert restart    # 重启
ccconvert status     # 查看状态
ccconvert open       # 浏览器打开 Dashboard
```

## 测试

```bash
cd <cc-convert 仓库根目录>
python -m pytest tests/ -q --tb=short
```

核心测试文件：`test_router.py`, `test_profile_store.py`, `test_manager_routes.py`, `test_port_health.py`, `test_daemon.py`, `test_main.py`

## 关键约束

- **Windows 兼容**：路径用 `runtime_paths` 而非硬编码，`SIGKILL` 不存在，用 `taskkill /F` 代替
- **密钥安全**：API key 通过 stdin JSON 传给 Worker，不出现于命令行/环境变量/日志
- **端口分配**：`_find_free_port` 用 `bind()` 探测，不用 `SO_REUSEADDR`（Windows 会误判）
- **目标模型（target_model）**：空 = 透传 Claude Code 发来的模型名；非空 = 覆盖
- **上游格式（upstream_format）**：`chat_completions` | `responses` | `auto`（默认）
- **Claude Code 内部 XML 标记**：`_normalize_claude_code_markup()` 转为 `[标注: 内容]` 纯文本
- **Worker 崩溃恢复**：`start_instance` 在端口被占时自动 evict 残留进程

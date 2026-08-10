# cc-convert (CC Convert)

Anthropic ↔ OpenAI 双向协议转换代理，让 Claude Code / Anthropic SDK 运行在任意 OpenAI 兼容后端（vLLM、SGLang、云 API）上。支持多实例管理、Web Dashboard、端口自动分配、上游格式自动探测。

## 架构

```
Claude Code ──→ CC Convert Worker (本地端口) ──→ 上游 API (OpenAI/Responses)
                    ↑
            CC Convert Manager (Dashboard, 生命周期管理)
```

- **Manager**：Web Dashboard + REST API，管理实例的增删改查和启停
- **Worker**：每个实例一个独立进程，监听各自端口，做协议转换
- **Supervisor**：Manager 内的子进程管理器，负责 Worker 的启停和健康检查

## 核心逻辑

### 协议转换（双向）

- **Anthropic → OpenAI**：`router.py` 接收 `/v1/messages`（Anthropic 格式），`converter.py` 转为 OpenAI chat.completions 请求发给上游，响应再转回 Anthropic 格式
- **Responses → Chat Completions**：`responses_converter.py` 让只实现 chat.completions 的后端（vLLM、SGLang）也能被 OpenAI Responses SDK 客户端调用（`/v1/responses`）
- **原生透传**：`/v1/chat/completions` 原样转发，零转换开销，保留所有上游字段
- 流式 SSE、工具调用（`tool_use` ↔ function call）、视觉输入、交错 thinking 推理块均支持

### 模型解析（`resolve_model()` 三级）

1. 精确匹配 `model_mapping` 规则
2. `fnmatch` 通配符匹配（如 `*opus*`）
3. 回退 `default_model`（＝实例的 `target_model`；为空则透传 Claude Code 发来的模型名）

### 上游格式探测

- `upstream_format`：`chat_completions` | `responses` | `auto`（默认）
- `auto` 时启动探测：先试 chat/completions，再试 responses，兜底 chat_completions

### 多实例生命周期

- `profile_store.py`：实例配置存储（schema v2，v1 自动迁移），端口自动分配（`_find_free_port` 从 18401 起，`bind()` 探测，不用 `SO_REUSEADDR`）
- `supervisor.py`：Worker 子进程启停 + 健康检查（轮询 `/_ccconvert/status`，Bearer token 鉴权），端口被占自动 evict 残留进程
- **密钥安全**：API key 通过 stdin JSON 传给 Worker（`worker_runner.py`），不出现于命令行/环境变量/日志
- **崩溃恢复**：`start_instance` 在端口被占时自动 evict 残留进程后重启

### 其他特性

- **服务端 Web Search**：内置 Tavily / 通晓 引擎，任何模型获得联网能力（Anthropic 与 Responses 端点均支持）
- **Claude Code 标记清理**：`_normalize_claude_code_markup()` 将内部 XML 标记转为 `[标注: 内容]` 纯文本
- **模型注入**：实例可选配置 `client_model` / `client_sonnet_model` / `client_opus_model` / `client_haiku_model`，仅用于 Dashboard"复制配置"输出 `ANTHROPIC_MODEL` 等环境变量，不传给 Worker

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

## 配置

- 配置文件：`~/.ccconvert/config.toml`（环境变量 `CCCONVERT_CONFIG_DIR` 可覆盖）
- 首次运行自动创建，编辑添加 API Key

## 测试

```bash
cd <cc-convert 仓库根目录>
python -m pytest tests/ -q --tb=short
```

核心测试文件：`test_router.py`, `test_profile_store.py`, `test_manager_routes.py`, `test_port_health.py`, `test_daemon.py`, `test_main.py`

## 关键约束

- **Windows 兼容**：路径用 `runtime_paths` 而非硬编码，`SIGKILL` 不存在，用 `taskkill /F` 代替
- **目标模型（target_model）**：空 = 透传；非空 = 覆盖上游收到的模型名

# TODO — 现状记录

> 更新时间：2026-10-24
> 分支：`main`（`803a63b`），工作区有未提交改动（`start.ps1` 编码修复）

## 已完成

- [x] `router.py` 导入顺序修复（`_upstream_url` 早于自身 import 导致 `NameError`）
- [x] 测试沙箱隔离（`tests/conftest.py` 模块级重定向配置/运行时目录 + 真实配置绊线）
- [x] 导入冒烟测试（`tests/test_smoke_imports.py`）
- [x] dev 依赖两份声明合并 + 一致性守卫（`tests/test_dev_deps.py`）
- [x] `CCCONVERT_CONFIG_DIR` / `CCCONVERT_RUNTIME_DIR` 覆盖开关修复
- [x] `setup_logging` 句柄泄漏与 handler 累积修复
- [x] 日志目录并入 `runtime_paths`，纳入沙箱
- [x] 管理器日志统一为单文件（`ccconvert logs` 能看到应用错误）
- [x] 端口探测漏判通配地址（Windows）、进程存活误判（`GetExitCodeProcess`）
- [x] Manager 向上找端口时排除实例端口
- [x] `stop_daemon` 强杀后复查；按端口杀进程前校验归属
- [x] `wmic` → `tasklist`
- [x] `start.bat` / `start.ps1` 改用 `uv run` 并切换到脚本目录
- [x] CI 双矩阵（ubuntu + windows）+ 文档《使用规范》
- [x] ubuntu CI 两个平台相关用例修复（SIGKILL 时序、Windows 专属 bind 行为）
- [x] `start.ps1` 加 UTF-8 BOM（PS 5.1 无 BOM 时按 GBK 解码导致中文乱码）

## 待办

### P0 — 影响日常使用

- [x] **流式请求的 httpx client 从不关闭**（`router.py`）
  - 改为 `_closing_stream`：仅当 client 由本次请求创建时才 `aclose()`
- [x] **每个请求都新建 httpx client**（无连接复用 / keep-alive）
  - Worker 在 lifespan 中创建一个共享 `AsyncClient`，`app.state.http_client` 复用
  - 测试：`tests/test_http_client_reuse.py`
- [x] **`_HTTPX_TIMEOUT` 硬编码**（`router.py`）
  - 改为 `build_upstream_timeout(settings)`，读 `settings.request_timeout`
- [x] **流式连接失败时的错误信息为空**
  - `httpx.ConnectError` 的 `str()` 为空，日志只有 `Stream error: `
  - 现在输出异常类型 + 主机 + 排查提示，客户端也能看到具体原因

- [x] **流式响应丢弃首个 chunk 的内容**
  - `_stream_response` 把第一个 chunk 整体当作 `message_start` 处理并 `continue`，
    若上游在同一 chunk 里带了首个 token，该 token 会被丢弃
  - 已改为：发出 `message_start` 后仅在无 `choices` 时跳过，否则继续处理
- [x] **历史推理回传策略（五种模式 + 厂商规则）**
  - 新增 `thinking_history` 实例配置：`upstream` / `rules` / `turn` / `keep` / `drop`
  - `turn`（默认，仅最后一次 assistant）兼容需要工具轮回传的上游，同时避免重发全部历史推理
  - `upstream` 在 `rules` 基础上，会从与推理相关的 400 中学习并调整后续请求
  - 面板的「?」弹窗展示 7 家厂商的官方做法与来源
  - 自动纠正结果按 `(base_url, target_model, upstream_format)` 指纹持久化到
    `instances/<id>/thinking_learning.json`；改动 URL/模型/协议后自动失效
  - 新增 `thinking_history_auto_correct`（默认开）与 `thinking_history_force_manual`
  - 新增「探测」按钮：最多 2 次 `max_tokens=1` 请求，自动给出建议模式
  - 纠正只写一次 WARNING 日志 + 编辑页提示，不会每条回复都打扰
  - 已知限制：上游**没有**标准化接口可查询该策略，"遵循上游" 实际是内置规则 + 错误学习
- [ ] **content 中的字面 `<thinking>` 标签（暂不修）**
  - 上游若把推理写成 `<thinking>...</thinking>` 放在 `content` 里（而非
    `reasoning_content` 字段），cc-convert 会当作普通文本透传，标签可见
  - 不影响功能，也不额外产生 token；仅观感问题
  - 不修的原因：状态机解析可能误伤真正讨论 XML 或包含该字面量的内容
  - 若要修，应做成默认关闭的开关

### P0 — Clash / 网络

- [ ] **Clash 核心处于半死状态**（2026-10-24 观测）
  - TUN 网卡 `Mihomo` 为 Up、DNS 劫持生效（`wawapii.com` → `198.18.0.22`）
  - 7897 有 TCP 监听，但经代理请求外网仍在 TLS 握手阶段失败
  - 控制管道（named pipe）连接超时，无法查询节点状态
  - 国内站点（baidu / volces）正常，外网站点（wawapii / api.openai.com）TLS 握手不完成
  - **待办**：Clash Verge 重启内核 → 更换节点/更新订阅 → 验证
- [ ] **IPv6 不可用但被启用**
  - 本机有全局 IPv6 地址，但外网 IPv6 不通（`http=000`）
  - 建议在 Clash Verge 系统设置中关闭 IPv6

### P1 — 发布链路

- [ ] **Docker Hub secrets 未配置**
  - `docker.yml` 需要 `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN`
  - 现状：push 到 main 时 `docker/login-action` 报 "Username and password required"，两个 job 红
  - 处理方式已定：配置 secret，继续自动推送
- [ ] **Docker 镜像本身未验证**
  - `Dockerfile` 无 `CMD`；`entrypoint.sh` 默认执行裸 `ccconvert` 会打印帮助后退出
  - 端口约定 8080 与实际默认 18400 不一致
  - 卷挂载路径与 `runtime_paths` 不匹配
- [ ] **workflow 推送的镜像名与 compose 不一致**
  - workflow 推 `${DOCKERHUB_USERNAME}/cc-convert`
  - `docker-compose.yml` 引用 `cc-convert/cc-convert`
  - 需统一（建组织账号或改 compose）

### P1 — 打包

- [x] **onedir 绿色版 exe**（`ccconvert.spec` + `build_exe.bat`）
  - frozen 模式下 daemon/worker 通过 `__daemon` / `__worker` 重新进入
  - 模板经 `sys._MEIPASS` 定位；已实测 Manager + Worker 均为 `ccconvert.exe`
  - 与源码版共享配置，启动时会互相识别并提示
- [ ] **`stop -f` 会留下孤儿 Worker**
  - 强杀 Manager 时来不及执行 `shutdown_all()`，Worker 继续占着端口
  - 下次 `start` 时由 supervisor 的端口驱逐逻辑清理，但停止后短时间内会残留
  - 彻底修法：Windows Job Object（KILL_ON_JOB_CLOSE）或在下次启动时主动清理

### P2 — 体验

- [ ] CI 首次全绿尚未确认（本地无法访问 GitHub API，需在 Actions 页面查看）
- [ ] `supervisor.start_instance` 按端口驱逐进程时未校验归属（与 `stop_daemon` 同类问题）
- [ ] `start.ps1` / `start.bat` 行为不一致（bat 先 restart，ps1 只 start）

## 已知问题 / 观察记录

- **Claude Code "Waiting for API response · will retry in 4m 27s · check your network"**
  - 实例 `Waw`（18402 → `https://wawapii.com`）日志中有 3 次 `httpcore.ConnectError`
    （`start_tls` 阶段）和 2 次 `peer closed connection without sending complete message body`
  - **不是 cc-convert 的逻辑错误**：是到上游的网络链路失败
  - 本机未设置代理环境变量，系统代理 `ProxyEnable=0`；上游域名解析到 Clash fake-IP
  - 对照实测：`baidu.com` / `volces.com` 正常；`wawapii.com` / `api.openai.com` TLS 握手不完成
  - 已通过共享连接池 + 错误信息改进降低触发概率和排查难度；根本修复需修 Clash 节点

- **Prompt 缓存与 cc-convert 的关系**
  - 服务端缓存（KV / 前缀缓存）**完全由上游提供商决定**，cc-convert 无法创造缓存
  - cc-convert 只做翻译：把上游的 `cache_read_input_tokens` / `cache_creation_input_tokens`
    或 OpenAI 标准的 `prompt_tokens_details.cached_tokens` 映射回 Anthropic 的 usage 字段
  - Anthropic 请求里的 `cache_control` 在转成 OpenAI 格式时被丢弃（OpenAI 协议无对应字段）
  - 上游若支持自动前缀缓存（DeepSeek、豆包、vLLM prefix caching 等），命中情况会自动反映到 usage

## 延后（有明确理由）

- `config.get_settings()` 的 `@lru_cache` 失效语义：位于请求依赖链，改动影响活跃请求的上游地址/密钥
- `config_path` 穿透到 14 个调用点：解析器已统一，属纯改动量
- `supervisor.py` 的 `wmic` 之外的历史残留命名

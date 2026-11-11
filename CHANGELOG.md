# Changelog

## [1.6.0-canary] - 2026-11-11

> 预发布（canary）。PyPI 包版本为 `1.6.0a0`（PEP 440 预发布），安装：
> `pip install --pre "cc-convert==1.6.0a0"`。桌面版为实验性，尚未进入 CI 分发。

### Added

- **Token 用量统计**：`/v1/messages`、`/v1/chat/completions`、`/v1/responses` 三入口全路径采集（含服务端工具循环与透传流），内存 15 分钟桶 + SQLite 持久化，请求热路径零磁盘 IO；拿不到 usage 记「未上报」，不按 0 记账
- **用量总览面板**（`/usage`）：KPI、堆叠趋势、构成占比、分组对比与明细表（Chart.js 本地 vendored，断网可用）
- **桌面版（实验性）**：`/desktop` 全新界面（universal-ui SPA，七页功能对齐 Web 面板，与现有界面共存）；Tauri 壳（`desktop-shell/`：窗口、托盘、Manager 生命周期、系统通知），见 `desktop-shell/README.md`
- 流式透传为采集用量向上游请求 `include_usage`；客户端未主动要求时该 usage chunk 会被剥离，客户端可见流保持不变
- `/static` 响应携带 `Cache-Control: no-cache`，前端更新后浏览器不再使用启发式缓存的旧资源

### Fixed

- 中文 Windows 下 `tasklist`/`netstat` 输出解码失败导致 `stdout` 为 `None` 的崩溃
- 桌面 UI 间距与组件用法对齐 universal-ui 约定；全局设置页选择器笔误导致的加载失败

### Docs

- 新增桌面版架构决策（ADR-014）与不变量 J 组；universal-ui 组件清单/集成契约内置仓库
- 用量统计设计/实施计划与实现同步

---

## [0.8.0] - 2026-08-03

### Breaking changes (项目改名)

- **项目更名为 cc-convert**，原名 `local-openai2anthropic` 及其作者痕迹已全部移除
- **包名**：`local_openai2anthropic` → `cc_convert`
- **CLI 命令**：`oa2a` → `ccconvert`
- **配置目录**：`~/.oa2a/` → `~/.ccconvert/`，环境变量前缀 `OA2A_` → `CCCONVERT_`
- **内部状态端点**：`/_oa2a/status` → `/_ccconvert/status`
- **前端 CSRF 头**：`X-OA2A-CSRF` → `X-CCCONVERT-CSRF`
- **Dashboard 品牌**：OA2A → CC Convert

### Added

- 实例卡片增加 `http://` 前缀显示切换
- 实例配置支持 Claude Code 模型注入（`ANTHROPIC_MODEL` / `ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU}_MODEL`），随"复制配置"一并输出

### Fixed

- `update_instance` 回滚快照补充缺失的 `upstream_format` 字段
- 编辑 `client_*` 模型注入字段不再触发无意义的 Worker 重启

---

## [0.7.3] - 2026-06-30

### Fixed

- **Auth middleware now accepts the `x-api-key` header.** The server's optional API-key authentication previously only honored `Authorization: Bearer <key>`. Anthropic-format clients — notably new-api's Claude channel, which follows the official Anthropic convention and sends `x-api-key` (with no `Authorization` header) — were rejected with `401 authentication_error`, making the channel unusable when `api_key` was configured. The middleware now accepts either `Authorization: Bearer <key>` (OpenAI style) or `x-api-key: <key>` (Anthropic style); the two are interchangeable. `Authorization: Bearer` behavior is unchanged.

---

## [0.7.2] - 2026-06-22

### Fixed

- **`created_at` now emitted as integer Unix seconds.** The Responses API documents `created_at` as an integer, but the converter was emitting a `float` (e.g. `1782085884.1576056`). Consumers that decode the field into a strict integer type — notably new-api's Go struct `OpenAIResponsesResponse.created_at` — rejected the payload with `json: cannot unmarshal number … into Go struct field … of type int`, causing channel-test failures for `glm-5.2` and `deepseek-v4-pro` on the OpenAI Responses (`/v1/responses`) endpoint. Both the non-streaming and streaming Responses paths now emit `int(time.time())`. Affects `/v1/responses` only; `/v1/messages` and `/v1/chat/completions` were already integer-correct.

---

## [0.7.1] - 2026-06-21

### Changed

- **Self-describing placeholder for orphaned tool results.** When the converter backfills a `tool` message for an assistant `tool_calls` whose result the client never supplied, the placeholder content is now an explicit error message (`[ERROR: tool result for this call was not provided by the client; output is unknown. Do not fabricate; ask the user or re-issue the tool call if needed.]`) instead of the previous vague `[tool result unavailable]`. This lets the model see that the data is genuinely missing and recover by asking the user or re-issuing the call, instead of treating the placeholder as an empty tool output and fabricating an answer. (Follow-up to the 0.7.0 fix for issue #3.)

---

## [0.7.0] - 2026-06-21

### Added

- **OpenAI Responses API bridge** (`POST /v1/responses`): Accept OpenAI Responses-format requests (the `client.responses.create` surface) and translate them to `/v1/chat/completions` for upstream backends that only implement the chat-completions API (vLLM, SGLang, …). The upstream chat completion is then translated back into a Responses `Response` object.
  - Non-streaming and streaming (SSE) both supported. Streaming emits the full Responses event sequence (`response.created`, `response.output_item.added`, `response.output_text.delta`, `response.output_item.done`, `response.completed`, …).
  - Supported input shapes: plain string, list of `EasyInputMessage` items, `function_call` / `function_call_output` (tool history), `reasoning` items (carried forward as `reasoning_content`), and multipart content with `input_text` / `input_image`.
  - `instructions` becomes a system message; `reasoning.effort` is forwarded as `chat_template_kwargs.reasoning_effort`; `tools` of type `function` are converted to chat-completions tools (other tool types are dropped).
  - Model name mapping still applies.
  - **Server-side web search on `/v1/responses`**: when a request carries a `web_search` / `web_search_preview` tool and a search provider (Tavily or 通晓/TongXiao) is configured, the proxy runs the search loop locally — the `web_search` tool is registered as an OpenAI function tool, executed via Tavily/TongXiao when the model calls it, and the results are fed back. The final Responses output includes one `web_search_call` item per executed search, followed by the model's answer. Both streaming and non-streaming supported.

### Fixed

- **Backfill placeholder tool results for orphaned tool calls.** vLLM/SGLang reject any assistant message carrying `tool_calls` when the subsequent `tool` messages do not answer every `tool_call_id` (history truncation, partial `tool_result` blocks, or a client sending only the call). The Anthropic converter and the Responses converter now insert a neutral placeholder `tool` message right after the assistant message for any `tool_call_id` lacking a matching tool result, so the upstream backend accepts the conversation instead of returning `400 invalid_request_error: An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'`. (GitHub issue #3.)

---

## [0.6.7] - 2026-06-17

### Added

- **GLM (Zhipu/Z.ai) reasoning_effort semantic mapping.** SGLang-served GLM-4.5/4.6/4.7/5.x chat templates only wire two effective reasoning levels — `High` (dials reasoning down) and `Max` (highest, the default) — via a `Reasoning Effort: <level>` system line, and any value other than `"high"` falls through to `Max`. Forwarding Anthropic effort tiers verbatim therefore inverts the intent (e.g. Anthropic `low` → GLM `Max`). The converter now detects GLM models by name and maps:
  - Anthropic `low` / `medium` (want less reasoning) → GLM `"high"` (GLM's low band)
  - Anthropic `high` / `xhigh` / `max` / unset (want more reasoning) → unset → GLM `Max`

### Changed

- DeepSeek V4 keeps its existing verbatim effort forwarding (defaulting to `"high"`); only GLM takes the two-band remap.

### Fixed

- Corrected the `--version` flag string, which had fallen behind at `0.6.5`.

---

## [0.6.5] - 2026-05-06

### Added

- **OpenAI-native passthrough endpoint** (`POST /v1/chat/completions`): Proxy OpenAI-format requests directly to the upstream API without any conversion. This allows clients that already speak the OpenAI protocol to bypass the Anthropic to OpenAI conversion layer, reducing overhead and preserving all upstream-specific fields (e.g., `logprobs`, `token_ids`, `refusal`, `annotations`).
  - Non-streaming and streaming (SSE) modes both supported.
  - All request fields are forwarded as-is — no validation, no filtering, no model name mapping.
  - Error handling: timeout (504), connection error (502), invalid JSON (400).

### Changed

- None.

### Fixed

- None.

---

## [0.6.4] - 2026-04-21

### Added

- Anthropic `output_config.effort` to `reasoning_effort` mapping for DeepSeek V4.
- `preserve_thinking` in `chat_template_kwargs` when thinking mode is enabled.
- Model name mapping with wildcard support.

### Fixed

- Support both `reasoning` and `reasoning_content` in streaming responses.

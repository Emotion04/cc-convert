# HANDOFF — 接手说明

> 写给下一个接手的人 / agent。读这份 + [文档索引](README.md) 就能继续。
> 最后更新：2026-10-30。

## 0. 一句话现状

协议桥本体稳定可用；**用量统计 P0（采集 + SQLite）与 P1（总览面板 /usage）已提交**
（全量 807 passed, 6 skipped；实机与浏览器冒烟通过）。
下一个任务是按 [用量统计实施计划](用量统计实施计划.md) 做 **P2 计费引擎**。

## 1. 先做这三件事

```bash
cd D:\CCode\local-openai2anthropic
git status                     # 确认工作区改动（见 §3）
uv run pytest -q               # 应为 719 passed, 6 skipped
uv run ccconvert status        # Manager 是否在跑、实例状态
```

然后按顺序读：`CLAUDE.md` → [架构总览](架构总览.md) → [不变量与陷阱](不变量与陷阱.md) → [开发指南](开发指南.md)。

## 2. 本会话完成了什么（已实现、已验证、**未提交**）

### 2.1 Responses 上游支持（新功能）

- 新增 `src/cc_convert/upstream_responses.py`：Chat 请求体 → Responses（`input`/`tools`/`reasoning`）、Responses 响应 → chat completion、Responses SSE → chat SSE。
- 接入 `/v1/messages`（流式/非流式/服务端工具）、`/v1/chat/completions`、`/v1/responses`（上下游同为 Responses 时直通）。
- 真实上游（aiaimax）实测：文本、流式、工具调用、多轮工具历史、两个 OpenAI 端点均通过。

### 2.2 思考策略探测修复

- 探测总预算 **20 s**（两个请求并发），失败/无信号**不再写纠正**。
- 前端：5 秒弹警告 toast + 实时倒计时（`config.html::probeThinking`）。
- Worker 启动时的 `upstream` 探测改为后台线程，不阻塞端口开放。
- 401/403 会带出上游原话（例：`INVALID_API_KEY`、`AccountOverdueError`）。

### 2.3 启动 / 进程生命周期修复

- 事件循环里的同步 shell 调用全部改 `asyncio.to_thread`；端口总览/自检路由同样。
- 实例自启改为并发（7 个实例 ~35 s 就绪，此前 1–2 分钟）；面板 ~10 s 可用。
- `taskkill /F /T` 杀进程树；启动前同时按 pid 文件与 meta 文件驱逐残留。
- Worker 增加父进程看门狗：Manager 消失 2 s 内退出（`stop -f` 后无残留，已实测）。

### 2.4 其它

- 探测/策略相关 UI 中文标签统一（`智能（auto）` 等），厂商弹窗同步。
- 修正了「chat 缓存更好」的错误说法（实测复现不了，见 `决策记录` 与 `profile_store` 注释）。
- 文档：本目录新增架构/开发/测试/不变量/决策/索引 + 两份用量统计文档。

### 2.5 审查发现的事故点修复

代码审查确认的缺陷里，功能性的一批已修（回归测试见 `tests/test_incident_fixes.py`）：

- **I1** server tool 未检查搜索服务是否启用（会把 server tool 当普通 function 转发）→ 已按启用状态门控并剔除。
- **I3** Responses 负载缺 `object` 时不转换 → 已按 `output` 字段兜底。
- **I5** Responses 上游 + `web_search` 时把 chat 体发到 `/responses` → 已改为一律直通（见 ADR-012）。
- **I7** 永远发送 `repetition_penalty: null` → 已改为仅在显式设置时发送。
- **I8** 环境变量会覆盖 config.toml → 已禁用 env source。
- **I9** `restart --port` 参数丢失 → 已透传。
- **I10** 两份 `_ensure_tool_results_for_tool_calls` → 已去重。
- **I13**（新发现）Tavily/通晓 client 单例不随配置变化 → 已按 `(api_key, timeout)` 作缓存键。

未修项与原因见 `不变量与陷阱 §I`。

### 2.6 测试

新增 `tests/test_upstream_responses.py`（14 个）、`tests/test_incident_fixes.py`（9 个）与探测相关用例；全量 **729 passed, 6 skipped**。

## 3. 工作区状态（重要）

```
M  CLAUDE.md
M  src/cc_convert/{daemon,profile_store,router,streaming/handler,supervisor,
                   thinking_policy,tools/handler,worker_runner}.py
M  src/cc_convert/web/{routes.py,templates/config.html}
M  tests/{test_daemon_advanced,test_passthrough,test_profile_store,test_thinking_policy}.py
?? src/cc_convert/upstream_responses.py
?? tests/test_upstream_responses.py
?? docs/（新增文档）
```

**建议**：先单独提交一个基线 commit（一句话说明），再开始用量统计。否则 P0 的 diff 会和这批改动混在一起，出问题无法 bisect。

## 4. 下一个任务：Token 用量统计 P2（计费引擎）

- 设计：[用量统计设计.md](用量统计设计.md)
- 计划（任务分解/验收/顺序）：[用量统计实施计划.md](用量统计实施计划.md)
- **P0/P1 已完成**：
  - P0：`src/cc_convert/usage/`（recording / ledger / store）、共享
    `usage.db`（WAL, schema_version=1）、`/_ccconvert/status` 带 `usage` 字段。
  - P1：`GET /api/usage/summary`（totals/series/groups，本地时区按天/小时聚合）、
    `/usage` 页面（KPI + 堆叠趋势 + 环形 + 分组条形 + 明细表）、
    侧边栏「用量统计」入口、Chart.js 本地 vendored 于 `web/static/vendor/`。
- P2 计费；P3 对比页。
- P0 的实现要点（改代码前必读）：
  - 请求线程只做内存累加，后台线程每 5 s flush，进程退出（lifespan）再刷一次。
  - 拿不到 usage 记 `unreported`，绝不按 0 记账；流式多见。
  - 流式用 `recorder.latest()`（最后一次上报为准，防重复计数）；
    服务端工具循环用 `recorder.upstream()`（逐次累加）。
  - chat/Responses 的 `input_tokens` **含缓存**，Anthropic 的**不含**——
    `recording.normalize_usage()` 已按字段形状区分，别改坏。
  - chat 透传流给上游注入 `include_usage`，客户端没要求时会把 usage chunk 剥离再转发。
  - 测试注意：`tests/test_usage_api.py` 用 monkeypatch 把 `CCCONVERT_RUNTIME_DIR`
    指到私有目录——其它测试模块会往共享沙箱 usage.db 写数据并持有连接。

### 开始前必须知道的三个决定（已定）

1. 价格带版本（`effective_from`），默认「按当时价」，另提供「按现价」模拟。
2. 上下文档位进桶主键（`prompt_class`），不做平均近似。
3. 缓存 TTL 以**实例设置**为准；请求里的 `cache_control.ttl` 只作提示（因为转 OpenAI 协议时它不会传给上游）。

## 5. 环境现状（本机）

| 项 | 状态 |
|---|---|
| Manager | 运行中，端口 18406（`ccconvert status` 确认） |
| 实例 | `gpt/doubao/Waw/opencode/tryapi/gguuai` 运行；`default` 未配 key 不启动 |
| gguuai | `upstream_format=chat_completions`，实测 200、缓存命中 4224 |
| Waw | **API key 已失效**（上游返回 `INVALID_API_KEY`）——需要换 key，不是代码问题 |
| doubao | **账号欠费**（`AccountOverdueError`）——不是代码问题 |
| 平台 | Windows 11，Git Bash，uv 管理 |

## 6. 用户偏好与硬约束

- 沟通与 UI 用中文；专有名词可保留英文并附中文。
- **密钥必须能在面板完整展开**（`GET /api/instances/{id}/key`）——这是有意的本地便利，不要「顺手打码」。
- **代理层不做隐式重试**，不替客户端改语义。
- 详细日志默认关闭（DEBUG），INFO 只留启动/错误。
- 探测最长 20 s，5 s 提示，实时倒计时。
- 图表库/前端第三方资源**本地 vendored**（用户可能断网/在墙内）。
- 提交信息一句话；一个逻辑改动一个提交。

## 7. 已知缺陷（未修部分）

完整列表与状态在 [不变量与陷阱 §I](不变量与陷阱.md)。功能性缺陷（I1/I3/I5/I7/I8/I9/I10/I13）已在本次会话修复；
仍保留的：

- **I12**：Responses 流里 `function_call_arguments.delta` 先于 `output_item.added` 时工具参数可能错位（极端顺序）。
- **I6**：上游不返回 usage 时流式用了 tiktoken 估算——实现用量统计时要区分「上报值/估算值」。
- **I4**：`/v1/chat/completions` 不做模型映射，这是「原生透传」的有意设计。
- **I2/I11**：流式终止事件的差异，已判定为非缺陷或低风险。

## 8. 验证「改动没搞坏」的最小清单

```bash
uv run pytest -q
uv run python -c "import cc_convert.router, cc_convert.upstream_responses"   # 导入冒烟
uv run ccconvert restart && uv run ccconvert status                          # 生命周期
# 对运行中的实例发一次真实请求（换成你的实例端口）
curl -s http://127.0.0.1:18408/v1/messages -H "Authorization: Bearer local" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-20250514","max_tokens":8,"messages":[{"role":"user","content":"hi"}]}'
```

## 9. 交接时的未决问题

- 用量统计 P2 计费未动工；`price_catalog` 等表还不存在，面板成本列显示「计费引擎上线后提供」。
- 是否要在 P4 做「余额探测」取决于目标中转是否提供接口（暂按尽力而为设计）。
- Docker 路径未验证，用户主线是 `uv` 源码运行。
- `tests/test_integration.py` 有无条件 skip，属已知工具缺口（不影响本次任务）。
- `usage_bucket.vendor` 列暂存空串（默认=实例名），P2 做计费时再决定是否单独维护。

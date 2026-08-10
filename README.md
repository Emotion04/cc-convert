# cc-convert

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![PyPI](https://img.shields.io/pypi/v/cc-convert.svg)](https://pypi.org/project/cc-convert/)
[![Downloads](https://img.shields.io/pypi/dm/cc-convert.svg)](https://pypi.org/project/cc-convert/)
[![Last Commit](https://img.shields.io/github/last-commit/Emotion04/cc-convert.svg)](https://github.com/Emotion04/cc-convert)
[![CI](https://img.shields.io/github/actions/workflow/status/Emotion04/cc-convert/publish.yml.svg)](https://github.com/Emotion04/cc-convert/actions)

**[中文] | [English](README_en.md)**

一个双向 API 协议转换代理，让 **Claude Code / Anthropic SDK 运行在任意 OpenAI 兼容后端**（vLLM、SGLang、云 API）上，内置管理面板，支持多实例配置，真正实现多窗口多配置管理。

---

如果你是大脑发达的AI使用者，会并行使用多个 Claude Code，并且希望多个 Claude Code 窗口分别使用**不同的模型或上游**（使用更强大的模型写代码、使用低成本的flash模型聊天或整理文件），似乎是一件棘手的事情：如果依赖环境变量 `ANTHROPIC_BASE_URL`，全局只有一个值——所有窗口都会指向同一个实例。著名的项目 CCSwitch 也是如此，只能快速切换配置，但不能精准地切换每一个窗口的配置。但是 CC-Convert 做到了。

## 最佳实践

CC Convert 的每个实例是独立端口。为每个窗口准备一份 `settings.json`，用 `claude --setting` 启动，不同窗口就能走不同的实例：

1. 在 Dashboard 创建多个实例（各对应不同上游/模型），记下各自端口
2. 为每个实例生成 settings 文件。实例卡片的**复制配置**可直接输出 `settings.json` 片段：

```json
// settings-work.json —— 窗口 1
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:18401",
    "ANTHROPIC_AUTH_TOKEN": "local"
  }
}
```

```json
// settings-chat.json —— 窗口 2
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:18402",
    "ANTHROPIC_AUTH_TOKEN": "local"
  }
}
```

3. 分别启动：

```bash
claude --setting settings-work.json    # 窗口 1 → 实例 A
claude --setting settings-chat.json    # 窗口 2 → 实例 B
```

这样每个窗口独立指向自己的 CC Convert 实例，模型、上游互不干扰，一个终端窗口一套配置。

> 提示：实例的Key与目标模型（target_model）会自动覆盖请求里的模型名，所以 settings 里可以不写Key与模型，统一由实例决定；想显式指定也可以像上面那样加上 `ANTHROPIC_MODEL`。

我使用的方法是仅在实例中配置 base_url 与 Key，Model 则由json配置文件指定。如此一来，你可以单独配置不同档位的Model，更重要的是，你依旧可以使用 `/model` 切换模型。
如

```json

{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "local",
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:18401",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "step-3.5-flash-2603",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "step-3.5-flash-2603",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "step-3.5-flash-2603",
    "ANTHROPIC_MODEL": "claude-fable-5",
  },

```



## 快速开始 (Python ≥ 3.12)

```bash
pip install cc-convert
ccconvert start
```

1. 打开 Dashboard——启动时会打印地址（如 `http://127.0.0.1:19500`）
2. 点击**添加实例**，填上游 **URL** 和 **API 密钥**（可选目标模型），保存并启动
3. 在实例卡片点**复制配置**，然后把 Claude Code 指过去：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:<实例端口>"
export ANTHROPIC_AUTH_TOKEN="local"
claude
```

或者直接用 Anthropic SDK 调用：

```python
import anthropic

client = anthropic.Anthropic(base_url="http://127.0.0.1:<实例端口>", api_key="local")
resp = client.messages.create(
    model="your-model",
    max_tokens=1024,
    messages=[{"role": "user", "content": "你好！"}],
)
print(resp.content[0].text)
```

## 工作原理

CC Convert 把每个Anthropic Messages 协议的请求转换成 OpenAI 格式发给你的后端，再把响应转回 Anthropic 格式。每个**实例**是一个独立代理进程、独立端口，由 **Manager** 面板统一管理。

三种代理模式：

| 模式 | 端点 | 适用场景 |
|------|------|----------|
| Anthropic 代理 | `POST /v1/messages` | Claude Code / Anthropic SDK |
| OpenAI 透传 | `POST /v1/chat/completions` | OpenAI 原生客户端，零转换开销 |
| Responses 桥接 | `POST /v1/responses` | OpenAI Responses SDK → 只实现 chat/completions 的后端 |



## 命令行

| 命令 | 说明 |
|------|------|
| `ccconvert start` | 后台启动 Manager 面板 |
| `ccconvert stop` / `restart` | 停止 / 重启 |
| `ccconvert status` | 查看 Manager 与实例状态 |
| `ccconvert logs` | 查看日志（`-f` 跟随） |
| `ccconvert open` | 浏览器打开面板 |

## 配置

配置文件：`~/.ccconvert/config.toml`（首次运行自动创建；`CCCONVERT_CONFIG_DIR` 可覆盖目录）。实例通常通过面板管理，无需手改。

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/v1/messages` | Anthropic 消息（支持流式） |
| `POST` | `/v1/chat/completions` | OpenAI 透传 |
| `POST` | `/v1/responses` | OpenAI Responses 桥接 |
| `GET` | `/v1/models` | 列出上游模型（透传） |
| `GET` | `/health` | 健康检查 |

完整 API 参考与使用文档：打开面板里的 **API 文档** 页（`/docs`）。

## 特色功能

- ~~Anthropic ↔ OpenAI 协议双向转换~~
- 支持流式 SSE、工具调用、视觉输入
- thinking 块映射与推理深度思考支持
- ~~服务端 Web Search（Tavily / 通晓），任意模型代理层联网搜索~~
- 多实例守护进程，自动分配空闲端口与崩溃自恢复

## 支持的后端

vLLM、SGLang，以及任意 OpenAI 兼容 API。

## 开发与贡献

```bash
git clone https://github.com/Emotion04/cc-convert.git
cd cc-convert
pip install -e ".[dev]"
pytest
```

## 许可

Apache License 2.0

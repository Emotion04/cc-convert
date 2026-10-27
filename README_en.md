# cc-convert

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![PyPI](https://img.shields.io/pypi/v/cc-convert.svg)](https://pypi.org/project/cc-convert/)
[![Downloads](https://img.shields.io/pypi/dm/cc-convert.svg)](https://pypi.org/project/cc-convert/)
[![Last Commit](https://img.shields.io/github/last-commit/Emotion04/cc-convert.svg)](https://github.com/Emotion04/cc-convert)
[![CI](https://img.shields.io/github/actions/workflow/status/Emotion04/cc-convert/publish.yml.svg)](https://github.com/Emotion04/cc-convert/actions)

**[中文](README.md) | English**

A bidirectional API protocol converter that runs **Claude Code / Anthropic SDK on any OpenAI-compatible backend** (vLLM, SGLang, cloud APIs), with a multi-instance management dashboard.

## Quick Start

> Prerequisites, the two-port distinction, and troubleshooting are covered in **[docs/使用规范.md](docs/使用规范.md)** (Chinese).

```bash
git clone https://github.com/Emotion04/cc-convert.git
cd cc-convert
uv sync
uv run ccconvert start
```

> No uv? `pip install cc-convert` works too, then run `ccconvert start` directly.

1. Open the Dashboard — the URL is printed on startup, defaulting to the first free port from `http://127.0.0.1:18400`

> ⚠️ **Two ports, easily confused**: `18400` (onwards) is the **Manager** dashboard — do **not** point Claude Code at it. `18401` (onwards) is the **instance port**, which is what Claude Code should use. The instance card's "Copy config" button emits the instance port.

2. Click **Add Instance**, fill in the upstream **URL** and **API key** (+ optional target model), save and start
3. On the instance card click **复制配置** (Copy config), then point Claude Code at it:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:<instance-port>"
export ANTHROPIC_AUTH_TOKEN="local"
claude
```

Or call it directly with the Anthropic SDK:

```python
import anthropic

client = anthropic.Anthropic(base_url="http://127.0.0.1:<instance-port>", api_key="local")
resp = client.messages.create(
    model="your-model",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.content[0].text)
```

## How It Works

Claude Code only speaks Anthropic's Messages protocol. CC Convert translates each request to OpenAI format, forwards it to your backend, and converts the response back. Each **instance** is an independent proxy process on its own port, managed by a **Manager** dashboard.

Three proxy modes:

| Mode | Endpoint | Use case |
|------|----------|----------|
| Anthropic proxy | `POST /v1/messages` | Claude Code / Anthropic SDK |
| OpenAI passthrough | `POST /v1/chat/completions` | OpenAI-native clients, zero conversion |
| Responses bridge | `POST /v1/responses` | OpenAI Responses SDK → chat-completions-only backends |

## Use Case: Different Configs per Window

### The pain point

You want several Claude Code windows to use **different models or upstreams** (e.g. one window for coding, one for chat, each on its own backend). With only the `ANTHROPIC_BASE_URL` env var there's a single global value — every window points at the same instance, so you can't tell them apart.

### Best practice: one settings file per window

Each CC Convert instance runs on its own port. Prepare one `settings.json` per window and launch with `claude --setting` — each window routes to its own instance:

1. Create multiple instances in the Dashboard (different upstream / model), note each port
2. Generate a settings file per instance — the **复制配置** (Copy config) button on the instance card outputs a `settings.json` snippet directly:

```json
// settings-work.json — window 1
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:18401",
    "ANTHROPIC_AUTH_TOKEN": "local"
  }
}
```

```json
// settings-chat.json — window 2
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:18402",
    "ANTHROPIC_AUTH_TOKEN": "local"
  }
}
```

3. Launch separately:

```bash
claude --setting settings-work.json    # window 1 → instance A
claude --setting settings-chat.json    # window 2 → instance B
```

Each window now points at its own CC Convert instance — models and upstreams stay isolated, one terminal window per config.

> Tip: an instance's target model (`target_model`) overrides the model name in every request, so the settings file doesn't have to specify a model — the instance decides. You can still set `ANTHROPIC_MODEL` explicitly if you prefer.

## CLI

| Command | Description |
|---------|-------------|
| `ccconvert start` | Start the manager dashboard in background |
| `ccconvert stop` / `restart` | Stop / restart |
| `ccconvert status` | Show manager & instance status |
| `ccconvert logs` | View logs (`-f` to follow) |
| `ccconvert open` | Open the dashboard in your browser |

## Configuration

Config file: `~/.ccconvert/config.toml` (auto-created; `CCCONVERT_CONFIG_DIR` overrides the directory). Runtime data (`manager.pid` / `manager.log` / `instances/`) lives alongside it and is overridable via `CCCONVERT_RUNTIME_DIR`. Instances are normally managed through the dashboard — no manual editing needed.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/messages` | Anthropic Messages (streaming supported) |
| `POST` | `/v1/chat/completions` | OpenAI passthrough |
| `POST` | `/v1/responses` | OpenAI Responses bridge |
| `GET` | `/v1/models` | List upstream models (proxied) |
| `GET` | `/health` | Health check |

Full API reference and user guide: open the **API 文档** page in the dashboard (`/docs`).

## Features

- Bidirectional Anthropic ↔ OpenAI conversion
- Streaming SSE, tool calling, vision
- Interleaved thinking / reasoning support
- Server-side web search (Tavily / 通晓) for any model
- Model name mapping (wildcards) & per-instance target model
- Multi-instance daemon with auto port allocation & crash recovery

## Supported Backends

vLLM, SGLang, and any OpenAI-compatible API.

## Development

```bash
git clone https://github.com/Emotion04/cc-convert.git
cd cc-convert
uv sync          # installs runtime and test dependencies in one step
uv run pytest
```

> pip users can still use `pip install -e ".[dev]"`. The two dev-dependency declarations are kept in sync, verified by `tests/test_dev_deps.py`.

The test suite is sandboxed: `tests/conftest.py` redirects the config and runtime directories to a temporary location and verifies at session end that your real `~/.ccconvert/config.toml` was left untouched.

## License

Apache License 2.0

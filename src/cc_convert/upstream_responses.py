# SPDX-License-Identifier: Apache-2.0
"""Chat Completions <-> Responses API adapter for Responses-only upstreams.

The proxy builds Chat Completions requests internally: both the Anthropic
converter and the native passthrough produce that shape, and every downstream
parser (``streaming/handler.py``, ``openai.ChatCompletion``) expects it back.

When ``upstream_format == "responses"`` the upstream only speaks
``/v1/responses``, so this module translates in both directions:

    chat request          -> Responses request body
    Responses response    -> chat completion dict
    Responses SSE         -> chat completion SSE chunks

Keeping the translation here means the hot-path parsers never learn about the
Responses format: they keep consuming the chat shape they were written for.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterable, AsyncIterator

_IMAGE_TYPES = ("image_url", "input_image")


def _now() -> int:
    return int(time.time())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text_from_content(content: Any) -> str:
    """Flatten an OpenAI message content value to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in ("text", "input_text", "output_text"):
                    parts.append(part.get("text", "") or "")
        return "\n".join(p for p in parts if p)
    return str(content)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _user_content_parts(content: Any) -> list[dict[str, Any]]:
    """Convert chat message content to Responses ``input`` content parts."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]

    parts: list[dict[str, Any]] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                parts.append({"type": "input_text", "text": part})
                continue
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("text", "input_text", "output_text"):
                parts.append({"type": "input_text", "text": part.get("text", "") or ""})
            elif ptype in _IMAGE_TYPES:
                url = part.get("image_url") or part.get("url")
                if isinstance(url, dict):
                    url = url.get("url")
                if url:
                    parts.append({"type": "input_image", "image_url": url})
    return parts


def _reasoning_item(text: str) -> dict[str, Any]:
    return {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": text}],
    }


def _messages_to_items(
    messages: Any,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Split chat ``messages`` into Responses ``instructions`` + ``input`` items."""
    instructions: list[str] = []
    items: list[dict[str, Any]] = []

    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        if role in ("system", "developer"):
            text = _text_from_content(content)
            if text:
                instructions.append(text)
            continue

        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id") or "",
                    "output": _stringify(content),
                }
            )
            continue

        if role == "assistant":
            reasoning = msg.get("reasoning_content") or msg.get("reasoning")
            if reasoning:
                items.append(_reasoning_item(str(reasoning)))
            text = _text_from_content(content)
            if text:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or _new_id("call"),
                        "name": function.get("name", "") or "",
                        "arguments": _stringify(
                            function.get("arguments") or "{}"
                        ),
                    }
                )
            continue

        parts = _user_content_parts(content)
        if not parts:
            parts = [{"type": "input_text", "text": ""}]
        items.append({"type": "message", "role": "user", "content": parts})

    return ("\n\n".join(instructions) if instructions else None), items


def _tools_to_responses(tools: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            # Server-side tools (web_search, file_search, …) already use the
            # Responses shape; forward untouched.
            out.append(tool)
            continue
        function = tool.get("function") or {}
        entry: dict[str, Any] = {
            "type": "function",
            "name": function.get("name", "") or "",
            "description": function.get("description", "") or "",
            "parameters": function.get("parameters")
            or {"type": "object", "properties": {}},
        }
        if function.get("strict"):
            entry["strict"] = True
        out.append(entry)
    return out


def _tool_choice_to_responses(choice: Any) -> Any:
    if choice in ("auto", "none"):
        return choice
    if choice in ("required", "any"):
        return "required"
    if isinstance(choice, dict):
        if choice.get("type") == "function":
            function = choice.get("function") or {}
            name = function.get("name") or choice.get("name")
            if name:
                return {"type": "function", "name": name}
            return "required"
    return None


def _reasoning_effort(chat: dict[str, Any]) -> str | None:
    effort = chat.get("reasoning_effort")
    if isinstance(effort, str) and effort:
        return effort
    kwargs = chat.get("chat_template_kwargs")
    if isinstance(kwargs, dict):
        effort = kwargs.get("reasoning_effort")
        if isinstance(effort, str) and effort:
            return effort
    return None


def chat_body_to_responses_body(chat: dict[str, Any]) -> dict[str, Any]:
    """Translate a Chat Completions request body to a Responses request body.

    Fields with no Responses equivalent (``stop``, ``stream_options``,
    ``repetition_penalty``, vLLM ``chat_template_kwargs``) are dropped rather
    than forwarded — strict upstreams reject unknown parameters outright.
    """
    instructions, items = _messages_to_items(chat.get("messages"))
    body: dict[str, Any] = {
        "model": chat.get("model", ""),
        "input": items,
        "stream": bool(chat.get("stream", False)),
        # Relays that proxy a ChatGPT account reject stored responses.
        "store": False,
    }
    if instructions:
        body["instructions"] = instructions

    max_tokens = chat.get("max_completion_tokens", chat.get("max_tokens"))
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool):
        body["max_output_tokens"] = max_tokens

    for key in ("temperature", "top_p"):
        value = chat.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            body[key] = value

    tools = _tools_to_responses(chat.get("tools"))
    if tools:
        body["tools"] = tools
        choice = _tool_choice_to_responses(chat.get("tool_choice"))
        if choice is not None:
            body["tool_choice"] = choice
        if isinstance(chat.get("parallel_tool_calls"), bool):
            body["parallel_tool_calls"] = chat["parallel_tool_calls"]

    effort = _reasoning_effort(chat)
    if effort:
        body["reasoning"] = {"effort": effort}

    return body


def _reasoning_text(item: dict[str, Any]) -> str:
    text = ""
    for part in item.get("summary") or []:
        if isinstance(part, dict):
            text += part.get("text", "") or ""
        elif isinstance(part, str):
            text += part
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in (
                "reasoning_text",
                "summary_text",
                "text",
            ):
                text += part.get("text", "") or ""
    return text


def _usage_to_chat(usage: Any) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("input_tokens")
    completion = usage.get("output_tokens")
    if prompt is None and completion is None:
        return None
    prompt = prompt or 0
    completion = completion or 0
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    chat_usage: dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": usage.get("total_tokens") or prompt + completion,
    }
    if isinstance(cached, int):
        chat_usage["prompt_tokens_details"] = {"cached_tokens": cached}
    return chat_usage


def responses_payload_to_chat_completion(
    payload: dict[str, Any], model: str = ""
) -> dict[str, Any]:
    """Convert a Responses ``Response`` object to a chat completion dict."""
    text_parts: list[str] = []
    reasoning = ""
    tool_calls: list[dict[str, Any]] = []

    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") in (
                    "output_text",
                    "text",
                ):
                    text_parts.append(part.get("text", "") or "")
        elif item_type == "reasoning":
            reasoning += _reasoning_text(item)
        elif item_type == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id") or _new_id("call"),
                    "type": "function",
                    "function": {
                        "name": item.get("name", "") or "",
                        "arguments": _stringify(item.get("arguments") or "{}"),
                    },
                }
            )

    text = "".join(text_parts)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": text if (text or not tool_calls) else None,
    }
    if reasoning:
        message["reasoning"] = reasoning
        message["reasoning_content"] = reasoning
    if tool_calls:
        message["tool_calls"] = tool_calls

    if tool_calls:
        finish_reason = "tool_calls"
    elif payload.get("status") == "incomplete":
        finish_reason = "length"
    else:
        finish_reason = "stop"

    completion: dict[str, Any] = {
        "id": payload.get("id") or _new_id("chatcmpl"),
        "object": "chat.completion",
        "created": payload.get("created_at") or _now(),
        "model": payload.get("model") or model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
    }
    usage = _usage_to_chat(payload.get("usage"))
    if usage is not None:
        completion["usage"] = usage
    return completion


def _chat_chunk(
    response_id: str,
    model: str,
    *,
    created: int,
    delta: dict[str, Any],
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
    with_choices: bool = True,
) -> str:
    chunk: dict[str, Any] = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": (
            [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
            if with_choices
            else []
        ),
    }
    if usage is not None:
        chunk["usage"] = usage
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def responses_sse_as_chat_sse(
    lines: AsyncIterable[str],
) -> AsyncIterator[str]:
    """Translate a Responses SSE stream into chat completion SSE chunks.

    Only the events the proxy needs are translated; everything else
    (metadata, obfuscation payloads, content_part events) is ignored.
    """
    response_id = _new_id("chatcmpl")
    model = ""
    created = _now()
    started = False
    tool_indexes: dict[str, int] = {}
    next_tool_index = 0
    saw_tool_call = False
    finished = False

    def _start() -> str:
        return _chat_chunk(
            response_id,
            model,
            created=created,
            delta={"role": "assistant", "content": ""},
        )

    async for line in lines:
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        if data == "[DONE]":
            break

        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type") or ""

        if event_type == "response.created":
            response = event.get("response") or {}
            response_id = response.get("id") or response_id
            model = response.get("model") or model
            created = response.get("created_at") or created
            if not started:
                started = True
                yield _start()
            continue

        if not started:
            # Some relays skip response.created; open the stream anyway.
            started = True
            yield _start()

        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if delta:
                yield _chat_chunk(
                    response_id, model, created=created, delta={"content": delta}
                )

        elif event_type in (
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
            "response.reasoning_summary.delta",
        ):
            delta = event.get("delta")
            if delta:
                yield _chat_chunk(
                    response_id,
                    model,
                    created=created,
                    delta={"reasoning_content": delta},
                )

        elif event_type == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") != "function_call":
                continue
            item_id = item.get("id") or item.get("call_id") or ""
            index = next_tool_index
            next_tool_index += 1
            saw_tool_call = True
            if item_id:
                tool_indexes[item_id] = index
            yield _chat_chunk(
                response_id,
                model,
                created=created,
                delta={
                    "tool_calls": [
                        {
                            "index": index,
                            "id": item.get("call_id") or item_id,
                            "type": "function",
                            "function": {
                                "name": item.get("name", "") or "",
                                "arguments": "",
                            },
                        }
                    ]
                },
            )

        elif event_type == "response.function_call_arguments.delta":
            index = tool_indexes.get(event.get("item_id") or "", 0)
            delta = event.get("delta")
            if delta:
                yield _chat_chunk(
                    response_id,
                    model,
                    created=created,
                    delta={
                        "tool_calls": [
                            {"index": index, "function": {"arguments": delta}}
                        ]
                    },
                )

        elif event_type == "response.completed":
            response = event.get("response") or {}
            status = response.get("status")
            finish_reason = (
                "tool_calls"
                if saw_tool_call
                else ("length" if status == "incomplete" else "stop")
            )
            yield _chat_chunk(
                response_id,
                model,
                created=created,
                delta={},
                finish_reason=finish_reason,
            )
            usage = _usage_to_chat(response.get("usage"))
            if usage is not None:
                yield _chat_chunk(
                    response_id,
                    model,
                    created=created,
                    delta={},
                    usage=usage,
                    with_choices=False,
                )
            finished = True

        elif event_type in ("response.failed", "error"):
            message = ""
            response = event.get("response") or {}
            error = response.get("error") or event.get("error") or {}
            if isinstance(error, dict):
                message = error.get("message") or ""
            logger_message = message or "upstream response failed"
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [],
                        "error": {"message": logger_message},
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            finished = True

    if not started:
        started = True
        yield _start()

    if not finished:
        # The stream ended without response.completed; still close cleanly.
        yield _chat_chunk(
            response_id,
            model,
            created=created,
            delta={},
            finish_reason="tool_calls" if saw_tool_call else "stop",
        )

    yield "data: [DONE]\n\n"

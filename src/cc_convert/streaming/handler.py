# SPDX-License-Identifier: Apache-2.0
"""Streaming response handlers."""

import json
import logging
import time
from typing import Any, AsyncGenerator

import httpx
from fastapi.responses import JSONResponse

from cc_convert.protocol import AnthropicError, AnthropicErrorResponse
from cc_convert.thinking_policy import note_upstream_rejection
from cc_convert.utils.errors import upstream_diagnostic
from cc_convert.utils.tokens import (
    _chunk_text,
    _count_tokens,
    _estimate_input_tokens,
    anthropic_input_tokens,
    extract_cache_creation_tokens,
    extract_cache_read_tokens,
)

logger = logging.getLogger(__name__)


def _extract_cache_tokens(usage: Any) -> tuple[int | None, int | None]:
    """Extract cache read and cache creation tokens from a vLLM/SGLang usage dict.

    vLLM may report cache hits in multiple forms:
      - ``cache_read_input_tokens`` / ``cache_creation_input_tokens`` at the top level
      - ``prompt_tokens_details.cached_tokens`` (standard OpenAI-compatible form)

    Returns a ``(cache_read, cache_creation)`` tuple.  Each value is ``None`` when
    the corresponding field is absent or zero.
    """
    if not isinstance(usage, dict):
        return None, None

    # Covers OpenAI/vLLM (prompt_tokens_details.cached_tokens), DeepSeek
    # (prompt_cache_hit_tokens) and vLLM's cache_read_input_tokens.
    return (
        extract_cache_read_tokens(usage),
        extract_cache_creation_tokens(usage),
    )


async def _stream_response(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    json_data: dict,
    model: str,
    *,
    upstream_body: dict | None = None,
    responses_upstream: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Stream response from OpenAI and convert to Anthropic format.

    ``json_data`` is always the chat-shaped request, used locally for input
    token estimation. ``upstream_body`` is what actually gets posted; when the
    upstream speaks the Responses API it is the translated body, and the SSE
    stream is translated back to chat chunks before parsing.
    """
    body = upstream_body if upstream_body is not None else json_data
    try:
        async with client.stream(
            "POST", url, headers=headers, json=body
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                error_text = error_body.decode("utf-8", errors="replace").strip()
                # In "follow upstream" mode, remember a reasoning-shape 400 so
                # later requests adapt (see thinking_policy.py).
                note_upstream_rejection(response.status_code, error_text, model)
                try:
                    error_json = json.loads(error_text) if error_text else {}
                    error_msg = error_json.get("error", {}).get("message") or error_text
                except json.JSONDecodeError:
                    error_msg = error_text
                if not error_msg:
                    error_msg = (
                        response.reason_phrase
                        or f"Upstream API error ({response.status_code})"
                    )
                error_msg += upstream_diagnostic("POST", url, response.status_code)

                error_event = AnthropicErrorResponse(
                    error=AnthropicError(type="api_error", message=error_msg)
                )
                yield f"event: error\ndata: {error_event.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
                return

            # Process SSE stream
            first_chunk = True
            content_block_started = False
            content_block_index = 0
            current_block_type = None  # 'thinking', 'text', or 'tool_use'
            current_tool_call_index = None
            tool_call_buffers: dict[int, str] = {}
            finish_reason = None
            input_tokens = _estimate_input_tokens(json_data)
            output_tokens = 0
            cache_read_input_tokens = None
            cache_creation_input_tokens = None
            message_id = None
            sent_message_delta = False
            pending_text_prefix = ""

            lines = response.aiter_lines()
            if responses_upstream:
                from cc_convert.upstream_responses import (
                    responses_sse_as_chat_sse,
                )

                lines = responses_sse_as_chat_sse(lines)

            async for line in lines:
                if not line.startswith("data: "):
                    continue

                data = line[6:]
                if data == "[DONE]":
                    if not sent_message_delta:
                        stop_reason_map = {
                            "stop": "end_turn",
                            "length": "max_tokens",
                            "tool_calls": "tool_use",
                        }
                        delta_event = {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": stop_reason_map.get(
                                    finish_reason or "stop", "end_turn"
                                )
                            },
                            "usage": {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "cache_creation_input_tokens": cache_creation_input_tokens,
                                "cache_read_input_tokens": cache_read_input_tokens,
                            },
                        }
                        logger.debug(
                            f"[Anthropic Stream Event] message_delta: {json.dumps(delta_event, ensure_ascii=False)}"
                        )
                        yield f"event: message_delta\ndata: {json.dumps(delta_event)}\n\n"
                    break

                try:
                    chunk = json.loads(data)
                    logger.debug(
                        f"[OpenAI Stream Chunk] {json.dumps(chunk, ensure_ascii=False)}"
                    )
                except json.JSONDecodeError:
                    continue

                # First chunk: message_start
                if first_chunk:
                    message_id = chunk.get("id", "")
                    usage = chunk.get("usage") or {}
                    cr_init, cc_init = _extract_cache_tokens(usage)
                    # Anthropic input_tokens excludes cached tokens.
                    input_tokens = anthropic_input_tokens(usage, input_tokens)
                    if cr_init is not None:
                        cache_read_input_tokens = cr_init
                    if cc_init is not None:
                        cache_creation_input_tokens = cc_init

                    start_event = {
                        "type": "message_start",
                        "message": {
                            "id": message_id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": model,
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {
                                "input_tokens": input_tokens,
                                "output_tokens": 0,
                                "cache_creation_input_tokens": cache_creation_input_tokens,
                                "cache_read_input_tokens": cache_read_input_tokens,
                            },
                        },
                    }
                    logger.debug(
                        f"[Anthropic Stream Event] message_start: {json.dumps(start_event, ensure_ascii=False)}"
                    )
                    yield f"event: message_start\ndata: {json.dumps(start_event)}\n\n"
                    first_chunk = False
                    # Some providers put the first token in the same chunk as
                    # the metadata. Fall through so it is not dropped; only
                    # skip when there is no choice to process.
                    if not chunk.get("choices"):
                        continue

                # Handle usage-only chunks
                if not chunk.get("choices"):
                    usage = chunk.get("usage") or {}
                    if usage:
                        input_tokens = usage.get("prompt_tokens", input_tokens)
                        output_tokens = usage.get("completion_tokens", output_tokens)
                        cr, cc = _extract_cache_tokens(usage)
                        if cr is not None:
                            cache_read_input_tokens = cr
                        if cc is not None:
                            cache_creation_input_tokens = cc
                        if content_block_started:
                            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': content_block_index})}\n\n"
                            content_block_started = False

                        stop_reason_map = {
                            "stop": "end_turn",
                            "length": "max_tokens",
                            "tool_calls": "tool_use",
                        }
                        delta_event = {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": stop_reason_map.get(
                                    finish_reason or "stop", "end_turn"
                                )
                            },
                            "usage": {
                                "input_tokens": anthropic_input_tokens(
                                    usage, input_tokens
                                ),
                                "output_tokens": usage.get("completion_tokens", 0),
                                "cache_creation_input_tokens": cache_creation_input_tokens,
                                "cache_read_input_tokens": cache_read_input_tokens,
                            },
                        }
                        logger.debug(
                            f"[Anthropic Stream Event] message_delta: {json.dumps(delta_event, ensure_ascii=False)}"
                        )
                        if logger.isEnabledFor(logging.DEBUG):
                            cr_read = cache_read_input_tokens or 0
                            total_in = cr_read + input_tokens
                            logger.debug(
                                "[cache] hit=%d input=%d ratio=%s%%",
                                cr_read,
                                anthropic_input_tokens(usage, input_tokens),
                                int(cr_read * 100 / total_in) if total_in else 0,
                            )
                        yield f"event: message_delta\ndata: {json.dumps(delta_event)}\n\n"
                        sent_message_delta = True
                    continue

                choice = chunk["choices"][0]
                delta = choice.get("delta", {})

                # Track finish reason (but don't skip - content may also be present)
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

                # Handle reasoning content (thinking)
                # vLLM uses "reasoning", SGLang uses "reasoning_content" — support both
                reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                if reasoning:
                    pending_text_prefix = ""
                    # Start thinking content block if not already started
                    if not content_block_started or current_block_type != "thinking":
                        # Close previous block if exists
                        if content_block_started:
                            stop_block = {
                                "type": "content_block_stop",
                                "index": content_block_index,
                            }
                            logger.debug(
                                f"[Anthropic Stream Event] content_block_stop ({current_block_type}): {json.dumps(stop_block, ensure_ascii=False)}"
                            )
                            yield f"event: content_block_stop\ndata: {json.dumps(stop_block)}\n\n"
                            content_block_index += 1
                        start_block = {
                            "type": "content_block_start",
                            "index": content_block_index,
                            "content_block": {
                                "type": "thinking",
                                "thinking": "",
                                "signature": "",
                            },
                        }
                        logger.debug(
                            f"[Anthropic Stream Event] content_block_start (thinking): {json.dumps(start_block, ensure_ascii=False)}"
                        )
                        yield f"event: content_block_start\ndata: {json.dumps(start_block)}\n\n"
                        content_block_started = True
                        current_block_type = "thinking"

                    for chunk in _chunk_text(reasoning):
                        delta_block = {
                            "type": "content_block_delta",
                            "index": content_block_index,
                            "delta": {"type": "thinking_delta", "thinking": chunk},
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(delta_block)}\n\n"
                    continue

                # Handle content
                if isinstance(delta.get("content"), str):
                    content_text = delta.get("content", "")
                    if not content_text:
                        continue
                    if content_text.strip() == "(no content)":
                        continue
                    if not content_block_started or current_block_type != "text":
                        if not content_text.strip():
                            pending_text_prefix += content_text
                            continue
                        # Close previous block if exists
                        if content_block_started:
                            stop_block = {
                                "type": "content_block_stop",
                                "index": content_block_index,
                            }
                            logger.debug(
                                f"[Anthropic Stream Event] content_block_stop ({current_block_type}): {json.dumps(stop_block, ensure_ascii=False)}"
                            )
                            yield f"event: content_block_stop\ndata: {json.dumps(stop_block)}\n\n"
                            content_block_index += 1
                        start_block = {
                            "type": "content_block_start",
                            "index": content_block_index,
                            "content_block": {"type": "text", "text": ""},
                        }
                        logger.debug(
                            f"[Anthropic Stream Event] content_block_start (text): {json.dumps(start_block, ensure_ascii=False)}"
                        )
                        yield f"event: content_block_start\ndata: {json.dumps(start_block)}\n\n"
                        content_block_started = True
                        current_block_type = "text"

                    if pending_text_prefix:
                        content_text = pending_text_prefix + content_text
                        pending_text_prefix = ""

                    output_tokens += _count_tokens(content_text)
                    delta_block = {
                        "type": "content_block_delta",
                        "index": content_block_index,
                        "delta": {"type": "text_delta", "text": content_text},
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(delta_block)}\n\n"

                # Handle tool calls
                if delta.get("tool_calls"):
                    pending_text_prefix = ""
                    for tool_call in delta["tool_calls"]:
                        tool_call_idx = tool_call.get("index", 0)

                        if tool_call.get("id"):
                            if content_block_started and (
                                current_block_type != "tool_use"
                                or current_tool_call_index != tool_call_idx
                            ):
                                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': content_block_index})}\n\n"
                                content_block_started = False
                                content_block_index += 1

                            if not content_block_started:
                                func = tool_call.get("function") or {}
                                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': content_block_index, 'content_block': {'type': 'tool_use', 'id': tool_call['id'], 'name': func.get('name', ''), 'input': {}}})}\n\n"
                                content_block_started = True
                                current_block_type = "tool_use"
                                current_tool_call_index = tool_call_idx
                                tool_call_buffers.setdefault(tool_call_idx, "")

                        if (tool_call.get("function") or {}).get("arguments"):
                            args = (tool_call.get("function") or {}).get(
                                "arguments", ""
                            )
                            if (
                                not content_block_started
                                or current_block_type != "tool_use"
                                or current_tool_call_index != tool_call_idx
                            ):
                                if content_block_started:
                                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': content_block_index})}\n\n"
                                    content_block_index += 1
                                func = tool_call.get("function") or {}
                                tool_id = tool_call.get("id", "")
                                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': content_block_index, 'content_block': {'type': 'tool_use', 'id': tool_id, 'name': func.get('name', ''), 'input': {}}})}\n\n"
                                content_block_started = True
                                current_block_type = "tool_use"
                                current_tool_call_index = tool_call_idx
                                tool_call_buffers.setdefault(tool_call_idx, "")
                            tool_call_buffers[tool_call_idx] = (
                                tool_call_buffers.get(tool_call_idx, "") + args
                            )
                            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': content_block_index, 'delta': {'type': 'input_json_delta', 'partial_json': args}})}\n\n"

            # Close final content block
            if content_block_started:
                stop_block = {
                    "type": "content_block_stop",
                    "index": content_block_index,
                }
                logger.debug(
                    f"[Anthropic Stream Event] content_block_stop (final): {json.dumps(stop_block, ensure_ascii=False)}"
                )
                yield f"event: content_block_stop\ndata: {json.dumps(stop_block)}\n\n"

            # Message stop
            stop_event = {"type": "message_stop"}
            logger.debug(
                f"[Anthropic Stream Event] message_stop: {json.dumps(stop_event, ensure_ascii=False)}"
            )
            yield f"event: message_stop\ndata: {json.dumps(stop_event)}\n\n"

    except httpx.RequestError as e:
        # str(ConnectError) is empty, which used to leave the client with a
        # blank error and the log with a bare "Stream error: ". Report the
        # exception type and host so the failure is actually diagnosable.
        host = url.split("/")[2] if url.count("/") >= 2 else url
        detail = str(e) or type(e).__name__
        logger.error(
            "Upstream connection failed: %s: %s (host=%s url=%s)",
            type(e).__name__, detail, host, url,
        )
        error_event = AnthropicErrorResponse(
            error=AnthropicError(
                type="api_error",
                message=(
                    f"Upstream connection failed ({type(e).__name__}: {detail}). "
                    f"Check the network path from this machine to {host}."
                ),
            )
        )
        yield f"event: error\ndata: {error_event.model_dump_json()}\n\n"
    except Exception as e:
        import traceback

        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        logger.error(f"Stream error: {error_msg}")
        error_event = AnthropicErrorResponse(
            error=AnthropicError(type="internal_error", message=str(e))
        )
        yield f"event: error\ndata: {error_event.model_dump_json()}\n\n"


async def _convert_result_to_stream(
    result: JSONResponse,
    model: str,
) -> AsyncGenerator[str, None]:
    """Convert a JSONResponse to streaming SSE format."""
    body = json.loads(bytes(result.body).decode("utf-8"))
    message_id = body.get("id", f"msg_{int(time.time() * 1000)}")
    content = body.get("content", [])
    usage = body.get("usage", {})
    stop_reason = body.get("stop_reason", "end_turn")

    # Map stop_reason
    stop_reason_map = {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
    }
    openai_stop_reason = stop_reason_map.get(stop_reason, "stop")

    # 1. message_start event
    start_event = {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": 0,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            },
        },
    }
    yield f"event: message_start\ndata: {json.dumps(start_event)}\n\n"

    # 2. Process content blocks
    for i, block in enumerate(content):
        block_type = block.get("type")

        if block_type == "text":
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': i, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
            text = block.get("text", "")
            yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': i, 'delta': {'type': 'text_delta', 'text': text}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

        elif block_type == "tool_use":
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': i, 'content_block': {'type': 'tool_use', 'id': block.get('id', ''), 'name': block.get('name', ''), 'input': block.get('input', {})}})}\n\n"
            tool_input = block.get("input", {})
            if tool_input:
                input_json = json.dumps(tool_input, ensure_ascii=False)
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': i, 'delta': {'type': 'input_json_delta', 'partial_json': input_json}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

        elif block_type == "server_tool_use":
            # Preserve official Anthropic block type so clients can count server tool uses.
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': i, 'content_block': {'type': 'server_tool_use', 'id': block.get('id', ''), 'name': block.get('name', ''), 'input': block.get('input', {})}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

        elif block_type == "web_search_tool_result":
            # Stream the tool result as its own content block.
            tool_result_block = dict(block)
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': i, 'content_block': tool_result_block})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

        elif block_type == "thinking":
            # Handle thinking blocks (BetaThinkingBlock)
            signature = block.get("signature", "")
            yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': i, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': signature}})}\n\n"
            thinking_text = block.get("thinking", "")
            if thinking_text:
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': i, 'delta': {'type': 'thinking_delta', 'thinking': thinking_text}})}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': i})}\n\n"

    # 3. message_delta with final usage
    delta_event = {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason},
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "server_tool_use": usage.get("server_tool_use"),
        },
    }
    yield f"event: message_delta\ndata: {json.dumps(delta_event)}\n\n"

    # 4. message_stop
    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"

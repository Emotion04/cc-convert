# SPDX-License-Identifier: Apache-2.0
"""Tests for the chat <-> Responses adapter used by Responses-only upstreams."""

import json

import pytest

from cc_convert.upstream_responses import (
    chat_body_to_responses_body,
    responses_payload_to_chat_completion,
    responses_sse_as_chat_sse,
)


async def _aiter(items):
    for item in items:
        yield item


class TestRequestConversion:
    def test_system_becomes_instructions(self):
        body = chat_body_to_responses_body(
            {
                "model": "m",
                "messages": [
                    {"role": "system", "content": "be terse"},
                    {"role": "user", "content": "hi"},
                ],
            }
        )
        assert body["instructions"] == "be terse"
        assert body["input"] == [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
        ]

    def test_multiple_system_messages_are_joined(self):
        body = chat_body_to_responses_body(
            {
                "messages": [
                    {"role": "system", "content": "a"},
                    {"role": "system", "content": "b"},
                    {"role": "user", "content": "q"},
                ]
            }
        )
        assert body["instructions"] == "a\n\nb"

    def test_assistant_text_and_tool_calls_become_items(self):
        body = chat_body_to_responses_body(
            {
                "messages": [
                    {"role": "user", "content": "q"},
                    {
                        "role": "assistant",
                        "content": "looking",
                        "reasoning_content": "think",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "f", "arguments": '{"a":1}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
                ]
            }
        )
        items = body["input"]
        assert items[1] == {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "think"}],
        }
        assert items[2] == {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "looking"}],
        }
        assert items[3] == {
            "type": "function_call",
            "call_id": "call_1",
            "name": "f",
            "arguments": '{"a":1}',
        }
        assert items[4] == {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "ok",
        }

    def test_images_keep_their_url(self):
        body = chat_body_to_responses_body(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "what"},
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://x/y.png"},
                            },
                        ],
                    }
                ]
            }
        )
        assert body["input"][0]["content"] == [
            {"type": "input_text", "text": "what"},
            {"type": "input_image", "image_url": "https://x/y.png"},
        ]

    def test_params_are_mapped(self):
        body = chat_body_to_responses_body(
            {
                "model": "m",
                "messages": [],
                "max_tokens": 64,
                "temperature": 0.2,
                "top_p": 0.9,
                "stop": ["x"],
                "repetition_penalty": 1.1,
                "chat_template_kwargs": {"reasoning_effort": "high"},
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "f",
                            "description": "d",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
                "tool_choice": {"type": "function", "function": {"name": "f"}},
                "parallel_tool_calls": False,
            }
        )
        assert body["max_output_tokens"] == 64
        assert body["temperature"] == 0.2
        assert body["top_p"] == 0.9
        assert "stop" not in body
        assert "repetition_penalty" not in body
        assert "chat_template_kwargs" not in body
        assert body["reasoning"] == {"effort": "high"}
        assert body["tools"] == [
            {"type": "function", "name": "f", "description": "d", "parameters": {"type": "object"}}
        ]
        assert body["tool_choice"] == {"type": "function", "name": "f"}
        assert body["parallel_tool_calls"] is False
        assert body["store"] is False

    def test_stream_flag_is_forwarded(self):
        body = chat_body_to_responses_body({"messages": [], "stream": True})
        assert body["stream"] is True


class TestResponseConversion:
    def test_text_and_usage(self):
        chat = responses_payload_to_chat_completion(
            {
                "id": "resp_1",
                "model": "m",
                "created_at": 123,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "hello"}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "total_tokens": 105,
                    "input_tokens_details": {"cached_tokens": 80},
                },
            }
        )
        assert chat["object"] == "chat.completion"
        assert chat["choices"][0]["message"]["content"] == "hello"
        assert chat["choices"][0]["finish_reason"] == "stop"
        assert chat["usage"]["prompt_tokens"] == 100
        assert chat["usage"]["completion_tokens"] == 5
        assert chat["usage"]["prompt_tokens_details"] == {"cached_tokens": 80}

    def test_function_calls_map_to_tool_calls(self):
        chat = responses_payload_to_chat_completion(
            {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_9",
                        "name": "f",
                        "arguments": '{"a":1}',
                    }
                ]
            }
        )
        message = chat["choices"][0]["message"]
        assert message["tool_calls"] == [
            {
                "id": "call_9",
                "type": "function",
                "function": {"name": "f", "arguments": '{"a":1}'},
            }
        ]
        assert chat["choices"][0]["finish_reason"] == "tool_calls"

    def test_reasoning_is_preserved(self):
        chat = responses_payload_to_chat_completion(
            {
                "output": [
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "because"}],
                    },
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    },
                ]
            }
        )
        message = chat["choices"][0]["message"]
        assert message["reasoning_content"] == "because"

    def test_incomplete_maps_to_length(self):
        chat = responses_payload_to_chat_completion(
            {"status": "incomplete", "output": []}
        )
        assert chat["choices"][0]["finish_reason"] == "length"


def _events(lines):
    return [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ") and line[6:].strip() != "[DONE]"
    ]


class TestStreamConversion:
    async def _convert(self, events):
        lines = []
        for event in events:
            lines.append(f"event: {event['type']}")
            lines.append("data: " + json.dumps(event))
            lines.append("")
        return [
            line async for line in responses_sse_as_chat_sse(_aiter(lines))
        ]

    @pytest.mark.asyncio
    async def test_text_deltas_become_content(self):
        out = await self._convert(
            [
                {
                    "type": "response.created",
                    "response": {"id": "resp_1", "model": "m", "created_at": 5},
                },
                {
                    "type": "response.output_text.delta",
                    "delta": "Hel",
                },
                {
                    "type": "response.output_text.delta",
                    "delta": "lo",
                },
                {
                    "type": "response.completed",
                    "response": {
                        "status": "completed",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 2,
                            "input_tokens_details": {"cached_tokens": 4},
                        },
                    },
                },
            ]
        )
        assert out[-1] == "data: [DONE]\n\n"
        chunks = _events(out)
        assert chunks[0]["id"] == "resp_1"
        text = "".join(
            c["choices"][0]["delta"].get("content", "")
            for c in chunks
            if c.get("choices")
        )
        assert text == "Hello"
        assert any(c.get("usage") for c in chunks)
        assert chunks[-1]["usage"]["prompt_tokens"] == 10

    @pytest.mark.asyncio
    async def test_function_call_deltas(self):
        out = await self._convert(
            [
                {"type": "response.created", "response": {"id": "r", "model": "m"}},
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "f",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "delta": '{"a"',
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "fc_1",
                    "delta": ":1}",
                },
                {"type": "response.completed", "response": {"status": "completed"}},
            ]
        )
        chunks = _events(out)
        tool_deltas = [
            c["choices"][0]["delta"]["tool_calls"][0]
            for c in chunks
            if c.get("choices") and c["choices"][0]["delta"].get("tool_calls")
        ]
        assert tool_deltas[0]["id"] == "call_1"
        assert tool_deltas[0]["function"]["name"] == "f"
        args = "".join(
            d.get("function", {}).get("arguments", "") for d in tool_deltas
        )
        assert args == '{"a":1}'
        finish = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c.get("choices") and c["choices"][0]["finish_reason"]
        ]
        assert finish == ["tool_calls"]

    @pytest.mark.asyncio
    async def test_missing_completed_event_still_closes(self):
        out = await self._convert(
            [
                {"type": "response.created", "response": {"id": "r", "model": "m"}},
                {"type": "response.output_text.delta", "delta": "x"},
            ]
        )
        assert out[-1] == "data: [DONE]\n\n"
        chunks = _events(out)
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


class TestStreamResponseIntegration:
    """A Responses upstream must still produce Anthropic SSE."""

    @pytest.mark.asyncio
    async def test_anthropic_stream_from_responses_upstream(self):
        from unittest.mock import MagicMock

        from cc_convert.streaming.handler import _stream_response

        events = [
            {
                "type": "response.created",
                "response": {"id": "resp_1", "model": "m", "created_at": 5},
            },
            {"type": "response.output_text.delta", "delta": "Hi"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 7, "output_tokens": 1},
                },
            },
        ]
        lines = []
        for event in events:
            lines.append(f"event: {event['type']}")
            lines.append("data: " + json.dumps(event))
            lines.append("")

        response = MagicMock()
        response.status_code = 200

        async def aiter_lines():
            for line in lines:
                yield line

        response.aiter_lines = aiter_lines

        class _Ctx:
            async def __aenter__(self):
                return response

            async def __aexit__(self, *args):
                return False

        client = MagicMock()
        client.stream = MagicMock(return_value=_Ctx())

        chat_body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        responses_body = chat_body_to_responses_body(chat_body)

        chunks = [
            chunk
            async for chunk in _stream_response(
                client,
                "http://up.test/v1/responses",
                {},
                chat_body,
                "m",
                upstream_body=responses_body,
                responses_upstream=True,
            )
        ]
        text = "".join(chunks)
        assert "message_start" in text
        assert "Hi" in text
        assert "message_stop" in text

        posted = client.stream.call_args
        assert posted.kwargs["json"]["input"][0]["role"] == "user"

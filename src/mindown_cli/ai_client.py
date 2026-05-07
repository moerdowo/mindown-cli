"""Thin OpenAI-compatible chat completions client (stdlib-only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class AIError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string from the model


@dataclass
class ChatChoice:
    content: str
    tool_calls: List[ToolCall]
    finish_reason: Optional[str]


class AIClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        timeout: float = 120.0,
    ) -> ChatChoice:
        url = f"{self.base_url}/chat/completions"
        body: Dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        data = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(e)
            first_line = detail.splitlines()[0] if detail else ""
            raise AIError(f"API error {e.code}: {first_line}") from None
        except urllib.error.URLError as e:
            raise AIError(f"network error: {e.reason}") from None

        choices = payload.get("choices") or []
        if not choices:
            raise AIError("API returned no choices")
        message = choices[0].get("message") or {}
        raw_calls = message.get("tool_calls") or []
        tool_calls: List[ToolCall] = []
        for c in raw_calls:
            fn = c.get("function") or {}
            tool_calls.append(
                ToolCall(
                    id=c.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=fn.get("arguments", "") or "{}",
                )
            )
        return ChatChoice(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
        )

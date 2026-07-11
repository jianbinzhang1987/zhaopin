import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from django.conf import settings


class LLMError(RuntimeError):
    pass


@dataclass
class LLMClient:
    api_key: str
    model: str
    base_url: str
    timeout: int = 60

    @classmethod
    def from_settings(cls) -> "LLMClient | None":
        api_key = getattr(settings, "LLM_API_KEY", "") or os.getenv("LLM_API_KEY", "")
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            model=getattr(settings, "LLM_MODEL", "gpt-4.1-mini"),
            base_url=getattr(settings, "LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            timeout=getattr(settings, "LLM_TIMEOUT_SECONDS", 60),
        )

    def json_completion(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"模型接口返回错误：{exc.code} {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"无法连接模型接口：{exc}") from exc

        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"模型响应格式不符合预期：{raw[:500]}") from exc
        return parse_json_content(content)


def parse_json_content(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise LLMError(f"模型没有返回JSON对象：{content[:500]}")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise LLMError("模型返回结果不是JSON对象")
    return parsed


def get_llm_client() -> LLMClient | None:
    return LLMClient.from_settings()


def with_ai_metadata(payload: dict[str, Any], source: str) -> dict[str, Any]:
    payload.setdefault("_ai", {})
    payload["_ai"].update({"source": source})
    return payload

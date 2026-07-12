import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from django.conf import settings

try:
    import certifi
except ImportError:  # pragma: no cover - certifi 为软依赖
    certifi = None


class LLMError(RuntimeError):
    pass


@dataclass
class LLMClient:
    api_key: str
    model: str
    base_url: str
    timeout: int = 60
    max_retries: int = 3

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
            max_retries=getattr(settings, "LLM_MAX_RETRIES", 3),
        )

    def json_completion(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        # 仅在模型接口支持时附上 response_format；当前上游不支持，故默认不发送。
        response_format = getattr(settings, "LLM_RESPONSE_FORMAT", None)
        if response_format:
            payload["response_format"] = response_format
        raw = self._post_chat_completion(payload)

        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"模型响应格式不符合预期：{raw[:500]}") from exc
        return parse_json_content(content)

    def _post_chat_completion(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, ensure_ascii=False)
        last_error: LLMError | None = None
        attempts = max(1, self.max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                return self._post_chat_completion_once(body)
            except LLMError as exc:
                if not str(exc).startswith("无法连接模型接口："):
                    raise
                last_error = exc
                if attempt >= attempts:
                    break
                time.sleep(min(0.5 * attempt, 2))
        message = str(last_error) if last_error else "无法连接模型接口：未知网络错误"
        raise LLMError(f"{message}（已重试 {attempts - 1} 次）") from last_error

    def _post_chat_completion_once(self, body: str) -> str:
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                # 部分 Cloudflare 网关会拦截默认 urllib UA，使用浏览器 UA 规避
                "User-Agent": "Mozilla/5.0 (compatible; SmartRecruitment/1.0)",
            },
            method="POST",
        )
        ssl_context = None
        if certifi is not None:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=ssl_context) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"模型接口返回错误：{exc.code} {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            return self._post_chat_completion_with_curl(body, exc)

    def _post_chat_completion_with_curl(self, body: str, original_exc: Exception) -> str:
        cmd = [
            "curl",
            "-sS",
            "--fail-with-body",
            "--max-time",
            str(self.timeout),
            "-H",
            f"Authorization: Bearer {self.api_key}",
            "-H",
            "Content-Type: application/json",
            "-H",
            "User-Agent: Mozilla/5.0 (compatible; SmartRecruitment/1.0)",
            "-d",
            body,
            f"{self.base_url}/chat/completions",
        ]
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=self.timeout + 5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LLMError(f"无法连接模型接口：{original_exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or str(original_exc)).strip()
            raise LLMError(f"无法连接模型接口：{detail[:500]}") from original_exc
        return result.stdout


def parse_json_content(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as original_exc:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise LLMError(f"模型没有返回JSON对象：{content[:500]}")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise LLMError(f"模型返回的JSON格式不合法：{content[:500]}") from original_exc
    if not isinstance(parsed, dict):
        raise LLMError("模型返回结果不是JSON对象")
    return parsed


def get_llm_client() -> LLMClient | None:
    return LLMClient.from_settings()


def with_ai_metadata(payload: dict[str, Any], source: str) -> dict[str, Any]:
    payload.setdefault("_ai", {})
    payload["_ai"].update({"source": source})
    return payload

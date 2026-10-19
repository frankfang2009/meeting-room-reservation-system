"""OpenAI 兼容的对话补全。第一家配置是 DeepSeek。

密钥只从进程环境变量 ``MRV2_MODEL_API_KEY`` 或数据目录 ``.model_api_key``
读取。调用方不得把密钥写入日志、异常文本、审计或返回给前端的对象。
本模块不读取预约数据库。测试应注入 :class:`FakeProvider`，不要访问网络。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MODEL_API_KEY_ENV = "MRV2_MODEL_API_KEY"
MODEL_API_KEY_FILENAME = ".model_api_key"
RESPONSE_MAX_BYTES = 64 * 1024
DEFAULT_BASE_URL = "https://api.deepseek.com"
MIN_TIMEOUT_SECONDS = 3
MAX_TIMEOUT_SECONDS = 30
_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{8,256}$")
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")
_KEY_FILE_MAX_BYTES = 4096


class ModelCallError(Exception):
    """供应商调用失败。``outcome`` 只有固定短码，不含地址、响应或密钥。"""

    def __init__(self, outcome: str) -> None:
        super().__init__(outcome)
        self.outcome = outcome

    def __str__(self) -> str:
        return self.outcome


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class ModelProvider(Protocol):
    name: str
    model: str

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        """返回模型消息的文本内容。失败时抛出 :class:`ModelCallError`。"""


def read_model_api_key(
    *,
    data_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """读取密钥。环境变量一旦出现就不再回落到文件，非法值视为缺失。"""

    source = os.environ if environ is None else environ
    if MODEL_API_KEY_ENV in source:
        return _normalize_key(source[MODEL_API_KEY_ENV])
    if data_dir is None:
        return None
    path = Path(data_dir) / MODEL_API_KEY_FILENAME
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    if len(blob) > _KEY_FILE_MAX_BYTES:
        return None
    try:
        text = blob.decode("utf-8")
    except UnicodeError:
        return None
    return _normalize_key(text)


def chat_completions_url(base_url: str) -> str:
    """把配置里的 HTTPS 根地址收成 ``/chat/completions``。

    只接受根路径或 ``/v1``。拒绝 http、用户信息、查询串和片段，避免把密钥
    放进 URL，也避免跳到别的路径。
    """

    if not isinstance(base_url, str) or not base_url.strip() or len(base_url) > 200:
        raise ValueError("模型地址无效")
    parts = urlsplit(base_url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("模型地址无效")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("模型地址无效")
    path = parts.path.rstrip("/")
    if path not in ("", "/v1"):
        raise ValueError("模型地址无效")
    endpoint_path = f"{path}/chat/completions" if path else "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, endpoint_path, "", ""))


class _RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ModelCallError("http_error")


def _opener():
    # 空的 ProxyHandler 会替换掉读取环境变量的默认代理，且不会注册任何
    # ``http_open``，因此请求不走 HTTP_PROXY / HTTPS_PROXY。
    return build_opener(ProxyHandler({}), _RefuseRedirect())


class DeepSeekProvider:
    """DeepSeek 的 OpenAI 兼容对话补全。``temperature`` 固定为 0。"""

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str,
        timeout_seconds: float,
    ) -> None:
        if not isinstance(api_key, str) or not _KEY_RE.fullmatch(api_key):
            raise ValueError("模型密钥无效")
        if not isinstance(model, str) or not _MODEL_NAME_RE.fullmatch(model):
            raise ValueError("模型名无效")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("超时无效")
        if not MIN_TIMEOUT_SECONDS <= float(timeout_seconds) <= MAX_TIMEOUT_SECONDS:
            raise ValueError("超时无效")
        self._api_key = api_key
        self._endpoint = chat_completions_url(base_url)
        self.model = model
        self.timeout_seconds = float(timeout_seconds)

    def __repr__(self) -> str:
        return f"DeepSeekProvider(model={self.model!r})"

    def __str__(self) -> str:
        return self.__repr__()

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        payload = _request_body(self.model, messages)
        request = Request(
            self._endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with _opener().open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(RESPONSE_MAX_BYTES + 1)
        except ModelCallError:
            raise
        except TimeoutError:
            raise ModelCallError("timeout") from None
        except HTTPError:
            raise ModelCallError("http_error") from None
        except URLError as error:
            if isinstance(getattr(error, "reason", None), TimeoutError):
                raise ModelCallError("timeout") from None
            raise ModelCallError("http_error") from None
        except OSError:
            raise ModelCallError("http_error") from None
        if len(raw) > RESPONSE_MAX_BYTES:
            raise ModelCallError("http_error")
        return _message_content(raw)


def open_configured_provider(
    *,
    enabled: bool,
    provider_name: str,
    base_url: str,
    model: str,
    timeout_seconds: int,
    data_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> DeepSeekProvider | None:
    """按配置构造 DeepSeek 供应商。缺密钥、地址或模型名时返回 ``None``。"""

    if enabled is not True or provider_name != "deepseek":
        return None
    if type(timeout_seconds) is not int:
        return None
    try:
        return DeepSeekProvider(
            api_key=read_model_api_key(data_dir=data_dir, environ=environ) or "",
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    except ValueError:
        return None


class FakeProvider:
    """测试替身。按脚本返回文本或抛出 :class:`ModelCallError`，不联网。"""

    def __init__(
        self,
        script: Sequence[str | ModelCallError],
        *,
        name: str = "fake",
        model: str = "fake-model",
    ) -> None:
        self._script = list(script)
        self.name = name
        self.model = model
        self.calls: list[tuple[ChatMessage, ...]] = []

    def complete(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(tuple(messages))
        if not self._script:
            raise ModelCallError("http_error")
        step = self._script.pop(0)
        if isinstance(step, ModelCallError):
            raise step
        if not isinstance(step, str):
            raise ModelCallError("invalid_json")
        return step


def guarded_complete(
    provider: ModelProvider,
    messages: Sequence[ChatMessage],
    *,
    encoded_text: str,
    allowed: Iterable[str],
) -> str:
    """发 HTTP 之前的最后一道闸门。用户消息必须就是已经过 I1 的编码文本。"""

    from .deid import OutboundBlocked, release_outbound

    release_outbound(encoded_text, allowed)
    user_messages = [message for message in messages if message.role == "user"]
    if len(user_messages) != 1 or user_messages[0].content != encoded_text:
        raise OutboundBlocked()
    return provider.complete(messages)


def _normalize_key(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not _KEY_RE.fullmatch(candidate):
        return None
    return candidate


def _request_body(model: str, messages: Sequence[ChatMessage]) -> bytes:
    body_messages: list[dict[str, str]] = []
    for message in messages:
        if message.role not in {"system", "user"} or not isinstance(message.content, str):
            raise ModelCallError("http_error")
        body_messages.append({"role": message.role, "content": message.content})
    if not body_messages:
        raise ModelCallError("http_error")
    body = {
        "model": model,
        "messages": body_messages,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _message_content(raw: bytes) -> str:
    try:
        parsed = json.loads(raw.decode("utf-8"))
        content = parsed["choices"][0]["message"]["content"]
    except (UnicodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise ModelCallError("invalid_json") from None
    if not isinstance(content, str) or not content.strip():
        raise ModelCallError("invalid_json")
    return content

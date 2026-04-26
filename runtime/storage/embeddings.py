from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
from typing import Protocol
import urllib.request


DEFAULT_HASH_EMBEDDING_DIM = 128
DEFAULT_HASH_EMBEDDING_MODEL = "token-sha256-signhash"
DEFAULT_HASH_EMBEDDING_VERSION = "1"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_EMBEDDING_VERSION = "1"
OPENAI_EMBEDDINGS_ENDPOINT = "/v1/embeddings"


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dim: int
    version: str
    deterministic: bool

    def embed_text(self, text: str) -> list[float]:
        ...


@dataclass(frozen=True)
class HashEmbeddingProvider:
    name: str = "hash"
    model: str = DEFAULT_HASH_EMBEDDING_MODEL
    dim: int = DEFAULT_HASH_EMBEDDING_DIM
    version: str = DEFAULT_HASH_EMBEDDING_VERSION
    deterministic: bool = True

    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = _tokenize(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:2], "big") % self.dim
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            weight = 1.0 + (len(token) / 24.0)
            vector[bucket] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [round(value / norm, 8) for value in vector]


@dataclass(frozen=True)
class OpenAIEmbeddingProvider:
    api_key: str
    name: str = "openai"
    model: str = DEFAULT_OPENAI_EMBEDDING_MODEL
    dim: int = DEFAULT_HASH_EMBEDDING_DIM
    version: str = DEFAULT_OPENAI_EMBEDDING_VERSION
    deterministic: bool = False
    base_url: str = "https://api.openai.com"
    timeout_seconds: float = 20.0

    def embed_text(self, text: str) -> list[float]:
        request_body: dict[str, object] = {
            "model": self.model,
            "input": text,
            "dimensions": self.dim,
        }
        request = urllib.request.Request(
            _join_url(self.base_url, OPENAI_EMBEDDINGS_ENDPOINT),
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        embedding = payload.get("data", [{}])[0].get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise ValueError("OpenAI embeddings response did not include a vector")
        return [float(value) for value in embedding]


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower())
    return [token for token in tokens if token.strip()]


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _positive_int_from_env(*names: str, default: int) -> int:
    for name in names:
        raw_value = os.environ.get(name)
        if raw_value is None:
            continue
        try:
            value = int(raw_value)
        except ValueError:
            continue
        if value > 0:
            return value
    return default


def _positive_float_from_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _openai_api_key() -> str | None:
    return os.environ.get("EXPCAP_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")


def _openai_provider() -> OpenAIEmbeddingProvider | None:
    api_key = _openai_api_key()
    if not api_key:
        return None
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        model=os.environ.get("EXPCAP_OPENAI_EMBEDDING_MODEL", DEFAULT_OPENAI_EMBEDDING_MODEL),
        dim=_positive_int_from_env(
            "EXPCAP_OPENAI_EMBEDDING_DIM",
            "EXPCAP_EMBEDDING_DIM",
            default=DEFAULT_HASH_EMBEDDING_DIM,
        ),
        base_url=os.environ.get("EXPCAP_OPENAI_BASE_URL", "https://api.openai.com"),
        timeout_seconds=_positive_float_from_env("EXPCAP_OPENAI_TIMEOUT_SECONDS", 20.0),
    )


def current_embedding_provider() -> EmbeddingProvider:
    requested_provider = os.environ.get("EXPCAP_EMBEDDING_PROVIDER", "hash").strip().lower() or "hash"
    if requested_provider in {"hash", "local-hash"}:
        return HashEmbeddingProvider()
    if requested_provider == "openai":
        provider = _openai_provider()
        if provider is not None:
            return provider
    return HashEmbeddingProvider()


def embedding_provider_config() -> dict[str, object]:
    requested_provider = os.environ.get("EXPCAP_EMBEDDING_PROVIDER", "hash").strip().lower() or "hash"
    provider = current_embedding_provider()
    fallback_reason = None
    status = "ready"
    if requested_provider == "openai" and provider.name != "openai":
        status = "fallback"
        fallback_reason = "missing_openai_api_key"
    elif requested_provider not in {"hash", "local-hash", "openai"}:
        status = "fallback"
        fallback_reason = f"unsupported_provider:{requested_provider}"
    return {
        "provider": provider.name,
        "requested_provider": requested_provider,
        "model": provider.model,
        "dim": provider.dim,
        "version": provider.version,
        "deterministic": provider.deterministic,
        "status": status,
        "fallback_reason": fallback_reason,
        "api_key_present": bool(_openai_api_key()) if requested_provider == "openai" else None,
    }


def embedding_metadata() -> dict[str, object]:
    config = embedding_provider_config()
    return {
        "embedding_provider": config["provider"],
        "embedding_requested_provider": config["requested_provider"],
        "embedding_model": config["model"],
        "embedding_dim": config["dim"],
        "embedding_version": config["version"],
        "embedding_status": config["status"],
    }


def embed_text(text: str) -> list[float]:
    return current_embedding_provider().embed_text(text)


def asset_embedding_text(asset: dict[str, object]) -> str:
    fragments = [
        asset.get("title", ""),
        asset.get("content", ""),
        asset.get("asset_type", ""),
        asset.get("knowledge_kind", ""),
        asset.get("knowledge_scope", ""),
    ]
    scope = asset.get("scope", {})
    if isinstance(scope, dict):
        fragments.append(scope.get("value", ""))
        fragments.append(scope.get("level", ""))
    return " ".join(str(fragment) for fragment in fragments if fragment)

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
import re
from typing import Protocol


DEFAULT_HASH_EMBEDDING_DIM = 128
DEFAULT_HASH_EMBEDDING_MODEL = "token-sha256-signhash"
DEFAULT_HASH_EMBEDDING_VERSION = "1"


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


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower())
    return [token for token in tokens if token.strip()]


def current_embedding_provider() -> EmbeddingProvider:
    requested_provider = os.environ.get("EXPCAP_EMBEDDING_PROVIDER", "hash").strip().lower() or "hash"
    if requested_provider in {"hash", "local-hash"}:
        return HashEmbeddingProvider()
    return HashEmbeddingProvider()


def embedding_provider_config() -> dict[str, object]:
    requested_provider = os.environ.get("EXPCAP_EMBEDDING_PROVIDER", "hash").strip().lower() or "hash"
    provider = current_embedding_provider()
    fallback_reason = None
    status = "ready"
    if requested_provider not in {"hash", "local-hash"}:
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

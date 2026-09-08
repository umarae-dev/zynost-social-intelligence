"""Shared adapter contract and isolation helpers (LLD-01, LLD-05, SEC-05–12)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence

from zynost_social.models import (
    AssetIdentity,
    ErrorClass,
    ProviderFetchOutcome,
    ProviderId,
    SocialMention,
)

T_PROV_S = 10.0
N_CONC = 4
BODY_MAX_BYTES = 1_048_576
TEXT_MAX_CHARS = 1024
MENTIONS_MAX = 128
RETRY_BACKOFF_S = 0.2

_logger = logging.getLogger("zynost_social.providers")

_KEEP_CONTROLS = frozenset("\t\n\r")


class ProviderError(Exception):
    """Adapter-signaled failure. Carries only a safe error_class (SEC-12)."""

    def __init__(self, error_class: ErrorClass) -> None:
        super().__init__(error_class)
        self.error_class = error_class


class TransientProviderError(Exception):
    """Retryable transport / 5xx. Isolation maps exhausted retries to timeout."""


class SocialProvider(ABC):
    @property
    @abstractmethod
    def provider(self) -> ProviderId:
        """Stable source id on ProviderFetchOutcome (MOD-05)."""

    @property
    def credential_names(self) -> tuple[str, ...]:
        """Env names that must all be present before any network call (CFG-03). Empty = no gate."""
        return ()

    @abstractmethod
    async def fetch_mentions(
        self,
        asset: AssetIdentity,
        since_minutes: int,
    ) -> list[SocialMention]:
        """Normalized mentions only. [] means success with zero mentions (LLD-02)."""


def credentials_present(names: tuple[str, ...]) -> bool:
    """True iff every name is set and strip() is non-empty. Never logs values (SEC-03)."""
    if not names:
        return True
    for name in names:
        value = os.environ.get(name)
        if value is None or not value.strip():
            return False
    return True


def sanitize_text(raw: str | bytes) -> str:
    """Untrusted mention text: UTF-8 replace, strip controls, cap length (SEC-09)."""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    cleaned: list[str] = []
    for char in text:
        code = ord(char)
        if char in _KEEP_CONTROLS or (code > 0x1F and not (0x7F <= code <= 0x9F)):
            cleaned.append(char)
    text = "".join(cleaned).replace("\r\n", "\n").replace("\r", "\n")
    return text[:TEXT_MAX_CHARS]


def hash_identifier(preimage: str) -> str:
    """SHA-256 lowercase hex of UTF-8 preimage (SEC-11). Do not hash empty URLs."""
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def hash_url(preimage: str | None) -> str | None:
    if preimage is None or not preimage.strip():
        return None
    return hash_identifier(preimage)


def validate_body_size(body: bytes) -> None:
    if len(body) > BODY_MAX_BYTES:
        raise ProviderError("malformed")


def cap_mentions(mentions: Sequence[SocialMention]) -> tuple[SocialMention, ...]:
    newest_first = sorted(mentions, key=lambda mention: mention.created_at, reverse=True)
    return tuple(newest_first[:MENTIONS_MAX])


def redact_for_log(kind: str, provider: str) -> None:
    """Log provider + safe class only. Never attach exceptions or bodies (SEC-12)."""
    _logger.info("provider_unavailable provider=%s error_class=%s", provider, kind)


def _unavailable(provider: ProviderId, error_class: ErrorClass) -> ProviderFetchOutcome:
    redact_for_log(error_class, provider)
    return ProviderFetchOutcome(
        provider=provider,
        status="unavailable",
        mentions=(),
        error_class=error_class,
        freshness_seconds=None,
    )


def _available(provider: ProviderId, mentions: Sequence[SocialMention]) -> ProviderFetchOutcome:
    capped = cap_mentions(mentions)
    return ProviderFetchOutcome(
        provider=provider,
        status="available",
        mentions=capped,
        error_class=None,
        freshness_seconds=0.0,
    )


def _normalize_mentions(raw: object, provider: ProviderId) -> list[SocialMention]:
    if not isinstance(raw, list):
        raise ProviderError("malformed")
    mentions: list[SocialMention] = []
    for item in raw:
        if not isinstance(item, SocialMention):
            raise ProviderError("malformed")
        if item.provider != provider:
            raise ProviderError("malformed")
        mentions.append(item)
    return mentions


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (TimeoutError, OSError, TransientProviderError))


async def collect_isolated(
    provider: SocialProvider,
    asset: AssetIdentity,
    since_minutes: int,
    timeout_s: float = T_PROV_S,
) -> ProviderFetchOutcome:
    """Run fetch_mentions under timeout; never raise into the engine (PRV-03)."""
    provider_id = provider.provider
    if not credentials_present(provider.credential_names):
        return _unavailable(provider_id, "not_configured")

    deadline = time.monotonic() + max(timeout_s, 0.0)
    retried = False

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _unavailable(provider_id, "timeout")
        try:
            raw = await asyncio.wait_for(
                provider.fetch_mentions(asset, since_minutes),
                timeout=remaining,
            )
            mentions = _normalize_mentions(raw, provider_id)
            return _available(provider_id, mentions)
        except ProviderError as exc:
            return _unavailable(provider_id, exc.error_class)
        except Exception as exc:
            if _is_retryable(exc) and not retried:
                retried = True
                wait = min(RETRY_BACKOFF_S, max(0.0, deadline - time.monotonic()))
                if wait <= 0:
                    return _unavailable(provider_id, "timeout")
                await asyncio.sleep(wait)
                continue
            if _is_retryable(exc):
                return _unavailable(provider_id, "timeout")
            return _unavailable(provider_id, "malformed")

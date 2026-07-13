"""Competition seed derivation — server-side only.

Hidden/holdout seed VALUES must never appear in an API response; only labels
("hidden-1") and counts are public. Values derive from the app secret via HMAC,
so they need no storage, cannot drift between the API and the worker process,
and rotate wholesale when the ruleset's round token changes.
"""

from __future__ import annotations

import hashlib
import hmac

from eflux.config import get_settings

SEED_KINDS = ("practice", "hidden", "holdout")
DEFAULT_ROUND = "round-1"


def derive_seed(slug: str, kind: str, index: int, round_token: str = DEFAULT_ROUND) -> int:
    """Deterministic per-(competition, round, kind, index) RNG seed, 1-based index."""
    if kind not in SEED_KINDS:
        raise ValueError(f"unknown seed kind: {kind!r}")
    if index < 1:
        raise ValueError("seed index is 1-based")
    message = f"{slug}:{round_token}:{kind}:{index}".encode()
    if kind == "practice":
        digest = hashlib.sha256(b"eflux-public-practice:" + message).digest()
    else:
        settings = get_settings()
        key = settings.evaluation_seed_key.strip()
        if not key:
            if settings.env.lower() not in {"dev", "development", "test"}:
                raise RuntimeError("EFLUX_EVALUATION_SEED_KEY is required outside development")
            key = settings.secret_key
        digest = hmac.new(key.encode(), message, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % (2**31 - 1)


def seed_values(slug: str, kind: str, count: int, round_token: str = DEFAULT_ROUND) -> list[int]:
    return [derive_seed(slug, kind, i, round_token) for i in range(1, count + 1)]


def seed_labels(kind: str, count: int) -> list[str]:
    """The only seed identifiers that may leave the server for hidden/holdout kinds."""
    if kind not in SEED_KINDS:
        raise ValueError(f"unknown seed kind: {kind!r}")
    return [f"{kind}-{i}" for i in range(1, count + 1)]

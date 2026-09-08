"""Asset matching. Algorithms and weights: docs/07 (MAT-01–MAT-10, QLY-03 scale)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

from zynost_social.models import AssetIdentity, MatchBatch, MetadataValue, SocialMention

# Collision-prone tickers (FR-ID-03). Symbol/alias hits in this set never match alone.
COLLISION_SYMBOLS = frozenset({"ONE", "IN", "ARB", "TON", "OP", "NEAR", "APT", "LINK"})

# Generic crypto/market chatter. One of these is never enough corroboration for COLLISION_SYMBOLS.
GENERIC_CRYPTO_TERMS = frozenset(
    {
        "crypto",
        "cryptocurrency",
        "cryptocurrencies",
        "token",
        "tokens",
        "coin",
        "coins",
        "blockchain",
        "defi",
        "nft",
        "nfts",
        "altcoin",
        "altcoins",
        "memecoin",
        "memecoins",
        "hodl",
        "wallet",
        "wallets",
        "onchain",
        "cex",
        "dex",
        "airdrop",
        "staking",
        "yield",
        "gas",
        "mainnet",
        "testnet",
        "layer2",
        "web3",
        "dao",
        "market",
        "markets",
        "trading",
        "trader",
        "traders",
        "volume",
        "liquidity",
        "oracle",
        "oracles",
        "airdrops",
        "listing",
        "listings",
    }
)

# Per-mention match_confidence (MAT-07). Take the max class that fired; never sum.
CONF_CONTRACT = 0.95
CONF_OFFICIAL = 0.85
CONF_NAME = 0.80
CONF_ALIAS = 0.70
CONF_SYMBOL_PROJECT_CONTEXT = 0.60
CONF_COLLISION_MULTI_CONTEXT = 0.55
CONF_SYMBOL_GENERIC_CONTEXT = 0.45

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_HEX_ADDR_RE = re.compile(r"0x[a-fA-F0-9]{8,}")
_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")
_DISCORD_CHANNEL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?discord\.com/channels/(\d{17,20})/(\d{17,20})",
    re.IGNORECASE,
)


def match_mentions(
    asset: AssetIdentity,
    mentions: Sequence[SocialMention],
) -> MatchBatch:
    """Partition mentions into asset-specific vs market-wide leftovers (MAT-05, MAT-06)."""
    asset_mentions: list[SocialMention] = []
    market_wide: list[SocialMention] = []
    for mention in mentions:
        classified = _classify_mention(asset, mention)
        if classified is None:
            if _looks_like_crypto_chatter(mention.text):
                market_wide.append(_clear_match_evidence(mention))
            continue
        asset_mentions.append(classified)
    return MatchBatch(
        asset_mentions=tuple(asset_mentions),
        market_wide=tuple(market_wide),
        asset_match_score=_asset_match_score(asset_mentions),
    )


def _asset_match_score(asset_mentions: Sequence[SocialMention]) -> float:
    """QLY-03: mean match_confidence, or 0 when N_a = 0."""
    if not asset_mentions:
        return 0.0
    total = sum(item.match_confidence for item in asset_mentions)
    return round(total / len(asset_mentions), 6)


def _clear_match_evidence(mention: SocialMention) -> SocialMention:
    return replace(mention, matched_terms=(), match_confidence=0.0, match_rationale="")


def _classify_mention(asset: AssetIdentity, mention: SocialMention) -> SocialMention | None:
    if mention.asset_id != asset.asset_id:
        return None
    hits = _collect_hits(asset, mention)
    decision = _gate(asset, hits)
    if decision is None:
        return None
    confidence, rationale = decision
    terms = tuple(hits.terms)
    if not terms or not rationale:
        return None
    return replace(
        mention,
        matched_terms=terms,
        match_confidence=confidence,
        match_rationale=rationale,
    )


class _Hits:
    __slots__ = (
        "terms",
        "has_contract",
        "has_official",
        "has_name",
        "has_alias",
        "has_symbol",
        "has_chain",
        "project_context_terms",
        "generic_context_terms",
    )

    def __init__(self) -> None:
        self.terms: list[str] = []
        self.has_contract = False
        self.has_official = False
        self.has_name = False
        self.has_alias = False
        self.has_symbol = False
        self.has_chain = False
        self.project_context_terms: list[str] = []
        self.generic_context_terms: list[str] = []

    def add_term(self, term: str) -> None:
        if term and term not in self.terms:
            self.terms.append(term)


def _collect_hits(asset: AssetIdentity, mention: SocialMention) -> _Hits:
    hits = _Hits()
    text = mention.text
    folded = text.casefold()
    tokens = {token.casefold() for token in _TOKEN_RE.findall(text)}

    contract = asset.contract_address
    if contract and _contract_in_text(contract, text):
        hits.has_contract = True
        hits.add_term(contract)

    for account in _official_account_hits(asset, mention):
        hits.has_official = True
        hits.add_term(account)

    if _phrase_in_text(asset.name, text):
        hits.has_name = True
        hits.add_term(asset.name)

    collision_asset = _is_collision_token(asset.symbol)
    for alias in asset.aliases:
        if not alias.strip():
            continue
        if not _phrase_in_text(alias, text):
            continue
        alias_is_ticker = alias.strip().casefold() == asset.symbol.casefold()
        if _is_collision_token(alias) or (collision_asset and alias_is_ticker):
            hits.has_symbol = True
            hits.add_term(alias)
            continue
        hits.has_alias = True
        hits.add_term(alias)

    if _symbol_in_text(asset.symbol, text):
        hits.has_symbol = True
        hits.add_term(asset.symbol)

    chain = asset.chain
    if chain and _phrase_in_text(chain, text):
        hits.has_chain = True
        hits.add_term(chain)

    project_terms = _project_terms(asset)
    for term in project_terms:
        if term.casefold() in tokens or _phrase_in_text(term, text):
            hits.project_context_terms.append(term)
            hits.add_term(term)

    for token in _TOKEN_RE.findall(text):
        folded_token = token.casefold()
        if folded_token in GENERIC_CRYPTO_TERMS:
            hits.generic_context_terms.append(folded_token)
            hits.add_term(folded_token)
        if folded_token.replace("-", "") == "onchain" or folded_token == "on-chain":
            hits.generic_context_terms.append("onchain")
            hits.add_term("onchain")

    if "on-chain" in folded or "onchain" in folded:
        if "onchain" not in hits.generic_context_terms:
            hits.generic_context_terms.append("onchain")
            hits.add_term("onchain")

    # Deduplicate context lists while preserving first-seen order.
    hits.project_context_terms = list(dict.fromkeys(hits.project_context_terms))
    hits.generic_context_terms = list(dict.fromkeys(hits.generic_context_terms))
    return hits


def _gate(asset: AssetIdentity, hits: _Hits) -> tuple[float, str] | None:
    """Return confidence + rationale, or None if the mention is not asset-specific."""
    collision = _is_collision_token(asset.symbol)
    rationale_parts: list[str] = []
    confidence = 0.0

    if hits.has_contract:
        confidence = max(confidence, CONF_CONTRACT)
        rationale_parts.append("contract address exact match")
    if hits.has_official:
        confidence = max(confidence, CONF_OFFICIAL)
        rationale_parts.append("official account reference")
    if hits.has_name:
        confidence = max(confidence, CONF_NAME)
        rationale_parts.append("official name phrase")
    if hits.has_alias:
        confidence = max(confidence, CONF_ALIAS)
        rationale_parts.append("alias phrase")

    strong = hits.has_contract or hits.has_official or hits.has_name or hits.has_alias
    project_n = len(hits.project_context_terms)
    generic_n = len(hits.generic_context_terms)
    independent_context = (1 if hits.has_chain else 0) + project_n + generic_n

    if collision:
        # MAT-03: never symbol-alone or a single generic crypto word.
        if strong:
            return confidence, _join_rationale(rationale_parts)
        multi_non_generic = (1 if hits.has_chain else 0) + project_n
        if hits.has_symbol and multi_non_generic >= 1 and independent_context >= 2:
            rationale_parts.append("collision symbol with multiple context terms")
            if hits.has_chain:
                rationale_parts.append("chain context")
            if project_n:
                rationale_parts.append("project terminology")
            return CONF_COLLISION_MULTI_CONTEXT, _join_rationale(rationale_parts)
        return None

    # Non-collision: MAT-02 — symbol needs one corroborating signal.
    if strong:
        return confidence, _join_rationale(rationale_parts)
    if not hits.has_symbol:
        return None
    if hits.has_chain or project_n >= 1:
        rationale_parts.append("symbol with corroborating context")
        if hits.has_chain:
            rationale_parts.append("chain context")
        if project_n:
            rationale_parts.append("project terminology")
        return CONF_SYMBOL_PROJECT_CONTEXT, _join_rationale(rationale_parts)
    if generic_n >= 1:
        rationale_parts.append("symbol with corroborating context")
        return CONF_SYMBOL_GENERIC_CONTEXT, _join_rationale(rationale_parts)
    return None


def _join_rationale(parts: Sequence[str]) -> str:
    return " + ".join(dict.fromkeys(parts))


def _is_collision_token(value: str) -> bool:
    return value.strip().upper() in COLLISION_SYMBOLS


def _phrase_in_text(phrase: str, text: str) -> bool:
    stripped = phrase.strip()
    if not stripped:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(stripped) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _symbol_in_text(symbol: str, text: str) -> bool:
    stripped = symbol.strip()
    if not stripped:
        return False
    escaped = re.escape(stripped)
    pattern = rf"(?<![A-Za-z0-9])[${{}}#]?{escaped}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _contract_in_text(contract: str, text: str) -> bool:
    needle = contract.strip()
    if not needle:
        return False
    folded_text = text.casefold()
    folded_needle = needle.casefold()
    if folded_needle in folded_text:
        return True
    compact_text = folded_text.replace(" ", "")
    compact_needle = folded_needle.replace(" ", "")
    if compact_needle in compact_text:
        return True
    found = {item.casefold() for item in _HEX_ADDR_RE.findall(text)}
    return folded_needle in found or compact_needle in found


def _project_terms(asset: AssetIdentity) -> tuple[str, ...]:
    """Tokens from name/aliases that are not the ticker and not collision words (MAT-09)."""
    collected: list[str] = []
    symbol_folded = asset.symbol.casefold()
    for source in (asset.name, *asset.aliases):
        for token in _TOKEN_RE.findall(source):
            if len(token) < 4:
                continue
            if token.casefold() == symbol_folded or _is_collision_token(token):
                continue
            if token.casefold() in GENERIC_CRYPTO_TERMS:
                continue
            collected.append(token)
    return tuple(dict.fromkeys(collected))


def _normalize_account(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    discord = _DISCORD_CHANNEL_RE.search(value)
    if discord is not None:
        return discord.group(2)
    lowered = value.casefold()
    for prefix in (
        "https://x.com/",
        "http://x.com/",
        "https://twitter.com/",
        "http://twitter.com/",
        "https://www.reddit.com/r/",
        "https://reddit.com/r/",
        "https://t.me/s/",
        "https://t.me/",
        "http://t.me/s/",
        "http://t.me/",
        "t.me/s/",
        "t.me/",
        "/r/",
        "r/",
        "/u/",
        "u/",
        "user/",
    ):
        if lowered.startswith(prefix):
            value = value[len(prefix) :]
            lowered = value.casefold()
            break
    value = value.lstrip("@").strip().strip("/")
    if "/" in value:
        value = value.split("/", 1)[0]
    return value.casefold()


def _official_account_hits(asset: AssetIdentity, mention: SocialMention) -> list[str]:
    identities: list[tuple[str, str]] = []
    for raw in asset.official_x_accounts:
        identities.append((_normalize_account(raw), raw.strip()))
    for raw in asset.official_reddit:
        identities.append((_normalize_account(raw), raw.strip()))
    for raw in asset.official_telegram:
        identities.append((_normalize_account(raw), raw.strip()))
    for raw in asset.official_discord:
        identities.append((_normalize_account(raw), raw.strip()))
    identities = [(norm, raw) for norm, raw in identities if norm]

    hits: list[str] = []
    text = mention.text
    for norm, raw in identities:
        if _account_in_text(norm, text):
            hits.append(raw)
    meta_hits = _official_metadata_hits(identities, mention.metadata)
    for item in meta_hits:
        if item not in hits:
            hits.append(item)
    return hits


def _account_in_text(norm: str, text: str) -> bool:
    if not norm:
        return False
    escaped = re.escape(norm)
    patterns = (
        rf"(?<![A-Za-z0-9])@{escaped}(?![A-Za-z0-9])",
        rf"(?<![A-Za-z0-9])r/{escaped}(?![A-Za-z0-9])",
        rf"(?<![A-Za-z0-9])/r/{escaped}(?![A-Za-z0-9])",
        rf"t\.me/(?:s/)?{escaped}(?![A-Za-z0-9])",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns) or (
        _SNOWFLAKE_RE.match(norm) is not None and _phrase_in_text(norm, text)
    )


def _official_metadata_hits(
    identities: Sequence[tuple[str, str]],
    metadata: Mapping[str, MetadataValue],
) -> list[str]:
    norms = {norm: raw for norm, raw in identities}
    hits: list[str] = []
    for key in ("subreddit", "channel", "channel_id", "guild_id"):
        raw_value = metadata.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        norm = _normalize_account(raw_value)
        if norm in norms:
            hits.append(norms[norm])
    return hits


def _looks_like_crypto_chatter(text: str) -> bool:
    tokens = {token.casefold() for token in _TOKEN_RE.findall(text)}
    if tokens & GENERIC_CRYPTO_TERMS:
        return True
    if "on-chain" in text.casefold() or "onchain" in tokens:
        return True
    if re.search(r"(?<![A-Za-z0-9])[$#][A-Za-z]{2,10}(?![A-Za-z0-9])", text):
        return True
    return False

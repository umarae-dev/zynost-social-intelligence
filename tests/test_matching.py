"""Matching partition and evidence (docs/07, TST-M-01–TST-M-08)."""

from datetime import UTC, datetime

from zynost_social.matching import match_mentions
from zynost_social.models import AssetIdentity, ProviderId, SocialMention

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

HARMONY = AssetIdentity(
    asset_id="harmony-one",
    symbol="ONE",
    name="Harmony",
    aliases=("Harmony ONE",),
    chain="Harmony",
    contract_address="0x799a4202c12ca952cb311598a809e697009daa21",
    official_x_accounts=("@harmonyprotocol",),
)

BTC = AssetIdentity(
    asset_id="btc",
    symbol="BTC",
    name="Bitcoin",
    aliases=("bitcoin",),
    official_x_accounts=("@bitcoin",),
)

ARB = AssetIdentity(
    asset_id="arbitrum",
    symbol="ARB",
    name="Arbitrum",
    aliases=("Arbitrum One",),
    chain="Arbitrum",
)

LINK = AssetIdentity(
    asset_id="chainlink",
    symbol="LINK",
    name="Chainlink",
    aliases=("chainlink",),
    official_reddit=("r/chainlink",),
)

OP = AssetIdentity(
    asset_id="optimism",
    symbol="OP",
    name="Optimism",
    official_telegram=("optimism",),
)

NEAR = AssetIdentity(
    asset_id="near",
    symbol="NEAR",
    name="Near Protocol",
    chain="NEAR",
)

APT = AssetIdentity(
    asset_id="aptos",
    symbol="APT",
    name="Aptos",
    official_discord=("123456789012345678",),
)

TON = AssetIdentity(
    asset_id="toncoin",
    symbol="TON",
    name="Toncoin",
    official_telegram=("toncoin",),
)

IN_TOKEN = AssetIdentity(
    asset_id="in-token",
    symbol="IN",
    name="Inverse Finance",
    aliases=("Inverse",),
)


def _mention(
    text: str,
    *,
    asset_id: str,
    provider: ProviderId = "x",
    external_id: str = "1",
    metadata: dict[str, str] | None = None,
) -> SocialMention:
    return SocialMention(
        provider=provider,
        external_id=external_id,
        asset_id=asset_id,
        text=text,
        author_id_hash="abc",
        created_at=NOW,
        engagement=0.0,
        metadata=metadata or {},
    )


def test_ticker_only_english_one_is_not_asset_specific() -> None:
    batch = match_mentions(
        HARMONY,
        [_mention("one of the best days this week", asset_id="harmony-one")],
    )
    assert batch.asset_mentions == ()
    assert batch.market_wide == ()
    assert batch.asset_match_score == 0.0


def test_collision_symbol_plus_one_generic_crypto_word_is_rejected() -> None:
    batch = match_mentions(
        HARMONY,
        [_mention("ONE token looks interesting", asset_id="harmony-one")],
    )
    assert batch.asset_mentions == ()
    assert len(batch.market_wide) == 1


def test_collision_multi_context_without_name_still_accepts() -> None:
    asset = AssetIdentity(
        asset_id="harmony-one",
        symbol="ONE",
        name="Harmony",
        chain="Pangaea",
    )
    mention = _mention("ONE Pangaea mainnet volume", asset_id="harmony-one")
    batch = match_mentions(asset, [mention])
    assert len(batch.asset_mentions) == 1
    assert "multiple context" in batch.asset_mentions[0].match_rationale


def test_collision_name_contract_official_and_multi_context_accept() -> None:
    named = _mention("Harmony ONE listing today", asset_id="harmony-one", external_id="n")
    contracted = _mention(
        f"check {HARMONY.contract_address} on explorers",
        asset_id="harmony-one",
        external_id="c",
    )
    official = _mention(
        "update from @harmonyprotocol",
        asset_id="harmony-one",
        external_id="o",
    )
    multi = _mention(
        "ONE Harmony mainnet volume",
        asset_id="harmony-one",
        external_id="m",
    )
    batch = match_mentions(HARMONY, [named, contracted, official, multi])
    assert len(batch.asset_mentions) == 4
    ids = {item.external_id for item in batch.asset_mentions}
    assert ids == {"n", "c", "o", "m"}
    by_id = {item.external_id: item for item in batch.asset_mentions}
    assert by_id["c"].match_confidence >= 0.9
    assert "contract" in by_id["c"].match_rationale
    assert "Harmony" in by_id["n"].matched_terms
    assert by_id["o"].match_confidence >= 0.8


def test_alias_phrase_with_corroboration_is_asset_specific() -> None:
    mention = _mention("Harmony ONE holders discussing upgrades", asset_id="harmony-one")
    batch = match_mentions(HARMONY, [mention])
    assert len(batch.asset_mentions) == 1
    matched = batch.asset_mentions[0]
    assert any("Harmony" in term for term in matched.matched_terms)
    assert matched.match_rationale
    assert 0.0 < matched.match_confidence <= 1.0


def test_contract_address_exact_is_high_confidence() -> None:
    mention = _mention(
        "lp 0x799a4202c12ca952cb311598a809e697009daa21",
        asset_id="harmony-one",
    )
    batch = match_mentions(HARMONY, [mention])
    assert len(batch.asset_mentions) == 1
    assert batch.asset_mentions[0].match_confidence == 0.95
    assert HARMONY.contract_address in batch.asset_mentions[0].matched_terms


def test_official_account_from_text_and_metadata() -> None:
    x_ref = _mention("gm from @bitcoin", asset_id="btc", external_id="x")
    reddit = _mention(
        "weekly recap",
        asset_id="chainlink",
        provider="reddit",
        external_id="r",
        metadata={"subreddit": "chainlink"},
    )
    telegram = _mention(
        "upgrade notes",
        asset_id="optimism",
        provider="telegram",
        external_id="t",
        metadata={"channel": "optimism"},
    )
    discord = _mention(
        "gm",
        asset_id="aptos",
        provider="discord",
        external_id="d",
        metadata={"channel_id": "123456789012345678"},
    )
    batch_x = match_mentions(BTC, [x_ref])
    batch_r = match_mentions(LINK, [reddit])
    batch_t = match_mentions(OP, [telegram])
    batch_d = match_mentions(APT, [discord])
    assert len(batch_x.asset_mentions) == 1
    assert len(batch_r.asset_mentions) == 1
    assert len(batch_t.asset_mentions) == 1
    assert len(batch_d.asset_mentions) == 1
    assert "official account" in batch_r.asset_mentions[0].match_rationale


def test_market_wide_leftovers_are_not_asset_mentions() -> None:
    asset_hit = _mention("Bitcoin hash rate discussion", asset_id="btc", external_id="a")
    chatter = _mention("the crypto market dumped hard", asset_id="btc", external_id="w")
    off_topic = _mention("I baked bread this morning", asset_id="btc", external_id="z")
    batch = match_mentions(BTC, [asset_hit, chatter, off_topic])
    assert [item.external_id for item in batch.asset_mentions] == ["a"]
    assert [item.external_id for item in batch.market_wide] == ["w"]
    assert all(item.match_confidence == 0.0 for item in batch.market_wide)
    assert all(item.matched_terms == () for item in batch.market_wide)


def test_every_asset_mention_has_evidence_fields() -> None:
    mentions = [
        _mention("Bitcoin ETF inflows", asset_id="btc", external_id="1"),
        _mention("$BTC crypto volume", asset_id="btc", external_id="2"),
    ]
    batch = match_mentions(BTC, mentions)
    assert len(batch.asset_mentions) == 2
    for item in batch.asset_mentions:
        assert item.matched_terms
        assert 0.0 < item.match_confidence <= 1.0
        assert item.match_rationale.strip()


def test_non_collision_bare_ticker_without_context_rejected() -> None:
    batch = match_mentions(BTC, [_mention("see BTC later", asset_id="btc")])
    assert batch.asset_mentions == ()


def test_non_collision_symbol_plus_generic_crypto_accepted() -> None:
    batch = match_mentions(BTC, [_mention("$BTC crypto volume", asset_id="btc")])
    assert len(batch.asset_mentions) == 1
    assert batch.asset_mentions[0].match_confidence == 0.45


def test_link_as_english_word_is_rejected() -> None:
    batch = match_mentions(LINK, [_mention("click this link below", asset_id="chainlink")])
    assert batch.asset_mentions == ()


def test_chainlink_name_accepts() -> None:
    batch = match_mentions(LINK, [_mention("Chainlink LINK oracle feeds", asset_id="chainlink")])
    assert len(batch.asset_mentions) == 1


def test_op_slang_rejected_optimism_name_accepted() -> None:
    slang = match_mentions(
        OP,
        [_mention("that character is OP and overpowered", asset_id="optimism")],
    )
    named = match_mentions(OP, [_mention("Optimism OP airdrop window", asset_id="optimism")])
    assert slang.asset_mentions == ()
    assert len(named.asset_mentions) == 1


def test_arb_generic_trading_chat_rejected() -> None:
    batch = match_mentions(
        ARB,
        [_mention("nice arb opportunity in crypto", asset_id="arbitrum")],
    )
    assert batch.asset_mentions == ()
    assert len(batch.market_wide) == 1


def test_arbitrum_name_accepts() -> None:
    batch = match_mentions(ARB, [_mention("Arbitrum ARB staking", asset_id="arbitrum")])
    assert len(batch.asset_mentions) == 1


def test_near_preposition_rejected_name_accepted() -> None:
    bare = match_mentions(NEAR, [_mention("the shop is near the station", asset_id="near")])
    named = match_mentions(NEAR, [_mention("Near Protocol gas fees", asset_id="near")])
    assert bare.asset_mentions == ()
    assert len(named.asset_mentions) == 1


def test_ton_quantity_phrase_rejected() -> None:
    batch = match_mentions(TON, [_mention("a ton of work left", asset_id="toncoin")])
    assert batch.asset_mentions == ()


def test_ton_official_channel_metadata_accepts() -> None:
    mention = _mention(
        "weekly update",
        asset_id="toncoin",
        provider="telegram",
        metadata={"channel": "toncoin"},
    )
    batch = match_mentions(TON, [mention])
    assert len(batch.asset_mentions) == 1


def test_in_preposition_rejected_inverse_name_accepted() -> None:
    bare = match_mentions(IN_TOKEN, [_mention("put funds in the pool", asset_id="in-token")])
    named = match_mentions(IN_TOKEN, [_mention("Inverse Finance proposal", asset_id="in-token")])
    assert bare.asset_mentions == ()
    assert len(named.asset_mentions) == 1


def test_wrong_asset_id_is_not_matched() -> None:
    batch = match_mentions(BTC, [_mention("Bitcoin rally", asset_id="eth")])
    assert batch.asset_mentions == ()


def test_does_not_invent_missing_identity_fields() -> None:
    sparse = AssetIdentity(asset_id="mystery", symbol="ONE", name="MysteryOne")
    batch = match_mentions(
        sparse,
        [_mention("ONE token crypto coin listing", asset_id="mystery")],
    )
    assert batch.asset_mentions == ()


def test_asset_match_score_is_mean_confidence() -> None:
    mentions = [
        _mention("Bitcoin ETF inflows", asset_id="btc", external_id="1"),
        _mention("$BTC crypto volume", asset_id="btc", external_id="2"),
    ]
    batch = match_mentions(BTC, mentions)
    expected = round(sum(item.match_confidence for item in batch.asset_mentions) / 2, 6)
    assert batch.asset_match_score == expected


def test_matching_is_deterministic() -> None:
    mentions = [
        _mention("Harmony ONE listing today", asset_id="harmony-one", external_id="1"),
        _mention("ONE token looks interesting", asset_id="harmony-one", external_id="2"),
        _mention("click this link below", asset_id="harmony-one", external_id="3"),
    ]
    first = match_mentions(HARMONY, mentions)
    second = match_mentions(HARMONY, mentions)
    assert first == second

from __future__ import annotations

import hashlib
import json

import pytest

from eflux.ecosystem.catalog import (
    get_standard_profile,
    list_builtin_population_packs,
    list_standard_profiles,
)


def _canonical_sha256(value: dict) -> str:
    content = {key: item for key, item in value.items() if key != "content_sha256"}
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_standard_profiles_are_complete_unique_and_json_serializable() -> None:
    profiles = list_standard_profiles()

    assert len(profiles) == 5
    assert {profile["id"] for profile in profiles} == {
        "battery-only",
        "residential-pv-battery",
        "commercial-load-battery",
        "industrial-flexible-load",
        "renewable-generator",
    }
    assert len({profile["content_sha256"] for profile in profiles}) == 5
    assert all(profile["version"] == "1.0.0" for profile in profiles)
    assert all(profile["market"] == "realprice" for profile in profiles)
    assert all(profile["content_sha256"] == _canonical_sha256(profile) for profile in profiles)
    json.dumps(profiles)

    battery = get_standard_profile("battery-only")["spec"]["vpp_params"]
    assert battery["battery_kwh"] > 0
    assert battery["pv_kw_peak"] == battery["wind_kw_rated"] == battery["load_kw_base"] == 0
    industrial = get_standard_profile("industrial-flexible-load")["spec"]["vpp_params"]
    assert industrial["load_profile"] == "industrial"
    assert industrial["load_elasticity"] > 0


def test_population_packs_cover_nine_required_regimes() -> None:
    packs = list_builtin_population_packs()

    assert len(packs) == 9
    assert {pack["id"] for pack in packs} == {
        "high-renewable-surplus",
        "demand-tight",
        "high-liquidity-market-making",
        "low-liquidity",
        "battery-arbitrage-heavy",
        "truthful-majority",
        "zi-majority",
        "ppo-llm-mixed",
        "adversarial",
    }
    assert len({pack["content_sha256"] for pack in packs}) == 9
    assert all(pack["version"] == "1.0.0" for pack in packs)
    assert all(pack["market"] == "p2p" for pack in packs)
    assert all(pack["content_sha256"] == _canonical_sha256(pack) for pack in packs)
    assert all(pack["spec"]["candidate_slots"] == 1 for pack in packs)
    assert all(pack["spec"]["roster"] for pack in packs)
    assert all(
        pack["spec"]["evaluation_protocol"]["method"] == "paired_world_population_tournament"
        for pack in packs
    )
    json.dumps(packs)

    by_id = {pack["id"]: pack for pack in packs}
    assert by_id["high-renewable-surplus"]["spec"]["scenario"]["renewable_multiplier"] > 1
    assert by_id["demand-tight"]["spec"]["scenario"]["load_multiplier"] > 1
    assert by_id["high-liquidity-market-making"]["spec"]["scenario"]["liquidity"] == "high"
    assert by_id["low-liquidity"]["spec"]["scenario"]["liquidity"] == "low"
    assert any(
        cohort["strategy"] == "llm_hybrid" for cohort in by_id["ppo-llm-mixed"]["spec"]["roster"]
    )
    assert by_id["adversarial"]["spec"]["scenario"]["adversarial_rules"][
        "must_pass_standard_gateway"
    ]


def test_catalog_results_are_defensive_copies() -> None:
    profiles = list_standard_profiles()
    profiles[0]["name"] = "corrupted"
    profiles[0]["spec"]["vpp_params"]["battery_kwh"] = -1
    profiles.append({"id": "injected"})

    fresh_profiles = list_standard_profiles()
    assert len(fresh_profiles) == 5
    assert fresh_profiles[0]["name"] == "Battery-only"
    assert fresh_profiles[0]["spec"]["vpp_params"]["battery_kwh"] == 100.0

    packs = list_builtin_population_packs()
    packs[0]["spec"]["roster"][0]["count"] = -1
    assert list_builtin_population_packs()[0]["spec"]["roster"][0]["count"] == 12

    profile = get_standard_profile("battery-only")
    profile["spec"]["vpp_params"]["battery_kwh"] = 0
    assert get_standard_profile("battery-only")["spec"]["vpp_params"]["battery_kwh"] == 100.0


def test_catalog_hashes_are_stable() -> None:
    profile_hashes = {item["id"]: item["content_sha256"] for item in list_standard_profiles()}
    pack_hashes = {item["id"]: item["content_sha256"] for item in list_builtin_population_packs()}

    assert profile_hashes == {
        "battery-only": "d79a358e55efcca5d0ee325ee7949ae201be59c4804edc2bf2d8dc4ad8e20f50",
        "commercial-load-battery": "7a4607fcdf3089a9a22270bf8cef6f094908b7edb3db3f2cef429a921beda7c6",
        "industrial-flexible-load": "020400c9955d4e20c429a1863b85170b2bea32324c6003456953fcd111c5fd30",
        "renewable-generator": "db8959ec147a8b0cc2c2768c6184c79407aed79933a1c37b8a1237414808f60c",
        "residential-pv-battery": "60db6f32259421bccb51249ebe03d47e07aa09f0cd8f59dc84a249b1a54223a9",
    }
    assert pack_hashes == {
        "adversarial": "2870f444bb859417fca9dc6b0cc184ce3b36adebba0c265f817f2abd7345cf3e",
        "battery-arbitrage-heavy": "d25cb9d1c3d790b4821e8ff3bfdbef98da9356e0e7dd6c0cf0615f0cd48d2412",
        "demand-tight": "674b832838a659e2aecb60be07d38cf8bda6553631ca29c54ce94b65c5ffba5e",
        "high-liquidity-market-making": "ab631f2fb0c231e1bca14adea68c13b9a334bd57229158d0e65f4f7999ce3ab1",
        "high-renewable-surplus": "744ba6a841b7a39446438b199cabba7971545045e812128a6ca02b34938ee92c",
        "low-liquidity": "bb4ba726a7647df262cfd8760dcd65c18f8fad581ecf33c41ad734c940f1f9f0",
        "ppo-llm-mixed": "43cf921d4e6fe13a696d7a0e7582e1a105d8cd3070345b48efcf1886fe2b52f5",
        "truthful-majority": "814144ed7b7fd20d240d28e3dd61c170bf2bd35d7243741b3600e408bc669a10",
        "zi-majority": "b3d869bd71c75d555536b8b0a0ad53627b0b9ad25d75bd489827858cd724b521",
    }


def test_unknown_profile_is_explicit() -> None:
    with pytest.raises(KeyError, match="unknown standard profile"):
        get_standard_profile("does-not-exist")

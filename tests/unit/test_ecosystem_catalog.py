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
    assert all(profile["version"] == "1" for profile in profiles)
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
    assert all(pack["version"] == "1" for pack in packs)
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
        "battery-only": "f3e925ec0557de23e4e263cb85134c8e7d4e2b4e528b30ce80c9a830837a330a",
        "commercial-load-battery": "55873bec8484f216b2058b9442742839ac404cc73cb79c15692af1a06b6c9def",
        "industrial-flexible-load": "ff0fea07b495f18c27fb03811c25c5325534893873914076a461ec4e0e76f1b0",
        "renewable-generator": "65d41a7b3bf541945fe53e78fa7e4cac2eee838e7988b982e3a312c60016c6fa",
        "residential-pv-battery": "2edcab391957010e7163d1125b674ae33d0e6e452d56deb174e6bc82527e26aa",
    }
    assert pack_hashes == {
        "adversarial": "2f78d252cfcaf76c1be831f115c02706dfae50063840b8e6b0c3fba80705d3bb",
        "battery-arbitrage-heavy": "ae85daa7a5cabf534f23356c6f7f7db84034c021e3679f581257fd5184a6be32",
        "demand-tight": "0afc7ebef32f705840e9607fdad9c9acd36fa9d5cc5d2bc32c1db047c05bc26d",
        "high-liquidity-market-making": "582ae42a2c848f171395b7336dca320d607bc037b39d887af010803439ad4915",
        "high-renewable-surplus": "8c8b5f35831e46f835ac1b5f5773b4867028309f9e0e1d2f4b18b5626745a6ea",
        "low-liquidity": "9a36c1c4bb0b2a714e307df31e85390a59df506821f9e43433d1831d217e231f",
        "ppo-llm-mixed": "fae54e0a472e7e6f367307fa56f1694ac8a88a882ee45bc3278138ebecf96379",
        "truthful-majority": "c9e4e0a28d4be146d148a78863e17c557d9a326d2fb1930ab8a4749eb695b09d",
        "zi-majority": "bdbc7bba33464b0f7f99c63e48cf7c322508c92ac27bb3ca3517bbab2c538a0d",
    }


def test_unknown_profile_is_explicit() -> None:
    with pytest.raises(KeyError, match="unknown standard profile"):
        get_standard_profile("does-not-exist")

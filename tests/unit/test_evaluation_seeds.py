from __future__ import annotations

import pytest

from eflux.evaluation.seeds import derive_seed, seed_labels, seed_values


def test_seeds_are_deterministic_and_distinct():
    a = derive_seed("season-0", "hidden", 1)
    assert a == derive_seed("season-0", "hidden", 1)
    assert a != derive_seed("season-0", "hidden", 2)
    assert a != derive_seed("season-0", "holdout", 1)
    assert a != derive_seed("season-0", "practice", 1)
    assert a != derive_seed("season-1", "hidden", 1)
    assert a != derive_seed("season-0", "hidden", 1, round_token="round-2")


def test_seed_values_and_labels_align():
    values = seed_values("season-0", "hidden", 5)
    labels = seed_labels("hidden", 5)
    assert len(values) == len(set(values)) == 5
    assert labels == ["hidden-1", "hidden-2", "hidden-3", "hidden-4", "hidden-5"]
    assert all(0 < v < 2**31 - 1 for v in values)


def test_unknown_kind_and_bad_index_raise():
    with pytest.raises(ValueError):
        derive_seed("season-0", "secret", 1)
    with pytest.raises(ValueError):
        derive_seed("season-0", "hidden", 0)
    with pytest.raises(ValueError):
        seed_labels("secret", 3)

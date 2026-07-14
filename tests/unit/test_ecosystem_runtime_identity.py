from __future__ import annotations

from eflux.api.routers.ecosystem import platform_runtime_identity
from eflux.ecosystem import runtime_identity


def test_runtime_identity_prefers_configured_commit(monkeypatch) -> None:
    monkeypatch.setenv("EFLUX_GIT_COMMIT", " configured-revision ")

    assert runtime_identity.repository_git_commit() == "configured-revision"
    assert platform_runtime_identity().model_dump() == {
        "git_commit": "configured-revision",
        "configured_by": "EFLUX_GIT_COMMIT",
    }


def test_runtime_identity_resolves_loose_and_packed_refs(tmp_path, monkeypatch) -> None:
    git_dir = tmp_path / ".git"
    ref = "refs/heads/main"
    commit = "a" * 40
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text(f"ref: {ref}\n", encoding="utf-8")
    (git_dir / ref).write_text(f"{commit}\n", encoding="utf-8")
    monkeypatch.delenv("EFLUX_GIT_COMMIT", raising=False)
    monkeypatch.setattr(runtime_identity, "PROJECT_ROOT", tmp_path)

    assert runtime_identity.repository_git_commit() == commit
    assert platform_runtime_identity().model_dump() == {
        "git_commit": commit,
        "configured_by": "repository",
    }

    (git_dir / ref).unlink()
    packed_commit = "b" * 40
    (git_dir / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled\n{packed_commit} {ref}\n",
        encoding="utf-8",
    )

    assert runtime_identity.repository_git_commit() == packed_commit

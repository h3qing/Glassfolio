import io
from pathlib import Path

from glassfolio.paths import resource_root


def test_resource_root_is_the_repo_in_development():
    root = resource_root()
    assert (root / "web").exists() and (root / "evals").exists() and (root / "tests" / "golden").exists()


def test_frozen_bundle_uses_the_unpacked_dir(monkeypatch, tmp_path):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_root() == Path(tmp_path)


def test_sidecar_exits_when_the_app_goes_away():
    from glassfolio.server.app import exit_when_closed
    exited = []
    exit_when_closed(io.StringIO("still here\n"), lambda: exited.append(True))
    assert exited == [True]

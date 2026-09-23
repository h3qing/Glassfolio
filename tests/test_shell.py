"""Running as the desktop app's sidecar."""

import io
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import TEST_KEY
from glassfolio.server.app import create_app, free_port

TOKEN = "t" * 43


def test_page_is_marked_for_the_native_shell(golden, tmp_path):
    (tmp_path / "index.html").write_text('<!doctype html><html lang="en"><head></head><body></body></html>')
    app = create_app(golden[0], TOKEN, ("testserver",), static_dir=tmp_path, shell="tauri")
    c = TestClient(app, base_url="http://testserver")
    c.get(f"/?token={TOKEN}", follow_redirects=False)
    page = c.get("/").text
    assert '<html lang="en" data-shell="tauri">' in page
    plain = create_app(golden[0], TOKEN, ("testserver",), static_dir=tmp_path)
    assert "data-shell" not in TestClient(plain, base_url="http://testserver").get("/").text


def test_free_port_is_usable():
    port = free_port()
    assert 1024 < port < 65536


def test_key_can_come_from_stdin(monkeypatch):
    from glassfolio.cli import _key_from_stdin
    monkeypatch.setattr("sys.stdin", io.StringIO(TEST_KEY + "\n"))
    assert _key_from_stdin() == TEST_KEY
    monkeypatch.setattr("sys.stdin", io.StringIO("not-a-key\n"))
    with pytest.raises(SystemExit):
        _key_from_stdin()


def test_socket_is_bound_before_ready_is_announced():
    from glassfolio.server.app import bind_local
    sock = bind_local(0)
    try:
        port = sock.getsockname()[1]
        import socket
        other = socket.socket()
        with pytest.raises(OSError):
            other.bind(("127.0.0.1", port))  # nobody else can take it now
        other.close()
    finally:
        sock.close()


def test_static_override_is_ignored_when_frozen(monkeypatch, tmp_path):
    import importlib
    import sys
    import glassfolio.server.app as app_module
    monkeypatch.setenv("GLASSFOLIO_STATIC", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "/bundle", raising=False)
    assert app_module.static_dir() == Path("/bundle/web/dist")
    monkeypatch.setattr(sys, "frozen", False)
    assert app_module.static_dir() == tmp_path


def test_restore_key_checks_it_opens_the_data(tmp_path, monkeypatch):
    from glassfolio.keys import DbKeyError
    from glassfolio.lake import open_lake
    from glassfolio.cli import verify_recovery_key
    open_lake(tmp_path / "h", TEST_KEY).con.close()
    assert verify_recovery_key(tmp_path / "h", TEST_KEY.upper()[:32] + " " + TEST_KEY[32:]) == TEST_KEY
    with pytest.raises(DbKeyError):
        verify_recovery_key(tmp_path / "h", "f" * 64)

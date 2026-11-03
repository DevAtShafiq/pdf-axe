"""
Regression tests (QA pass, qa/cloud.md) for the desktop side: cloud_client.py and
the account bridge, against servers that are not the Office Axe account server.

Run:  python -m pytest server/tests/test_qa_bridge.py -q
"""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

from server.tests.test_bridge_account import bridge  # noqa: F401  (fixture)


def _serve(handler_cls):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class _HtmlPage(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<!doctype html><title>Router</title><p>Welcome"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET

    def log_message(self, *a):
        pass


@pytest.fixture()
def html_server():
    srv, url = _serve(_HtmlPage)
    yield url
    srv.shutdown()


@pytest.fixture()
def garbage_server():
    """A TCP service that is not HTTP at all."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    stop = threading.Event()

    def run():
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            try:
                conn.recv(4096)
                conn.sendall(b"SSH-2.0-OpenSSH_9.0\r\n")
            finally:
                conn.close()
    threading.Thread(target=run, daemon=True).start()
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    stop.set()
    sock.close()


def test_client_wrong_server_raises_cloud_error(html_server):
    from cloud_client import CloudClient, CloudError
    c = CloudClient(html_server)
    with pytest.raises(CloudError) as ei:
        c.health()
    assert ei.value.status == 0 and getattr(ei.value, "wrong_server", False)
    with pytest.raises(CloudError):
        c.login("a@example.com", "password123")


def test_client_non_http_service_raises_cloud_error(garbage_server):
    from cloud_client import CloudClient, CloudError
    with pytest.raises(CloudError) as ei:
        CloudClient(garbage_server, timeout=5).health()
    assert ei.value.status == 0


def test_client_malformed_address_raises_cloud_error():
    from cloud_client import CloudClient, CloudError
    with pytest.raises(CloudError):
        CloudClient("htp//nonsense").health()


def test_bridge_state_and_login_against_wrong_server(bridge, html_server):  # noqa: F811
    b = bridge
    r = b.account_set_server(html_server)
    assert r["ok"] and r["reachable"] is False
    st = b.account_get_state()
    assert st["ok"] and st["configured"] and not st["logged_in"] and st["offline"]
    r = b.account_login("a@example.com", "password123")
    assert not r["ok"] and "not an Office Axe account server" in r["error"]


@pytest.mark.parametrize("typed,scheme", [
    ("127.0.0.1:8045", "http"), ("localhost:8000", "http"), ("192.168.1.20:8000", "http"),
    ("10.0.0.5", "http"), ("172.20.1.9:8000", "http"), ("office-pc:8000", "http"),
    ("server.local:8000", "http"), ("[::1]:8000", "http"),
    ("accounts.example.com", "https"), ("8.8.8.8", "https"), ("172.32.0.1", "https"),
])
def test_set_server_picks_scheme(typed, scheme):
    # The scheme rule only (account_set_server would also try to reach the host)
    from cloud_bridge import CloudBridgeMixin
    assert CloudBridgeMixin._cloud_is_local_host(typed) is (scheme == "http")


def test_set_server_lan_name_gets_http(bridge):  # noqa: F811
    r = bridge.account_set_server("127.0.0.1:1")
    assert r["ok"] and r["server_url"] == "http://127.0.0.1:1"

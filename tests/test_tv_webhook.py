import json
import threading
import urllib.request
from http.server import HTTPServer

from trading_agent.tv_webhook import _make_handler


def _post(port, body, headers=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}",
        data=json.dumps(body).encode(),
        headers=headers or {},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _run_server(secret, on_payload):
    server = HTTPServer(("127.0.0.1", 0), _make_handler(on_payload, secret))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_correct_secret_is_accepted():
    received = []
    server, thread = _run_server("s3cret", received.append)
    try:
        status = _post(
            server.server_port, {"msg": "hi"}, headers={"X-Webhook-Secret": "s3cret"}
        )
        assert status == 200
        assert received == [{"msg": "hi"}]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_wrong_secret_is_rejected():
    received = []
    server, thread = _run_server("s3cret", received.append)
    try:
        status = _post(
            server.server_port, {"msg": "hi"}, headers={"X-Webhook-Secret": "nope"}
        )
        assert status == 401
        assert received == []
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_missing_secret_header_is_rejected():
    received = []
    server, thread = _run_server("s3cret", received.append)
    try:
        status = _post(server.server_port, {"msg": "hi"})
        assert status == 401
        assert received == []
    finally:
        server.shutdown()
        thread.join(timeout=5)

"""Unit test konfigurasi & keamanan lokal (Req 29.1, 29.2, 29.4) dan envelope error."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import main as main_module
from studio.api.errors import StudioError, error_envelope
from studio.app import create_app
from studio.config import Settings, is_loopback_host, non_loopback_warning

_ENV_VARS = (
    "HOST",
    "PORT",
    "DATA_DIR",
    "CORS_ORIGINS",
    "MODEL_DEFAULT",
    "MODEL_ROOT",
    "MODEL_PROFILER",
    "MODEL_QUERY",
    "MODEL_CHART",
    "MODEL_INSIGHT",
)

ALLOWED_ORIGIN = "http://localhost:3000"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolasi dari env shell / `.env` nyata."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _assert_envelope(body: Any, code: str | None = None) -> None:
    assert set(body) == {"error"}
    err = body["error"]
    assert set(err) == {"code", "message", "details"}
    assert isinstance(err["code"], str) and err["code"]
    assert isinstance(err["message"], str) and err["message"]
    assert isinstance(err["details"], dict)
    if code is not None:
        assert err["code"] == code


# --- Host default & peringatan non-loopback (Req 29.1, 29.2) ---------------


def test_default_host_is_loopback() -> None:
    assert Settings().host == "127.0.0.1"


def test_blank_host_env_falls_back_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "   ")
    assert Settings().host == "127.0.0.1"


def test_host_env_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")
    assert Settings().host == "0.0.0.0"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "127.0.0.5"])
def test_no_warning_for_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)
    assert non_loopback_warning(host) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
def test_warning_for_non_loopback_hosts(host: str) -> None:
    assert not is_loopback_host(host)
    warning = non_loopback_warning(host)
    assert warning is not None
    assert host in warning
    assert "autentikasi" in warning


class _Runner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, app: Any, **kwargs: Any) -> None:
        self.calls.append({"app": app, **kwargs})


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> _Runner:
    runner = _Runner()
    monkeypatch.setattr(main_module, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(main_module.uvicorn, "run", runner)
    # `main()` memvalidasi model saat startup (Req 9.4); pakai model tool-capable.
    monkeypatch.setenv("MODEL_DEFAULT", "openai/gpt-4o-mini")
    return runner


def test_main_binds_loopback_without_warning(
    fake_run: _Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    main_module.main()
    assert len(fake_run.calls) == 1
    assert fake_run.calls[0]["host"] == "127.0.0.1"
    assert "PERINGATAN" not in capsys.readouterr().err


def test_main_prints_warning_to_stderr_for_non_loopback(
    fake_run: _Runner, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")
    main_module.main()
    out = capsys.readouterr()
    assert "PERINGATAN" in out.err
    assert "0.0.0.0" in out.err
    assert "PERINGATAN" not in out.out
    assert fake_run.calls[0]["host"] == "0.0.0.0"


# --- CORS (Req 29.4) --------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(cors_origins=[ALLOWED_ORIGIN])))


def test_cors_allows_configured_origin(client: TestClient) -> None:
    resp = client.get("/api/health", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


def test_cors_rejects_unknown_origin(client: TestClient) -> None:
    resp = client.get("/api/health", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_rejects_unknown_origin(client: TestClient) -> None:
    headers = {"Origin": "http://evil.example.com", "Access-Control-Request-Method": "POST"}
    resp = client.options("/api/health", headers=headers)
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_cors_preflight_allows_configured_origin(client: TestClient) -> None:
    headers = {"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"}
    resp = client.options("/api/health", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN


def test_cors_origins_parsed_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, http://127.0.0.1:3000 ,")
    assert Settings().cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


# --- Envelope error konsisten -----------------------------------------------


@pytest.fixture
def error_client() -> TestClient:
    app = create_app(Settings())

    @app.get("/_test/studio-error")
    async def raise_studio_error() -> None:
        raise StudioError("DATASET_NOT_FOUND", "Dataset tidak ada.", {"id": "x"}, http_status=404)

    @app.get("/_test/boom")
    async def raise_unhandled() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/_test/validate")
    async def validate(n: int) -> dict[str, int]:
        return {"n": n}

    return TestClient(app, raise_server_exceptions=False)


def test_studio_error_envelope(error_client: TestClient) -> None:
    resp = error_client.get("/_test/studio-error")
    assert resp.status_code == 404
    body = resp.json()
    _assert_envelope(body, "DATASET_NOT_FOUND")
    assert body["error"]["details"] == {"id": "x"}


def test_not_found_route_envelope(error_client: TestClient) -> None:
    resp = error_client.get("/api/does-not-exist")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "NOT_FOUND")


def test_method_not_allowed_envelope(error_client: TestClient) -> None:
    resp = error_client.post("/api/health")
    assert resp.status_code == 405
    _assert_envelope(resp.json(), "METHOD_NOT_ALLOWED")


def test_validation_error_envelope(error_client: TestClient) -> None:
    resp = error_client.get("/_test/validate", params={"n": "abc"})
    assert resp.status_code == 422
    body = resp.json()
    _assert_envelope(body, "VALIDATION_ERROR")
    assert body["error"]["details"]["errors"]


def test_unhandled_error_envelope_hides_internals(error_client: TestClient) -> None:
    resp = error_client.get("/_test/boom")
    assert resp.status_code == 500
    _assert_envelope(resp.json(), "INTERNAL_ERROR")
    assert "secret internal detail" not in resp.text


def test_studio_error_to_dict_and_helper_match() -> None:
    exc = StudioError("X", "msg")
    assert exc.to_dict() == {"code": "X", "message": "msg", "details": {}}
    assert error_envelope("X", "msg") == {"error": exc.to_dict()}

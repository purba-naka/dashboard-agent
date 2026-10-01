"""Unit test Model_Gateway dan validasi model saat startup (Req 9.1, 9.4, 9.5).

``supports_tools`` selalu di-stub (tanpa jaringan/provider): ``True``, ``False``,
dan exception. Test startup memakai ``main.load_models``/``main.main`` dengan
``load_dotenv`` dan ``uvicorn.run`` di-monkeypatch seperti
``tests/unit/test_config_security.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from google.adk.models.lite_llm import LiteLlm

import main as main_module
from studio.agents import model_gateway
from studio.agents.model_gateway import (
    SKIP_CHECK_ENV,
    ModelCapabilityError,
    build_models,
    resolve_from_settings,
    skip_capability_check,
)
from studio.config import Settings
from studio.core.models_config import AGENT_ENV, DEFAULT_ENV, resolve_models

_ENV_VARS = (DEFAULT_ENV, *AGENT_ENV.values(), SKIP_CHECK_ENV, "HOST", "PORT", "DATA_DIR")

ALL_OPENAI = {agent: "openai/gpt-4o-mini" for agent in AGENT_ENV}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolasi dari env shell / `.env` nyata."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


class _Stub:
    """Stub ``supports_function_calling`` yang mencatat model yang dicek."""

    def __init__(self, result: bool | BaseException | dict[str, Any]) -> None:
        self.result = result
        self.calls: list[str] = []

    def __call__(self, model: str) -> bool:
        self.calls.append(model)
        result = self.result
        if isinstance(result, dict):
            result = result.get(model, True)
        if isinstance(result, BaseException):
            raise result
        return bool(result)


# --- build_models -----------------------------------------------------------


def test_build_models_returns_litellm_per_agent_when_supported() -> None:
    resolved = {**ALL_OPENAI, "chart": "anthropic/claude-3-5-sonnet-20240620"}
    stub = _Stub(True)

    models = build_models(resolved, supports_tools=stub)

    assert set(models) == set(AGENT_ENV)
    for agent, model in models.items():
        assert isinstance(model, LiteLlm)
        assert model.model == resolved[agent]
    # Setiap nama model unik dicek tepat sekali.
    assert sorted(stub.calls) == sorted(set(resolved.values()))


def test_build_models_rejects_model_without_tool_calling() -> None:
    resolved = {**ALL_OPENAI, "query": "some-provider/no-tools-model"}
    stub = _Stub({"some-provider/no-tools-model": False})

    with pytest.raises(ModelCapabilityError) as exc:
        build_models(resolved, supports_tools=stub)

    err = exc.value
    assert err.code == "MODEL_NO_TOOL_CALLING"
    assert "some-provider/no-tools-model" in err.message
    assert "'query'" in err.message
    assert "MODEL_QUERY" in err.message
    assert err.details == {
        "agent": "query",
        "model": "some-provider/no-tools-model",
        "env_var": "MODEL_QUERY",
    }
    assert (err.agent, err.model, err.reason) == ("query", "some-provider/no-tools-model", None)


def test_build_models_treats_exception_as_unsupported() -> None:
    resolved = {**ALL_OPENAI, "insight": "unknown/mystery-model"}
    stub = _Stub({"unknown/mystery-model": ValueError("model not mapped")})

    with pytest.raises(ModelCapabilityError) as exc:
        build_models(resolved, supports_tools=stub)

    err = exc.value
    assert err.code == "MODEL_NO_TOOL_CALLING"
    assert "unknown/mystery-model" in err.message
    assert "'insight'" in err.message
    assert "model not mapped" in err.message
    assert err.details["agent"] == "insight"
    assert err.details["model"] == "unknown/mystery-model"
    assert err.details["env_var"] == "MODEL_INSIGHT"
    assert err.details["reason"] == "ValueError: model not mapped"
    assert isinstance(err.__cause__, ValueError)


def test_build_models_skip_check_does_not_call_supports_tools() -> None:
    stub = _Stub(False)
    resolved = {agent: "ollama/llama3.1" for agent in AGENT_ENV}

    models = build_models(resolved, supports_tools=stub, skip_check=True)

    assert stub.calls == []
    assert {a: m.model for a, m in models.items()} == resolved


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_skip_capability_check_truthy(value: str) -> None:
    assert skip_capability_check({SKIP_CHECK_ENV: value}) is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_skip_capability_check_falsy(value: str) -> None:
    assert skip_capability_check({SKIP_CHECK_ENV: value}) is False
    assert skip_capability_check({}) is False


# --- resolve_from_settings + resolve_models --------------------------------


def test_resolve_from_settings_prefers_settings_and_falls_back_to_environ() -> None:
    settings = Settings(model_default="openai/gpt-4o-mini", model_chart="  anthropic/claude  ")
    environ = {"MODEL_QUERY": "gemini/gemini-2.0-flash", "MODEL_DEFAULT": "ignored/model", "MODEL_ROOT": "  "}

    raw = resolve_from_settings(settings, environ)

    assert raw == {
        "MODEL_DEFAULT": "openai/gpt-4o-mini",
        "MODEL_CHART": "anthropic/claude",
        "MODEL_QUERY": "gemini/gemini-2.0-flash",
    }
    resolved = resolve_models(raw)
    assert resolved["chart"] == "anthropic/claude"
    assert resolved["query"] == "gemini/gemini-2.0-flash"
    assert resolved["root"] == resolved["profiler"] == resolved["insight"] == "openai/gpt-4o-mini"


# --- Startup (main.py) -------------------------------------------------------


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
    return runner


def _stub_gateway(monkeypatch: pytest.MonkeyPatch, stub: _Stub) -> None:
    """Arahkan ``main.build_models`` ke ``build_models`` asli dengan ``supports_tools`` stub."""

    def patched(resolved: Any, supports_tools: Any = None, *, skip_check: bool = False) -> Any:
        return model_gateway.build_models(resolved, stub, skip_check=skip_check)

    monkeypatch.setattr(main_module, "build_models", patched)


def test_startup_exits_1_when_model_vars_missing(
    fake_run: _Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 1
    assert fake_run.calls == []
    err = capsys.readouterr().err
    assert "ERROR [MISSING_MODEL_CONFIG]" in err
    for var in (*AGENT_ENV.values(), DEFAULT_ENV):
        assert var in err


def test_startup_exits_1_when_only_some_agents_configured(
    fake_run: _Runner, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MODEL_ROOT", "openai/gpt-4o-mini")
    monkeypatch.setenv("MODEL_QUERY", "openai/gpt-4o-mini")

    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 1
    assert fake_run.calls == []
    err = capsys.readouterr().err
    assert "MISSING_MODEL_CONFIG" in err
    for var in ("MODEL_PROFILER", "MODEL_CHART", "MODEL_INSIGHT", DEFAULT_ENV):
        assert var in err
    assert "MODEL_ROOT" not in err and "MODEL_QUERY" not in err


def test_startup_exits_1_when_model_lacks_tool_calling(
    fake_run: _Runner, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MODEL_DEFAULT", "openai/gpt-4o-mini")
    monkeypatch.setenv("MODEL_PROFILER", "acme/plain-text-llm")
    _stub_gateway(monkeypatch, _Stub({"acme/plain-text-llm": False}))

    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 1
    assert fake_run.calls == []
    err = capsys.readouterr().err
    assert "ERROR [MODEL_NO_TOOL_CALLING]" in err
    assert "acme/plain-text-llm" in err
    assert "'profiler'" in err


def test_startup_skip_check_env_allows_unlisted_model(
    fake_run: _Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_DEFAULT", "ollama/custom-local")
    monkeypatch.setenv(SKIP_CHECK_ENV, "1")
    stub = _Stub(False)
    _stub_gateway(monkeypatch, stub)

    models = main_module.load_models(Settings())

    assert stub.calls == []
    assert {m.model for m in models.values()} == {"ollama/custom-local"}


def test_startup_runs_server_with_models_when_supported(
    fake_run: _Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODEL_DEFAULT", "openai/gpt-4o-mini")
    monkeypatch.setenv("MODEL_CHART", "anthropic/claude-3-5-sonnet-20240620")
    _stub_gateway(monkeypatch, _Stub(True))

    main_module.main()

    assert len(fake_run.calls) == 1
    models = fake_run.calls[0]["app"].state.models
    assert set(models) == set(AGENT_ENV)
    assert models["chart"].model == "anthropic/claude-3-5-sonnet-20240620"
    assert models["root"].model == "openai/gpt-4o-mini"

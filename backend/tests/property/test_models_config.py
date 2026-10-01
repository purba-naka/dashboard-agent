"""Feature: dashboard-studio-agent, Property 11: Resolusi model per agent.

**Validates: Requirements 9.2, 9.3, 9.4**
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from studio.core.models_config import AGENT_ENV, DEFAULT_ENV, MissingModelConfig, resolve_models

_ALL_VARS = [*AGENT_ENV.values(), DEFAULT_ENV]

_model_name = st.from_regex(r"[a-z0-9][a-z0-9/_.:\-]{0,20}", fullmatch=True)
_padding = st.sampled_from(["", " ", "\t", "  \n"])
# ``None`` = variabel tidak diset; string kosong/whitespace = diset tapi kosong.
_value = st.one_of(
    st.none(),
    st.sampled_from(["", "   ", "\t"]),
    st.tuples(_padding, _model_name, _padding).map("".join),
)


@st.composite
def _envs(draw: st.DrawFn) -> dict[str, str]:
    env: dict[str, str] = {}
    for var in _ALL_VARS:
        value = draw(_value)
        if value is not None:
            env[var] = value
    noise = draw(st.dictionaries(st.sampled_from(["PATH", "MODEL_OTHER", "OPENAI_API_KEY"]), _model_name))
    env.update(noise)
    return env


def _get(env: dict[str, str], var: str) -> str | None:
    value = (env.get(var) or "").strip()
    return value or None


@given(_envs())
def test_resolve_models_prefers_agent_var_then_default(env: dict[str, str]) -> None:
    """Feature: dashboard-studio-agent, Property 11: Resolusi model per agent.

    **Validates: Requirements 9.2, 9.3, 9.4**
    """
    default = _get(env, DEFAULT_ENV)
    expected = {agent: _get(env, var) or default for agent, var in AGENT_ENV.items()}
    missing = [var for agent, var in AGENT_ENV.items() if expected[agent] is None]

    if missing:
        with pytest.raises(MissingModelConfig) as exc_info:
            resolve_models(env)
        err = exc_info.value
        assert err.missing_vars == [*missing, DEFAULT_ENV]
        assert err.code == "MISSING_MODEL_CONFIG"
        for var in err.missing_vars:
            assert var in err.message
        # Variabel yang terisi tidak disebut sebagai hilang.
        for agent, var in AGENT_ENV.items():
            if expected[agent] is not None:
                assert var not in err.missing_vars
    else:
        assert resolve_models(env) == expected

"""Resolusi nama model per agent dari environment (Req 9.2, 9.3, 9.4).

Modul murni: tidak mengimpor LiteLLM/ADK. Pembuatan objek model dan validasi
kapabilitas tool calling dilakukan di ``studio.agents.model_gateway``.
"""

from __future__ import annotations

from collections.abc import Mapping

from studio.api.errors import StudioError

DEFAULT_ENV = "MODEL_DEFAULT"

AGENT_ENV: dict[str, str] = {
    "root": "MODEL_ROOT",
    "profiler": "MODEL_PROFILER",
    "query": "MODEL_QUERY",
    "chart": "MODEL_CHART",
    "insight": "MODEL_INSIGHT",
    "architect": "MODEL_ARCHITECT",
    #: Semantic_Drafter (output terstruktur, tanpa tool; Req 32.3).
    "semantic": "MODEL_SEMANTIC",
}

#: Agent yang tidak memanggil tool sehingga cek tool-calling dilewati.
NO_TOOL_AGENTS: frozenset[str] = frozenset({"semantic"})


class MissingModelConfig(StudioError):
    """Variabel model suatu agent dan ``MODEL_DEFAULT`` sama-sama tidak diset."""

    def __init__(self, missing_vars: list[str]) -> None:
        self.missing_vars = list(missing_vars)
        super().__init__(
            "MISSING_MODEL_CONFIG",
            "Konfigurasi model tidak lengkap. Set variabel berikut di .env: "
            + ", ".join(self.missing_vars),
            {"missing_vars": self.missing_vars},
            http_status=500,
        )


def _get(env: Mapping[str, str], name: str) -> str | None:
    """Nilai variabel yang sudah di-strip; kosong/whitespace dianggap tidak diset."""
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def resolve_models(env: Mapping[str, str]) -> dict[str, str]:
    """Kembalikan ``{agent: nama_model}`` untuk setiap agent di ``AGENT_ENV``.

    Variabel khusus agent diutamakan; bila tidak diset dipakai ``MODEL_DEFAULT``.
    Bila keduanya tidak diset untuk minimal satu agent, raise
    ``MissingModelConfig`` dengan daftar variabel agent yang hilang diikuti
    ``MODEL_DEFAULT``.
    """
    default = _get(env, DEFAULT_ENV)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for agent, var in AGENT_ENV.items():
        value = _get(env, var) or default
        if value is None:
            missing.append(var)
        else:
            resolved[agent] = value
    if missing:
        raise MissingModelConfig([*missing, DEFAULT_ENV])
    return resolved

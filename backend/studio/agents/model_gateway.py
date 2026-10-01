"""Model_Gateway: bangun objek ``LiteLlm`` per agent + cek kapabilitas tool calling.

Resolusi nama model (murni) ada di ``studio.core.models_config.resolve_models``;
modul ini menambahkan langkah yang bergantung pada LiteLLM/ADK (Req 9.1, 9.5).

Dipanggil sekali saat startup oleh ``backend/main.py``. Hasil ``build_models``
disimpan di ``app.state.models`` (``dict[agent, LiteLlm]``) agar dapat dipakai
saat membangun agent (task 18.9) dan runner (task 18.11).

Override: ``MODEL_SKIP_CAPABILITY_CHECK=1`` (juga ``true``/``yes``/``on``)
melewati cek ``supports_function_calling``. Berguna untuk model lokal/kustom
(mis. ``ollama/...``) yang mendukung tool calling tetapi tidak tercatat di
model map LiteLLM. Default: cek aktif.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import litellm
from google.adk.models.lite_llm import LiteLlm

from studio.api.errors import StudioError
from studio.core.models_config import AGENT_ENV, DEFAULT_ENV, NO_TOOL_AGENTS

if TYPE_CHECKING:
    from studio.config import Settings

SKIP_CHECK_ENV = "MODEL_SKIP_CAPABILITY_CHECK"
_TRUTHY = {"1", "true", "yes", "on"}

# Pemetaan variabel env model -> field `Settings`.
_SETTINGS_FIELDS: dict[str, str] = {
    DEFAULT_ENV: "model_default",
    **{var: var.lower() for var in AGENT_ENV.values()},
}


class ModelCapabilityError(StudioError):
    """Model yang dikonfigurasi untuk suatu agent tidak mendukung tool calling."""

    def __init__(self, agent: str, model: str, reason: str | None = None) -> None:
        self.agent = agent
        self.model = model
        self.reason = reason
        var = AGENT_ENV.get(agent, DEFAULT_ENV)
        message = (
            f"Model '{model}' untuk agent '{agent}' tidak mendukung tool calling "
            f"(periksa {var} atau {DEFAULT_ENV})."
        )
        if reason:
            message += f" Detail: {reason}"
        message += f" Set {SKIP_CHECK_ENV}=1 untuk melewati cek ini pada model lokal/kustom."
        details: dict[str, str] = {"agent": agent, "model": model, "env_var": var}
        if reason:
            details["reason"] = reason
        super().__init__("MODEL_NO_TOOL_CALLING", message, details, http_status=500)


def resolve_from_settings(
    settings: Settings, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Mapping ``{MODEL_*: nilai}`` untuk ``resolve_models``.

    Nilai dari ``Settings`` diutamakan; bila kosong dipakai ``environ``
    (default ``os.environ``). Nilai kosong/whitespace dilewati.
    """
    env = os.environ if environ is None else environ
    out: dict[str, str] = {}
    for var, field in _SETTINGS_FIELDS.items():
        value = getattr(settings, field, None) or env.get(var)
        if value is not None and value.strip():
            out[var] = value.strip()
    return out


def skip_capability_check(environ: Mapping[str, str] | None = None) -> bool:
    """True bila ``MODEL_SKIP_CAPABILITY_CHECK`` bernilai truthy."""
    env = os.environ if environ is None else environ
    return (env.get(SKIP_CHECK_ENV) or "").strip().lower() in _TRUTHY


def build_models(
    resolved: Mapping[str, str],
    supports_tools: Callable[[str], bool] = litellm.supports_function_calling,
    *,
    skip_check: bool = False,
) -> dict[str, LiteLlm]:
    """Bangun ``LiteLlm`` per agent dari ``{agent: nama_model}``.

    Raise ``ModelCapabilityError(agent, model)`` bila ``supports_tools(model)``
    mengembalikan nilai falsy atau melempar exception (kecuali ``skip_check``).
    Semua model divalidasi dulu sebelum objek dibuat; cek per nama model
    dilakukan sekali.
    """
    if not skip_check:
        checked: set[str] = set()
        for agent, model in resolved.items():
            if agent in NO_TOOL_AGENTS or model in checked:
                continue
            try:
                ok = supports_tools(model)
            except Exception as exc:  # model tak dikenal / error provider
                raise ModelCapabilityError(
                    agent, model, f"{type(exc).__name__}: {exc}"
                ) from exc
            if not ok:
                raise ModelCapabilityError(agent, model)
            checked.add(model)

    # Pilihan LiteLLM untuk Gemini disengaja (provider-agnostik); redam peringatan ADK.
    os.environ.setdefault("ADK_SUPPRESS_GEMINI_LITELLM_WARNINGS", "true")
    return {agent: LiteLlm(model=model) for agent, model in resolved.items()}

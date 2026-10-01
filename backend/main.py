"""Entrypoint Backend_API: `python main.py`."""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from studio.agents.model_gateway import (
    ModelCapabilityError,
    build_models,
    resolve_from_settings,
    skip_capability_check,
)
from studio.agents.wiring import configure_agents
from studio.api import chat
from studio.app import create_app
from studio.config import Settings, non_loopback_warning
from studio.core.models_config import MissingModelConfig, resolve_models


def load_models(settings: Settings) -> dict:
    """Resolusi + validasi model per agent (Req 9.1, 9.4, 9.5).

    Kegagalan dicetak ke stderr dan proses berhenti dengan exit code 1.
    """
    try:
        resolved = resolve_models(resolve_from_settings(settings))
        return build_models(resolved, skip_check=skip_capability_check())
    except (MissingModelConfig, ModelCapabilityError) as exc:
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    # Env yang sudah diset di shell tetap diprioritaskan di atas `.env`.
    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
    settings = Settings()

    models = load_models(settings)

    warning = non_loopback_warning(settings.host)
    if warning:
        print(warning, file=sys.stderr)

    # `{agent: LiteLlm}` dipasang sebelum lifespan agar `configure_agents`
    # (task 18.11) dapat merakit agent dan runner chat.
    app = create_app(
        settings,
        extra_routers=[chat.router],
        lifespan_hooks=[configure_agents],
    )
    app.state.models = models
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

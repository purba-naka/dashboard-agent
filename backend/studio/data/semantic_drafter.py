"""Semantic_Drafter: draft Semantic_Model otomatis setelah profiling (Req 32).

Alur per Workspace (``run``):

1. ``heuristic_draft`` + ``merge_draft`` → upsert ``candidate`` → publish
   ``semantic.updated {run_id, phase: "heuristic"}``. Draft heuristik selalu ada
   walau LLM gagal (Req 32.2, 32.7).
2. Pengayaan LLM lewat ``SemanticEnricher`` (ADK ``LlmAgent`` + ``output_schema``)
   dengan konteks dari Privacy_Guard (Req 32.3, 32.4) dan batas waktu.
3. Validasi per entri: kolom harus ada, metrik lolos ``DataEngine.validate_metric``;
   entri gagal dibuang dan dicatat (Req 32.8).
4. ``merge_draft`` ulang (entri ``confirmed``/``user``/ditolak tidak tersentuh,
   Req 32.5, 32.6) → upsert → set domain → publish ``semantic.updated``
   ``{phase: "llm"}``.
5. Gagal di langkah 2 → run ``llm_failed`` + ``semantic.warning`` (Req 32.7).

Satu drafter aktif per Workspace; run baru diantrekan di belakang run berjalan
(Req 32.11). Semua langkah berjalan di latar belakang dan tidak mengubah
Dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from studio.api.errors import StudioError
from studio.core.models import Aggregation, BusinessMetric, GlossaryTerm, NumberFormat
from studio.core.privacy import PrivacySettings, build_dataset_context
from studio.core.semantic import (
    DatasetInput,
    DraftEntry,
    ExistingEntry,
    column_key,
    heuristic_draft,
    merge_draft,
    metric_key,
    term_key,
)
from studio.store.repos import DatasetRecord, Repositories

__all__ = [
    "SemanticDraftOutput",
    "SemanticEnricher",
    "SemanticDrafter",
    "AdkSemanticEnricher",
    "kpi_catalog_summary",
]

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "agents" / "knowledge"


# ---------------------------------------------------------------------------
# Skema output LLM (output_schema ADK)
# ---------------------------------------------------------------------------


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DraftColumnOut(_Loose):
    table: str
    column: str
    label: str | None = None
    description: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    default_aggregation: Aggregation | None = None


class DraftMetricOut(_Loose):
    name: str
    label: str | None = None
    description: str | None = None
    expr: str
    base_table: str
    synonyms: list[str] = Field(default_factory=list)
    good_direction: Literal["up", "down", "neutral"] = "up"
    format_style: Literal["number", "currency", "percent"] | None = None


class DraftTermOut(_Loose):
    term: str
    description: str
    synonyms: list[str] = Field(default_factory=list)


class SemanticDraftOutput(_Loose):
    domain: str | None = None
    domain_confidence: float | None = Field(default=None, ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    columns: list[DraftColumnOut] = Field(default_factory=list)
    metrics: list[DraftMetricOut] = Field(default_factory=list)
    glossary: list[DraftTermOut] = Field(default_factory=list)


class SemanticEnricher(Protocol):
    async def __call__(self, context: Mapping[str, Any]) -> SemanticDraftOutput: ...


def kpi_catalog_summary(max_chars: int = 6000) -> str:
    """Ringkasan katalog KPI dari BI_Knowledge_Pack (kosong bila belum ada)."""
    path = _KNOWLEDGE_DIR / "kpi_catalog.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Enricher ADK
# ---------------------------------------------------------------------------


class AdkSemanticEnricher:
    """``LlmAgent`` dengan ``output_schema`` di ``Runner`` terpisah (tanpa sesi chat)."""

    APP_NAME = "semantic_drafter"
    OUTPUT_KEY = "semantic_draft"

    def __init__(self, model: Any, instruction: str) -> None:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self._sessions = InMemorySessionService()
        agent = LlmAgent(
            name="Semantic_Drafter",
            model=model,
            instruction=instruction,
            output_schema=SemanticDraftOutput,
            output_key=self.OUTPUT_KEY,
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        self._runner = Runner(agent=agent, app_name=self.APP_NAME, session_service=self._sessions)

    async def __call__(self, context: Mapping[str, Any]) -> SemanticDraftOutput:
        from google.genai import types

        session = await self._sessions.create_session(app_name=self.APP_NAME, user_id="local")
        message = types.Content(
            role="user",
            parts=[types.Part(text=json.dumps(context, ensure_ascii=False, default=str))],
        )
        try:
            async for _ in self._runner.run_async(
                user_id="local", session_id=session.id, new_message=message
            ):
                pass
            final = await self._sessions.get_session(
                app_name=self.APP_NAME, user_id="local", session_id=session.id
            )
        finally:
            try:
                await self._sessions.delete_session(
                    app_name=self.APP_NAME, user_id="local", session_id=session.id
                )
            except Exception:  # noqa: BLE001
                pass
        raw = (final.state if final is not None else {}).get(self.OUTPUT_KEY)
        if isinstance(raw, str):
            raw = json.loads(raw)
        return SemanticDraftOutput.model_validate(raw)


# ---------------------------------------------------------------------------
# Drafter
# ---------------------------------------------------------------------------

Publisher = Callable[[str, str, dict[str, Any]], None]
MetricValidator = Callable[[str, BusinessMetric], Awaitable[Any]]


class SemanticDrafter:
    def __init__(
        self,
        repos: Repositories,
        bus: Any,
        *,
        validate_metric: MetricValidator | None = None,
        enricher: SemanticEnricher | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.repos = repos
        self.bus = bus
        self.validate_metric = validate_metric
        #: Dipasang wiring agent setelah Model_Gateway siap; ``None`` = heuristik saja.
        self.enricher = enricher
        self.timeout_s = timeout_s
        self._chains: dict[str, asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # -- penjadwalan -----------------------------------------------------------

    def schedule(self, workspace_id: str, trigger_dataset_id: str | None = None) -> asyncio.Task[None]:
        """Antrekan run untuk Workspace (berjalan setelah run sebelumnya selesai)."""
        previous = self._chains.get(workspace_id)

        async def chained() -> None:
            if previous is not None:
                try:
                    await previous
                except BaseException:  # noqa: BLE001 — run sebelumnya tidak menghentikan antrean
                    pass
            await self.run_safely(workspace_id, trigger_dataset_id)

        task = asyncio.create_task(chained(), name=f"semantic-draft-{workspace_id}")
        self._chains[workspace_id] = task
        self._tasks.add(task)

        def _done(t: asyncio.Task[None]) -> None:
            self._tasks.discard(t)
            if self._chains.get(workspace_id) is t:
                self._chains.pop(workspace_id, None)

        task.add_done_callback(_done)
        return task

    async def on_dataset_profiled(self, dataset: DatasetRecord) -> None:
        """Hook setelah profiling + relasi selesai (dipasang di ``app.py``)."""
        self.schedule(dataset.workspace_id, dataset.id)

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # -- run ---------------------------------------------------------------------

    async def run_safely(self, workspace_id: str, trigger_dataset_id: str | None = None) -> None:
        try:
            await self.run(workspace_id, trigger_dataset_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Semantic_Drafter gagal untuk Workspace %s", workspace_id)

    async def run(self, workspace_id: str, trigger_dataset_id: str | None = None) -> str:
        run = await self.repos.draft_runs.create(workspace_id, trigger_dataset_id)
        datasets = await self.repos.datasets.list_by_workspace(workspace_id)
        profiles = await self.repos.profiles.list_by_workspace(workspace_id)
        inputs = [
            DatasetInput(
                dataset_id=d.id,
                table=d.table_name,
                columns=tuple(profiles[d.id].columns) if d.id in profiles else (),
                original_names={m.normalized: m.original for m in d.column_mapping},
            )
            for d in datasets
            if d.id in profiles
        ]

        # 1. Heuristik.
        heuristic = heuristic_draft(inputs)
        await self._write(workspace_id, heuristic)
        self._publish(workspace_id, "semantic.updated", {"run_id": run.id, "phase": "heuristic"})

        if self.enricher is None:
            await self.repos.draft_runs.finish(run.id, "done")
            return run.id

        # 2. LLM.
        context = self._llm_context(datasets, profiles, heuristic)
        try:
            output = await asyncio.wait_for(self.enricher(context), timeout=self.timeout_s)
        except asyncio.CancelledError:
            raise
        except (Exception, asyncio.TimeoutError) as exc:  # noqa: BLE001
            reason = "timeout" if isinstance(exc, asyncio.TimeoutError) else f"{type(exc).__name__}: {exc}"
            logger.warning("Pengayaan semantik LLM gagal (%s)", reason)
            await self.repos.draft_runs.finish(run.id, "llm_failed")
            self._publish(workspace_id, "semantic.warning", {"run_id": run.id, "reason": reason})
            return run.id

        # 3–4. Validasi, merge, simpan.
        entries, discarded = await self._from_llm(workspace_id, output, datasets, heuristic)
        await self._write(workspace_id, entries)
        await self.repos.semantic_meta.set_domain(
            workspace_id, output.domain, output.domain_confidence, output.assumptions
        )
        await self.repos.draft_runs.finish(run.id, "done", discarded)
        self._publish(
            workspace_id,
            "semantic.updated",
            {"run_id": run.id, "phase": "llm", "discarded": len(discarded)},
        )
        return run.id

    async def _write(self, workspace_id: str, draft: Sequence[DraftEntry]) -> None:
        existing = await self.repos.semantic.list(workspace_id)
        rejected = await self.repos.semantic.rejected_keys(workspace_id)
        plan = merge_draft(
            [ExistingEntry(e.entry_key, e.status, e.source) for e in existing], draft, rejected
        )
        for entry in plan:
            await self.repos.semantic.upsert(
                workspace_id,
                kind=entry.kind,  # type: ignore[arg-type]
                entry_key=entry.entry_key,
                body=entry.body,
                status="candidate",
                source="auto",
                dataset_id=entry.dataset_id,
            )

    def _llm_context(
        self,
        datasets: Sequence[DatasetRecord],
        profiles: Mapping[str, Any],
        heuristic: Sequence[DraftEntry],
    ) -> dict[str, Any]:
        tables = []
        for d in datasets:
            profile = profiles.get(d.id)
            columns = list(profile.columns) if profile is not None else []
            settings = PrivacySettings(no_samples=bool(d.privacy_no_samples))
            tables.append(build_dataset_context(d, columns, None, settings))
        return {
            "task": "Susun draft model semantik bisnis untuk dataset berikut.",
            "datasets": tables,
            "heuristic_metrics": [e.body for e in heuristic if e.kind == "metric"],
            "kpi_catalog": kpi_catalog_summary(),
        }

    async def _from_llm(
        self,
        workspace_id: str,
        output: SemanticDraftOutput,
        datasets: Sequence[DatasetRecord],
        heuristic: Sequence[DraftEntry],
    ) -> tuple[list[DraftEntry], list[dict[str, Any]]]:
        discarded: list[dict[str, Any]] = []
        by_table = {d.table_name: d for d in datasets}
        base_columns = {e.entry_key: e for e in heuristic if e.kind == "column"}
        entries: list[DraftEntry] = []

        for col in output.columns:
            key = column_key(col.table, col.column)
            ds = by_table.get(col.table)
            base = base_columns.get(key)
            if ds is None or base is None:
                discarded.append({"entry_key": key, "reason": "kolom tidak ada di Dataset"})
                continue
            body = dict(base.body)
            if col.label:
                body["label"] = col.label
            if col.description:
                body["description"] = col.description
            if col.synonyms:
                body["synonyms"] = list(dict.fromkeys(col.synonyms))[:20]
            if col.default_aggregation:
                body["default_aggregation"] = col.default_aggregation
            entries.append(DraftEntry("column", key, body, ds.id))

        for m in output.metrics:
            key = metric_key(m.name)
            try:
                metric = BusinessMetric(
                    name=m.name,
                    label=m.label or "",
                    description=m.description or "",
                    expr=m.expr,
                    base_table=m.base_table,
                    synonyms=list(dict.fromkeys(m.synonyms))[:20],
                    good_direction=m.good_direction,
                    format=NumberFormat(style=m.format_style or "number"),
                )
            except ValidationError as exc:
                discarded.append({"entry_key": key, "reason": f"metrik tidak valid: {exc.errors()[0]['msg']}"})
                continue
            if self.validate_metric is not None:
                try:
                    await self.validate_metric(workspace_id, metric)
                except StudioError as exc:
                    discarded.append({"entry_key": key, "reason": exc.message})
                    continue
            entries.append(DraftEntry("metric", key, metric.model_dump(mode="json"), None))

        for t in output.glossary:
            try:
                term = GlossaryTerm(term=t.term, description=t.description, synonyms=t.synonyms[:20])
            except ValidationError as exc:
                discarded.append({"entry_key": term_key(t.term), "reason": exc.errors()[0]["msg"]})
                continue
            entries.append(DraftEntry("term", term_key(term.term), term.model_dump(mode="json"), None))
        return entries, discarded

    def _publish(self, workspace_id: str, event_type: str, data: dict[str, Any]) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(workspace_id, event_type, data)
        except Exception:
            logger.exception("Gagal menerbitkan event %s", event_type)

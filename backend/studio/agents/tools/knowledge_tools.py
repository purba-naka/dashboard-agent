"""Tool BI_Knowledge_Pack: ``get_bi_knowledge(topic)`` (Req 35.2–35.4).

Pengetahuan BI disimpan sebagai file Markdown lokal di ``agents/knowledge/``
(subfolder ``playbooks/``). Setiap file diawali front-matter::

    ---
    topic: playbooks/finance
    summary: ...
    ---

Topik tidak dikenal → ``UNKNOWN_TOPIC`` beserta daftar topik yang tersedia.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.tools import ToolContext

from studio.agents.tools.context import ToolServices
from studio.agents.tools.guard import llm_output, ok_result
from studio.api.errors import StudioError

__all__ = [
    "KNOWLEDGE_DIR",
    "MAX_TOPIC_BYTES",
    "KnowledgeTopic",
    "load_knowledge",
    "make_knowledge_tools",
]

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
MAX_TOPIC_BYTES = 16 * 1024


@dataclass(frozen=True)
class KnowledgeTopic:
    topic: str
    summary: str
    body: str


def _parse(path: Path, root: Path) -> KnowledgeTopic:
    text = path.read_text(encoding="utf-8")[:MAX_TOPIC_BYTES]
    default_topic = path.relative_to(root).with_suffix("").as_posix()
    topic, summary, body = default_topic, "", text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            header, body = text[3:end], text[end + 4 :]
            for line in header.splitlines():
                key, _, value = line.partition(":")
                if key.strip() == "topic" and value.strip():
                    topic = value.strip()
                elif key.strip() == "summary":
                    summary = value.strip()
    return KnowledgeTopic(topic=topic, summary=summary, body=body.strip())


@functools.lru_cache(maxsize=4)
def load_knowledge(root: Path = KNOWLEDGE_DIR) -> dict[str, KnowledgeTopic]:
    """Semua topik di ``root`` (rekursif), dikunci nama topik."""
    if not root.is_dir():
        return {}
    topics: dict[str, KnowledgeTopic] = {}
    for path in sorted(root.rglob("*.md")):
        item = _parse(path, root)
        topics[item.topic] = item
    return topics


def make_knowledge_tools(services: ToolServices, *, root: Path = KNOWLEDGE_DIR) -> dict[str, Callable[..., Any]]:
    guard = llm_output(max_sample_rows=services.sample_rows)

    @guard
    async def get_bi_knowledge(topic: str, tool_context: ToolContext) -> dict[str, Any]:
        """Ambil pengetahuan BI untuk satu topik.

        Args:
            topic: mis. principles, kpi_catalog, interaction, chart_selection,
                playbooks/sales, playbooks/finance, playbooks/marketing,
                playbooks/operations, playbooks/hr, playbooks/ecommerce.
                Topik tak dikenal mengembalikan daftar topik yang tersedia.
        """
        topics = load_knowledge(root)
        key = str(topic or "").strip().removesuffix(".md")
        item = topics.get(key) or topics.get(f"playbooks/{key}")
        if item is None:
            raise StudioError(
                "UNKNOWN_TOPIC",
                f"Topik '{topic}' tidak ada di BI_Knowledge_Pack.",
                {"available": [{"topic": t.topic, "summary": t.summary} for t in topics.values()]},
                http_status=404,
            )
        return ok_result(topic=item.topic, summary=item.summary, content=item.body)

    return {"get_bi_knowledge": get_bi_knowledge}

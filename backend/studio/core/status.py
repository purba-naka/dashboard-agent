"""Status turunan item Dashboard (murni; tanpa I/O).

Status dihitung saat ``GET /dashboards/{id}`` dan render, tidak disimpan:

- ``invalid`` ⇔ ``relations_used(query) ⊄ {relasi berstatus confirmed}``
  (Req 7.8). Menghapus/menolak relasi membuat item yang query-nya memakai
  relasi itu otomatis ``invalid`` saat dibaca.
- ``stale`` ⇔ ada ``dataset_id`` dengan
  ``datasets.data_version > item.dataset_versions[dataset_id]`` (Req 26.2).

Catatan keputusan:

- Hanya Insight_Card yang menyimpan ``dataset_versions`` (baseline saat
  dihitung). Chart tidak menyimpan data (data diikat ulang oleh render dari
  SQL-nya setiap kali, Req 26.2 "merender ulang semua chart"), sehingga chart
  tidak pernah ``stale``.
- Dataset yang tidak lagi ada di ``dataset_versions`` saat ini (mis. terhapus)
  dilewati untuk ``stale``; ``dataset_id`` tanpa baseline juga dilewati.
- ``query_meta`` ``None`` (query tidak ditemukan di katalog) dianggap tidak
  memakai relasi apa pun, sehingga tidak ``invalid``; kegagalan eksekusi
  dilaporkan oleh render sebagai status ``error``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import ConfigDict

from studio.core.models import DashboardContent, DashboardItem, InsightItem, StudioModel


class QueryMetaLike(Protocol):
    """Bentuk minimal metadata query yang dibutuhkan untuk status."""

    @property
    def relations_used(self) -> Collection[str]: ...


@dataclass(frozen=True)
class QueryMeta:
    """Metadata query tersimpan (baris ``queries``) yang relevan untuk status."""

    query_id: str
    #: ``relation_id`` yang dipakai sebagai kondisi JOIN (``ValidatedQuery``).
    relations_used: frozenset[str] = field(default_factory=frozenset)
    tables_used: tuple[str, ...] = ()
    dataset_ids: tuple[str, ...] = ()


class ItemStatus(StudioModel):
    """Status turunan per item; kontrak ``ItemStatus`` di ``types.ts``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    invalid: bool = False
    stale: bool = False


def is_invalid(
    query_meta: QueryMetaLike | None, confirmed_relation_ids: Collection[str]
) -> bool:
    """``True`` bila ada relasi yang dipakai query yang tidak lagi confirmed."""
    if query_meta is None:
        return False
    confirmed = (
        confirmed_relation_ids
        if isinstance(confirmed_relation_ids, (set, frozenset))
        else set(confirmed_relation_ids)
    )
    return any(rel_id not in confirmed for rel_id in query_meta.relations_used)


def is_stale(item: DashboardItem, dataset_versions: Mapping[str, int]) -> bool:
    """``True`` bila ada Dataset dengan ``data_version`` lebih baru dari baseline item."""
    if not isinstance(item, InsightItem):
        return False
    for dataset_id, baseline in item.dataset_versions.items():
        current = dataset_versions.get(dataset_id)
        if current is not None and current > baseline:
            return True
    return False


def item_status(
    item: DashboardItem,
    query_meta: QueryMetaLike | None,
    confirmed_relation_ids: Collection[str],
    dataset_versions: Mapping[str, int],
) -> ItemStatus:
    """Hitung status ``{invalid, stale}`` satu item.

    ``dataset_versions`` adalah ``dataset_id -> data_version`` saat ini.
    """
    return ItemStatus(
        invalid=is_invalid(query_meta, confirmed_relation_ids),
        stale=is_stale(item, dataset_versions),
    )


def item_query_id(item: DashboardItem) -> str:
    """``query_id`` sumber item (chart: dari ``spec``; insight: langsung)."""
    if isinstance(item, InsightItem):
        return item.query_id
    return item.spec.query_id


def dashboard_item_status(
    content: DashboardContent,
    query_metas: Mapping[str, QueryMetaLike],
    confirmed_relation_ids: Iterable[str],
    dataset_versions: Mapping[str, int],
) -> dict[str, ItemStatus]:
    """Status seluruh item Dashboard, dikunci oleh ``item_id``.

    ``query_metas`` dikunci oleh ``query_id``; query yang tidak ada dianggap
    tidak memakai relasi.
    """
    confirmed = frozenset(confirmed_relation_ids)
    return {
        item_id: item_status(
            item, query_metas.get(item_query_id(item)), confirmed, dataset_versions
        )
        for item_id, item in content.items.items()
    }


__all__ = [
    "QueryMetaLike",
    "QueryMeta",
    "ItemStatus",
    "is_invalid",
    "is_stale",
    "item_status",
    "item_query_id",
    "dashboard_item_status",
]

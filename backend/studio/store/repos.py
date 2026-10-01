"""Repositori Metadata_Store: Workspace, Upload, Dataset, DatasetProfile,
Relation, Query, Dashboard, PatchEvent, ChatSession, Proposal.

Setiap repositori adalah pembungkus tipis di atas :class:`~studio.store.db.Database`
yang mengembalikan *record* frozen bertipe (kolom ``*_json`` sudah di-parse ke
model domain). Konvensi:

* id baru = ULID (string 26 karakter), timestamp = ISO-8601 UTC
  (``datetime.now(UTC).isoformat()``); di record timestamp berupa ``datetime``
  aware UTC.
* ``owner_id`` default ``'local'`` (Req 29.3); Dataset dan Dashboard mewarisi
  ``owner_id`` Workspace-nya.
* Entitas tidak ditemukan → ``StudioError("NOT_FOUND", ..., http_status=404)``.
  Pelanggaran ``UNIQUE`` → ``CONFLICT`` (409), pelanggaran ``CHECK`` →
  ``VALIDATION_ERROR`` (422), foreign key yang dirujuk tidak ada → ``NOT_FOUND``.
* Semua method memakai helper ``db.execute/fetch_*`` sehingga otomatis ikut
  transaksi ``db.transaction()`` yang sedang aktif di task yang sama (dipakai
  Dashboard_Store untuk insert ``patch_events`` + update ``dashboards`` atomik).
* Sel hasil query dienkode JSON dengan ``date``/``datetime``/``time`` → string
  ISO, ``Decimal`` → ``float``, float non-finite → ``null``.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, TypeAdapter
from ulid import ULID

from studio.api.errors import StudioError
from studio.core.models import (
    COMMAND_ADAPTER,
    FILTER_SET_ADAPTER,
    OP_LIST_ADAPTER,
    Cell,
    ColumnInfo,
    ColumnProfile,
    DashboardContent,
    FilterSet,
    PatchEvent,
    PatchKind,
    PatchSource,
)
from studio.core.relations import RelationCandidate
from studio.store.db import Database

# ---------------------------------------------------------------------------
# Konstanta & tipe
# ---------------------------------------------------------------------------

#: ``owner_id`` default mode lokal tanpa autentikasi (Req 29.3).
LOCAL_OWNER_ID = "local"
#: Batas baris snapshot hasil query yang disimpan di tabel ``queries`` (Req 10.7).
MAX_SNAPSHOT_ROWS = 1000

UploadKind = Literal["csv", "xlsx"]
RelationStatus = Literal["candidate", "confirmed", "rejected", "deleted"]
Cardinality = Literal["one_to_one", "one_to_many", "many_to_many"]
QueryCreatedBy = Literal["agent", "user", "system"]
ProposalStatus = Literal["pending", "approved", "expired"]

_COLUMN_INFO_LIST = TypeAdapter(list[ColumnInfo])
_COLUMN_PROFILE_LIST = TypeAdapter(list[ColumnProfile])


# ---------------------------------------------------------------------------
# Helper umum
# ---------------------------------------------------------------------------


def new_id() -> str:
    """ULID baru sebagai string."""
    return str(ULID())


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Timestamp ISO-8601 UTC (``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``)."""
    return utc_now().isoformat()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _ts(value: str) -> datetime:
    parsed = _parse_ts(value)
    assert parsed is not None
    return parsed


def json_cell(value: Any) -> Any:
    """Nilai sel → nilai JSON (tanggal jadi string ISO, NaN/inf jadi ``None``)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return _to_jsonable(value)


def _to_jsonable(value: Any) -> Any:
    """Konversi rekursif ke struktur JSON-ready."""
    if value is None or isinstance(value, (bool, int, str, float, datetime, date, time, Decimal)):
        return json_cell(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        items = [_to_jsonable(v) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    return str(value)


def dumps(value: Any) -> str:
    """Enkode JSON kompak dan ketat (tanpa NaN) untuk kolom ``*_json``."""
    return json.dumps(
        _to_jsonable(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _loads(text: str | None, default: Any = None) -> Any:
    return default if text is None else json.loads(text)


def _dump_filters(filters: Iterable[Any] | None) -> str:
    """FilterSet (model Pydantic / dict JSON / ``core.filters.Predicate``) → JSON."""
    if not filters:
        return "[]"
    items: list[Any] = []
    for pred in filters:
        if isinstance(pred, (BaseModel, Mapping)):
            items.append(pred)
        else:  # Predicate frozen dari core/filters.py
            from studio.core.filters import to_model

            items.append(to_model(pred))
    validated = FILTER_SET_ADAPTER.validate_python(
        [p.model_dump() if isinstance(p, BaseModel) else p for p in items]
    )
    return dumps(FILTER_SET_ADAPTER.dump_python(validated, mode="json"))


def _load_filters(text: str | None) -> FilterSet:
    return FILTER_SET_ADAPTER.validate_python(_loads(text, []))


def not_found(entity: str, entity_id: str) -> StudioError:
    return StudioError(
        "NOT_FOUND",
        f"{entity} '{entity_id}' tidak ditemukan.",
        {"entity": entity, "id": entity_id},
        http_status=404,
    )


def _translate_integrity(exc: sqlite3.IntegrityError, entity: str) -> StudioError:
    msg = str(exc)
    upper = msg.upper()
    if "UNIQUE" in upper:
        return StudioError(
            "CONFLICT", f"{entity} bentrok dengan data yang sudah ada.", {"reason": msg}, 409
        )
    if "FOREIGN KEY" in upper:
        return StudioError(
            "NOT_FOUND", f"Entitas yang dirujuk {entity} tidak ditemukan.", {"reason": msg}, 404
        )
    if "CHECK" in upper or "NOT NULL" in upper:
        return StudioError(
            "VALIDATION_ERROR", f"Nilai {entity} tidak valid.", {"reason": msg}, 422
        )
    return StudioError("CONFLICT", f"Gagal menyimpan {entity}.", {"reason": msg}, 409)


class _Record:
    """Mixin record: ``to_json()`` menghasilkan dict JSON-ready."""

    def to_json(self) -> dict[str, Any]:
        return {f.name: _to_jsonable(getattr(self, f.name)) for f in fields(self)}  # type: ignore[arg-type]


class _Repo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def _insert(self, entity: str, sql: str, params: Sequence[Any]) -> int:
        try:
            return await self.db.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise _translate_integrity(exc, entity) from exc

    async def _update(self, entity: str, sql: str, params: Sequence[Any]) -> int:
        return await self._insert(entity, sql, params)


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkspaceRecord(_Record):
    id: str
    owner_id: str
    name: str
    created_at: datetime
    updated_at: datetime


def _workspace(row: Any) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        owner_id=row["owner_id"],
        name=row["name"],
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


class WorkspaceRepo(_Repo):
    """CRUD Workspace (Req 1.1, 1.3, 1.4). Hapus = cascade seluruh metadata."""

    async def create(self, name: str, *, owner_id: str = LOCAL_OWNER_ID) -> WorkspaceRecord:
        ws_id, now = new_id(), utc_now_iso()
        await self._insert(
            "Workspace",
            "INSERT INTO workspaces (id, owner_id, name, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (ws_id, owner_id, name, now, now),
        )
        return await self.get(ws_id)

    async def get_or_none(self, workspace_id: str) -> WorkspaceRecord | None:
        row = await self.db.fetch_one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))
        return None if row is None else _workspace(row)

    async def get(self, workspace_id: str) -> WorkspaceRecord:
        record = await self.get_or_none(workspace_id)
        if record is None:
            raise not_found("Workspace", workspace_id)
        return record

    async def list(self, *, owner_id: str | None = None) -> list[WorkspaceRecord]:
        if owner_id is None:
            rows = await self.db.fetch_all("SELECT * FROM workspaces ORDER BY created_at, id")
        else:
            rows = await self.db.fetch_all(
                "SELECT * FROM workspaces WHERE owner_id = ? ORDER BY created_at, id", (owner_id,)
            )
        return [_workspace(r) for r in rows]

    async def rename(self, workspace_id: str, name: str) -> WorkspaceRecord:
        count = await self._update(
            "Workspace",
            "UPDATE workspaces SET name = ?, updated_at = ? WHERE id = ?",
            (name, utc_now_iso(), workspace_id),
        )
        if count == 0:
            raise not_found("Workspace", workspace_id)
        return await self.get(workspace_id)

    async def touch(self, workspace_id: str) -> None:
        await self.db.execute(
            "UPDATE workspaces SET updated_at = ? WHERE id = ?", (utc_now_iso(), workspace_id)
        )

    async def delete(self, workspace_id: str) -> None:
        """Hapus Workspace beserta seluruh baris turunan (ON DELETE CASCADE).

        Pembersihan file upload dan sesi ADK dilakukan pemanggil (Req 1.4).
        """
        count = await self.db.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        if count == 0:
            raise not_found("Workspace", workspace_id)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadRecord(_Record):
    id: str
    workspace_id: str
    original_name: str
    stored_path: str
    kind: UploadKind
    size_bytes: int
    created_at: datetime


def _upload(row: Any) -> UploadRecord:
    return UploadRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        original_name=row["original_name"],
        stored_path=row["stored_path"],
        kind=row["kind"],
        size_bytes=row["size_bytes"],
        created_at=_ts(row["created_at"]),
    )


class UploadRepo(_Repo):
    """File upload asli di ``data/uploads/{workspace_id}/`` (``stored_path`` relatif)."""

    async def create(
        self,
        workspace_id: str,
        *,
        original_name: str,
        stored_path: str,
        kind: UploadKind,
        size_bytes: int,
        upload_id: str | None = None,
    ) -> UploadRecord:
        up_id = upload_id or new_id()
        await self._insert(
            "Upload",
            "INSERT INTO uploads (id, workspace_id, original_name, stored_path, kind,"
            " size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (up_id, workspace_id, original_name, stored_path, kind, size_bytes, utc_now_iso()),
        )
        return await self.get(up_id)

    async def get_or_none(self, upload_id: str) -> UploadRecord | None:
        row = await self.db.fetch_one("SELECT * FROM uploads WHERE id = ?", (upload_id,))
        return None if row is None else _upload(row)

    async def get(self, upload_id: str) -> UploadRecord:
        record = await self.get_or_none(upload_id)
        if record is None:
            raise not_found("Upload", upload_id)
        return record

    async def list_by_workspace(self, workspace_id: str) -> list[UploadRecord]:
        rows = await self.db.fetch_all(
            "SELECT * FROM uploads WHERE workspace_id = ? ORDER BY created_at, id", (workspace_id,)
        )
        return [_upload(r) for r in rows]

    async def delete(self, upload_id: str) -> None:
        try:
            count = await self.db.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
        except sqlite3.IntegrityError as exc:
            raise StudioError(
                "CONFLICT",
                "Upload masih dirujuk oleh Dataset.",
                {"id": upload_id, "reason": str(exc)},
                409,
            ) from exc
        if count == 0:
            raise not_found("Upload", upload_id)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Satu entri pemetaan nama kolom asli → hasil normalisasi (Req 2.5)."""

    original: str
    normalized: str


@dataclass(frozen=True, slots=True)
class DatasetRecord(_Record):
    """Baris ``datasets``; memenuhi protokol ``DatasetMeta`` Privacy_Guard."""

    id: str
    workspace_id: str
    owner_id: str
    upload_id: str | None
    table_name: str
    source_name: str
    sheet_name: str | None
    parquet_path: str
    schema: tuple[ColumnInfo, ...]
    column_mapping: tuple[ColumnMapping, ...]
    row_count: int
    data_version: int
    data_updated_at: datetime
    privacy_no_samples: bool
    created_at: datetime


def _mapping_entries(mapping: Iterable[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for entry in mapping:
        if isinstance(entry, ColumnMapping):
            out.append({"original": entry.original, "normalized": entry.normalized})
        elif isinstance(entry, Mapping):
            out.append({"original": str(entry["original"]), "normalized": str(entry["normalized"])})
        else:  # pasangan (original, normalized)
            original, normalized = entry
            out.append({"original": str(original), "normalized": str(normalized)})
    return out


def _schema_json(schema: Iterable[ColumnInfo | Mapping[str, Any]]) -> str:
    cols = _COLUMN_INFO_LIST.validate_python(
        [c.model_dump() if isinstance(c, BaseModel) else dict(c) for c in schema]
    )
    return dumps(cols)


def _dataset(row: Any) -> DatasetRecord:
    return DatasetRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        upload_id=row["upload_id"],
        table_name=row["table_name"],
        source_name=row["source_name"],
        sheet_name=row["sheet_name"],
        parquet_path=row["parquet_path"],
        schema=tuple(_COLUMN_INFO_LIST.validate_json(row["schema_json"])),
        column_mapping=tuple(
            ColumnMapping(original=e["original"], normalized=e["normalized"])
            for e in _loads(row["column_mapping_json"], [])
        ),
        row_count=row["row_count"],
        data_version=row["data_version"],
        data_updated_at=_ts(row["data_updated_at"]),
        privacy_no_samples=bool(row["privacy_no_samples"]),
        created_at=_ts(row["created_at"]),
    )


class DatasetRepo(_Repo):
    """Dataset terdaftar (Req 1.5, 4.2); ``table_name`` unik per Workspace."""

    async def create(
        self,
        workspace_id: str,
        *,
        table_name: str,
        source_name: str,
        parquet_path: str,
        schema: Iterable[ColumnInfo | Mapping[str, Any]],
        column_mapping: Iterable[Any],
        row_count: int,
        upload_id: str | None = None,
        sheet_name: str | None = None,
        privacy_no_samples: bool = False,
        dataset_id: str | None = None,
    ) -> DatasetRecord:
        ds_id, now = dataset_id or new_id(), utc_now_iso()
        # owner_id diwarisi dari Workspace; rowcount 0 = Workspace tidak ada.
        count = await self._insert(
            "Dataset",
            "INSERT INTO datasets (id, workspace_id, owner_id, upload_id, table_name,"
            " source_name, sheet_name, parquet_path, schema_json, column_mapping_json,"
            " row_count, data_version, data_updated_at, privacy_no_samples, created_at)"
            " SELECT ?, id, owner_id, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?"
            " FROM workspaces WHERE id = ?",
            (
                ds_id,
                upload_id,
                table_name,
                source_name,
                sheet_name,
                parquet_path,
                _schema_json(schema),
                dumps(_mapping_entries(column_mapping)),
                row_count,
                now,
                int(privacy_no_samples),
                now,
                workspace_id,
            ),
        )
        if count == 0:
            raise not_found("Workspace", workspace_id)
        return await self.get(ds_id)

    async def get_or_none(self, dataset_id: str) -> DatasetRecord | None:
        row = await self.db.fetch_one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        return None if row is None else _dataset(row)

    async def get(self, dataset_id: str, *, workspace_id: str | None = None) -> DatasetRecord:
        record = await self.get_or_none(dataset_id)
        if record is None or (workspace_id is not None and record.workspace_id != workspace_id):
            raise not_found("Dataset", dataset_id)
        return record

    async def get_by_table(self, workspace_id: str, table_name: str) -> DatasetRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM datasets WHERE workspace_id = ? AND table_name = ?",
            (workspace_id, table_name),
        )
        return None if row is None else _dataset(row)

    async def list_by_workspace(self, workspace_id: str) -> list[DatasetRecord]:
        rows = await self.db.fetch_all(
            "SELECT * FROM datasets WHERE workspace_id = ? ORDER BY created_at, id",
            (workspace_id,),
        )
        return [_dataset(r) for r in rows]

    async def list_table_names(self, workspace_id: str) -> list[str]:
        rows = await self.db.fetch_all(
            "SELECT table_name FROM datasets WHERE workspace_id = ? ORDER BY table_name",
            (workspace_id,),
        )
        return [r["table_name"] for r in rows]

    async def data_versions(self, workspace_id: str) -> dict[str, int]:
        """``dataset_id → data_version`` untuk status stale (Req 26.2)."""
        rows = await self.db.fetch_all(
            "SELECT id, data_version FROM datasets WHERE workspace_id = ?", (workspace_id,)
        )
        return {r["id"]: r["data_version"] for r in rows}

    async def update_file(
        self,
        dataset_id: str,
        *,
        parquet_path: str,
        schema: Iterable[ColumnInfo | Mapping[str, Any]],
        column_mapping: Iterable[Any],
        row_count: int,
        upload_id: str | None = None,
    ) -> DatasetRecord:
        """Re-upload: ganti file & skema, ``data_version += 1``, ``data_updated_at = now``."""
        count = await self._update(
            "Dataset",
            "UPDATE datasets SET upload_id = COALESCE(?, upload_id), parquet_path = ?,"
            " schema_json = ?, column_mapping_json = ?, row_count = ?,"
            " data_version = data_version + 1, data_updated_at = ? WHERE id = ?",
            (
                upload_id,
                parquet_path,
                _schema_json(schema),
                dumps(_mapping_entries(column_mapping)),
                row_count,
                utc_now_iso(),
                dataset_id,
            ),
        )
        if count == 0:
            raise not_found("Dataset", dataset_id)
        return await self.get(dataset_id)

    async def set_privacy(self, dataset_id: str, no_samples: bool) -> DatasetRecord:
        """Toggle "jangan kirim sample rows" (Req 27.4)."""
        count = await self.db.execute(
            "UPDATE datasets SET privacy_no_samples = ? WHERE id = ?",
            (int(bool(no_samples)), dataset_id),
        )
        if count == 0:
            raise not_found("Dataset", dataset_id)
        return await self.get(dataset_id)

    async def delete(self, dataset_id: str) -> None:
        count = await self.db.execute("DELETE FROM datasets WHERE id = ?", (dataset_id,))
        if count == 0:
            raise not_found("Dataset", dataset_id)


# ---------------------------------------------------------------------------
# DatasetProfile
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DatasetProfileRecord(_Record):
    dataset_id: str
    data_version: int
    columns: tuple[ColumnProfile, ...]
    #: ``{duplicate_rows, mixed_type_columns[], null_pct{}}``
    quality: dict[str, Any]
    computed_at: datetime


def _profile(row: Any) -> DatasetProfileRecord:
    return DatasetProfileRecord(
        dataset_id=row["dataset_id"],
        data_version=row["data_version"],
        columns=tuple(_COLUMN_PROFILE_LIST.validate_json(row["columns_json"])),
        quality=_loads(row["quality_json"], {}),
        computed_at=_ts(row["computed_at"]),
    )


class DatasetProfileRepo(_Repo):
    """Hasil profiling per Dataset (satu baris per dataset; di-upsert per ``data_version``)."""

    async def upsert(
        self,
        dataset_id: str,
        *,
        data_version: int,
        columns: Iterable[ColumnProfile | Mapping[str, Any]],
        quality: Mapping[str, Any],
    ) -> DatasetProfileRecord:
        cols = _COLUMN_PROFILE_LIST.validate_python(
            [c.model_dump() if isinstance(c, BaseModel) else dict(c) for c in columns]
        )
        await self._insert(
            "DatasetProfile",
            "INSERT INTO dataset_profiles (dataset_id, data_version, columns_json,"
            " quality_json, computed_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (dataset_id) DO UPDATE SET data_version = excluded.data_version,"
            " columns_json = excluded.columns_json, quality_json = excluded.quality_json,"
            " computed_at = excluded.computed_at",
            (dataset_id, data_version, dumps(cols), dumps(dict(quality)), utc_now_iso()),
        )
        return await self.get(dataset_id)

    async def get_or_none(self, dataset_id: str) -> DatasetProfileRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM dataset_profiles WHERE dataset_id = ?", (dataset_id,)
        )
        return None if row is None else _profile(row)

    async def get(self, dataset_id: str) -> DatasetProfileRecord:
        record = await self.get_or_none(dataset_id)
        if record is None:
            raise not_found("DatasetProfile", dataset_id)
        return record

    async def list_by_workspace(self, workspace_id: str) -> dict[str, DatasetProfileRecord]:
        rows = await self.db.fetch_all(
            "SELECT p.* FROM dataset_profiles p JOIN datasets d ON d.id = p.dataset_id"
            " WHERE d.workspace_id = ?",
            (workspace_id,),
        )
        return {r["dataset_id"]: _profile(r) for r in rows}

    async def delete(self, dataset_id: str) -> None:
        await self.db.execute("DELETE FROM dataset_profiles WHERE dataset_id = ?", (dataset_id,))


# ---------------------------------------------------------------------------
# Relation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationRecord(_Record):
    """Baris ``relations`` + nama tabel SQL kedua sisi (join ke ``datasets``)."""

    id: str
    workspace_id: str
    candidate_key: str
    from_dataset_id: str
    from_table: str
    from_column: str
    to_dataset_id: str
    to_table: str
    to_column: str
    cardinality: Cardinality
    overlap_pct: float
    status: RelationStatus
    created_at: datetime
    decided_at: datetime | None


_RELATION_SELECT = (
    "SELECT r.*, fd.table_name AS from_table, td.table_name AS to_table FROM relations r"
    " JOIN datasets fd ON fd.id = r.from_dataset_id"
    " JOIN datasets td ON td.id = r.to_dataset_id"
)


def _relation(row: Any) -> RelationRecord:
    return RelationRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        candidate_key=row["candidate_key"],
        from_dataset_id=row["from_dataset_id"],
        from_table=row["from_table"],
        from_column=row["from_column"],
        to_dataset_id=row["to_dataset_id"],
        to_table=row["to_table"],
        to_column=row["to_column"],
        cardinality=row["cardinality"],
        overlap_pct=float(row["overlap_pct"]),
        status=row["status"],
        created_at=_ts(row["created_at"]),
        decided_at=_parse_ts(row["decided_at"]),
    )


class RelationRepo(_Repo):
    """Relation_Candidate & Confirmed_Relation (Req 7.2, 7.4, 7.5, 7.8).

    Transisi status::

        (baru) ──upsert──▶ candidate ──confirm──▶ confirmed
                             │  ▲                    │
                          reject│ upsert (dari deleted) │ delete
                             ▼  │                    ▼
                          rejected ◀──reject──   deleted

    * ``upsert_candidates`` tidak pernah menghidupkan kembali kandidat
      ``rejected``/``confirmed``: statusnya tetap, overlap/kardinalitas hanya
      diperbarui untuk status ``candidate``. Baris ``deleted`` yang terdeteksi
      ulang kembali menjadi ``candidate`` (dengan id yang sama).
    * ``confirm``/``reject`` pada relasi ``deleted`` → ``NOT_FOUND``.
    """

    async def upsert_candidates(
        self, workspace_id: str, candidates: Iterable[RelationCandidate]
    ) -> list[RelationRecord]:
        """Simpan kandidat by ``candidate_key``; kembalikan record semua key tersebut."""
        cands = list(candidates)
        if not cands:
            return []
        now = utc_now_iso()
        async with self.db.transaction():
            for c in cands:
                await self._insert(
                    "Relation",
                    "INSERT INTO relations (id, workspace_id, candidate_key, from_dataset_id,"
                    " from_column, to_dataset_id, to_column, cardinality, overlap_pct, status,"
                    " created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, NULL)"
                    " ON CONFLICT (workspace_id, candidate_key) DO UPDATE SET"
                    " from_dataset_id = excluded.from_dataset_id,"
                    " from_column = excluded.from_column,"
                    " to_dataset_id = excluded.to_dataset_id,"
                    " to_column = excluded.to_column,"
                    " cardinality = excluded.cardinality,"
                    " overlap_pct = excluded.overlap_pct,"
                    " status = 'candidate',"
                    " decided_at = NULL"
                    " WHERE relations.status IN ('candidate', 'deleted')",
                    (
                        new_id(),
                        workspace_id,
                        c.candidate_key,
                        c.from_dataset_id,
                        c.from_column,
                        c.to_dataset_id,
                        c.to_column,
                        c.cardinality,
                        float(c.overlap_pct),
                        now,
                    ),
                )
            keys = list(dict.fromkeys(c.candidate_key for c in cands))
            placeholders = ",".join("?" for _ in keys)
            rows = await self.db.fetch_all(
                f"{_RELATION_SELECT} WHERE r.workspace_id = ? AND r.candidate_key IN ({placeholders})",
                (workspace_id, *keys),
            )
        by_key = {r["candidate_key"]: _relation(r) for r in rows}
        return [by_key[k] for k in keys if k in by_key]

    async def get_or_none(self, relation_id: str) -> RelationRecord | None:
        row = await self.db.fetch_one(f"{_RELATION_SELECT} WHERE r.id = ?", (relation_id,))
        return None if row is None else _relation(row)

    async def get(self, relation_id: str, *, workspace_id: str | None = None) -> RelationRecord:
        record = await self.get_or_none(relation_id)
        if record is None or (workspace_id is not None and record.workspace_id != workspace_id):
            raise not_found("Relation", relation_id)
        return record

    async def get_by_key(self, workspace_id: str, candidate_key: str) -> RelationRecord | None:
        row = await self.db.fetch_one(
            f"{_RELATION_SELECT} WHERE r.workspace_id = ? AND r.candidate_key = ?",
            (workspace_id, candidate_key),
        )
        return None if row is None else _relation(row)

    async def list(
        self,
        workspace_id: str,
        *,
        status: RelationStatus | Collection[RelationStatus] | None = None,
    ) -> list[RelationRecord]:
        """Daftar relasi Workspace; default semua kecuali ``deleted``."""
        if status is None:
            statuses: list[str] = ["candidate", "confirmed", "rejected"]
        elif isinstance(status, str):
            statuses = [status]
        else:
            statuses = list(status)
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        rows = await self.db.fetch_all(
            f"{_RELATION_SELECT} WHERE r.workspace_id = ? AND r.status IN ({placeholders})"
            " ORDER BY r.created_at, r.id",
            (workspace_id, *statuses),
        )
        return [_relation(r) for r in rows]

    async def list_confirmed(self, workspace_id: str) -> list[RelationRecord]:
        return await self.list(workspace_id, status="confirmed")

    async def confirmed_ids(self, workspace_id: str) -> frozenset[str]:
        rows = await self.db.fetch_all(
            "SELECT id FROM relations WHERE workspace_id = ? AND status = 'confirmed'",
            (workspace_id,),
        )
        return frozenset(r["id"] for r in rows)

    async def rejected_keys(self, workspace_id: str) -> frozenset[str]:
        """``candidate_key`` yang ditolak; tidak boleh diusulkan lagi (Req 7.5)."""
        rows = await self.db.fetch_all(
            "SELECT candidate_key FROM relations WHERE workspace_id = ? AND status = 'rejected'",
            (workspace_id,),
        )
        return frozenset(r["candidate_key"] for r in rows)

    async def _set_status(
        self,
        relation_id: str,
        status: RelationStatus,
        workspace_id: str | None,
    ) -> RelationRecord:
        sql = (
            "UPDATE relations SET status = ?, decided_at = ?"
            " WHERE id = ? AND status != 'deleted'"
        )
        params: list[Any] = [status, utc_now_iso(), relation_id]
        if workspace_id is not None:
            sql += " AND workspace_id = ?"
            params.append(workspace_id)
        count = await self.db.execute(sql, params)
        if count == 0:
            raise not_found("Relation", relation_id)
        return await self.get(relation_id)

    async def confirm(self, relation_id: str, *, workspace_id: str | None = None) -> RelationRecord:
        """Simpan sebagai Confirmed_Relation (Req 7.4)."""
        return await self._set_status(relation_id, "confirmed", workspace_id)

    async def reject(self, relation_id: str, *, workspace_id: str | None = None) -> RelationRecord:
        """Tandai ditolak (Req 7.5)."""
        return await self._set_status(relation_id, "rejected", workspace_id)

    async def delete(self, relation_id: str, *, workspace_id: str | None = None) -> RelationRecord:
        """Soft delete → status ``deleted``; item dependen menjadi invalid (Req 7.8)."""
        return await self._set_status(relation_id, "deleted", workspace_id)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueryRecord(_Record):
    """Query tersimpan (Req 10.7, 14.7); memenuhi ``QueryMetaLike`` (``relations_used``)."""

    id: str
    workspace_id: str
    sql: str
    tables_used: tuple[str, ...]
    dataset_ids: tuple[str, ...]
    relations_used: frozenset[str]
    #: ``output_col → {table, column} | None``
    lineage: dict[str, dict[str, str] | None]
    output_schema: tuple[ColumnInfo, ...]
    #: Snapshot hasil (≤ 1.000 baris), sel sudah dalam bentuk JSON.
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    filters: FilterSet
    executed_at: datetime
    created_by: QueryCreatedBy
    truncated_for_storage: bool = field(default=False)

    @property
    def query_id(self) -> str:
        return self.id

    @property
    def columns(self) -> tuple[ColumnInfo, ...]:
        return self.output_schema


def _query(row: Any) -> QueryRecord:
    snapshot = _loads(row["result_snapshot_json"], [])
    return QueryRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        sql=row["sql"],
        tables_used=tuple(_loads(row["tables_used_json"], [])),
        dataset_ids=tuple(_loads(row["dataset_ids_json"], [])),
        relations_used=frozenset(_loads(row["relations_used_json"], [])),
        lineage=_loads(row["lineage_json"], {}),
        output_schema=tuple(_COLUMN_INFO_LIST.validate_json(row["output_schema_json"])),
        rows=tuple(tuple(r) for r in snapshot),
        row_count=row["row_count"],
        filters=_load_filters(row["filters_json"]),
        executed_at=_ts(row["executed_at"]),
        created_by=row["created_by"],
        truncated_for_storage=row["row_count"] > len(snapshot),
    )


def _lineage_json(lineage: Mapping[str, Any] | None) -> str:
    out: dict[str, Any] = {}
    for col, src in (lineage or {}).items():
        if src is None:
            out[str(col)] = None
        elif isinstance(src, Mapping):
            out[str(col)] = {"table": str(src["table"]), "column": str(src["column"])}
        else:  # ColumnRef atau objek dengan atribut table/column
            out[str(col)] = {"table": str(src.table), "column": str(src.column)}
    return dumps(out)


class QueryRepo(_Repo):
    """Riwayat SQL tereksekusi + snapshot hasil ≤ 1.000 baris (Req 10.7). SQL disimpan apa adanya."""

    async def create(
        self,
        workspace_id: str,
        *,
        sql: str,
        output_schema: Iterable[ColumnInfo | Mapping[str, Any]],
        rows: Sequence[Sequence[Cell]],
        row_count: int | None = None,
        tables_used: Iterable[str] = (),
        dataset_ids: Iterable[str] = (),
        relations_used: Iterable[str] = (),
        lineage: Mapping[str, Any] | None = None,
        filters: Iterable[Any] | None = None,
        created_by: QueryCreatedBy = "agent",
        query_id: str | None = None,
        executed_at: datetime | None = None,
    ) -> QueryRecord:
        q_id = query_id or new_id()
        total = len(rows) if row_count is None else int(row_count)
        snapshot = [[json_cell(v) for v in r] for r in rows[:MAX_SNAPSHOT_ROWS]]
        await self._insert(
            "Query",
            "INSERT INTO queries (id, workspace_id, sql, tables_used_json, dataset_ids_json,"
            " relations_used_json, lineage_json, output_schema_json, result_snapshot_json,"
            " row_count, filters_json, executed_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                q_id,
                workspace_id,
                sql,
                dumps(list(dict.fromkeys(tables_used))),
                dumps(list(dict.fromkeys(dataset_ids))),
                dumps(list(dict.fromkeys(relations_used))),
                _lineage_json(lineage),
                _schema_json(output_schema),
                dumps(snapshot),
                total,
                _dump_filters(filters),
                _iso(executed_at) if executed_at else utc_now_iso(),
                created_by,
            ),
        )
        return await self.get(q_id)

    async def get_or_none(self, query_id: str) -> QueryRecord | None:
        row = await self.db.fetch_one("SELECT * FROM queries WHERE id = ?", (query_id,))
        return None if row is None else _query(row)

    async def get(self, query_id: str, *, workspace_id: str | None = None) -> QueryRecord:
        record = await self.get_or_none(query_id)
        if record is None or (workspace_id is not None and record.workspace_id != workspace_id):
            raise not_found("Query", query_id)
        return record

    async def get_many(self, query_ids: Iterable[str]) -> dict[str, QueryRecord]:
        ids = list(dict.fromkeys(query_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = await self.db.fetch_all(
            f"SELECT * FROM queries WHERE id IN ({placeholders})", ids
        )
        return {r["id"]: _query(r) for r in rows}

    async def list_by_workspace(self, workspace_id: str, *, limit: int = 100) -> list[QueryRecord]:
        rows = await self.db.fetch_all(
            "SELECT * FROM queries WHERE workspace_id = ? ORDER BY executed_at DESC, id DESC"
            " LIMIT ?",
            (workspace_id, limit),
        )
        return [_query(r) for r in rows]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DashboardRecord(_Record):
    id: str
    workspace_id: str
    owner_id: str
    title: str
    version: int
    content: DashboardContent
    #: Stack patch id, bawah → atas.
    undo_stack: tuple[str, ...]
    redo_stack: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


def _dashboard(row: Any) -> DashboardRecord:
    return DashboardRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        owner_id=row["owner_id"],
        title=row["title"],
        version=row["version"],
        content=DashboardContent.model_validate_json(row["content_json"]),
        undo_stack=tuple(_loads(row["undo_stack_json"], [])),
        redo_stack=tuple(_loads(row["redo_stack_json"], [])),
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


class DashboardRepo(_Repo):
    """Penyimpanan baris ``dashboards``. Logika apply/undo/redo ada di ``dashboard_store``."""

    async def create(
        self,
        workspace_id: str,
        title: str,
        *,
        content: DashboardContent | None = None,
        dashboard_id: str | None = None,
    ) -> DashboardRecord:
        """Dashboard baru versi 0 dengan konten awal (default kosong berjudul ``title``)."""
        db_id, now = dashboard_id or new_id(), utc_now_iso()
        initial = content if content is not None else DashboardContent(title=title)
        count = await self._insert(
            "Dashboard",
            "INSERT INTO dashboards (id, workspace_id, owner_id, title, version, content_json,"
            " undo_stack_json, redo_stack_json, created_at, updated_at)"
            " SELECT ?, id, owner_id, ?, 0, ?, '[]', '[]', ?, ? FROM workspaces WHERE id = ?",
            (db_id, initial.title, initial.model_dump_json(), now, now, workspace_id),
        )
        if count == 0:
            raise not_found("Workspace", workspace_id)
        return await self.get(db_id)

    async def get_or_none(self, dashboard_id: str) -> DashboardRecord | None:
        row = await self.db.fetch_one("SELECT * FROM dashboards WHERE id = ?", (dashboard_id,))
        return None if row is None else _dashboard(row)

    async def get(self, dashboard_id: str) -> DashboardRecord:
        record = await self.get_or_none(dashboard_id)
        if record is None:
            raise not_found("Dashboard", dashboard_id)
        return record

    async def get_version(self, dashboard_id: str) -> int:
        version = await self.db.fetch_value(
            "SELECT version FROM dashboards WHERE id = ?", (dashboard_id,)
        )
        if version is None:
            raise not_found("Dashboard", dashboard_id)
        return int(version)

    async def list_by_workspace(self, workspace_id: str) -> list[DashboardRecord]:
        rows = await self.db.fetch_all(
            "SELECT * FROM dashboards WHERE workspace_id = ? ORDER BY created_at, id",
            (workspace_id,),
        )
        return [_dashboard(r) for r in rows]

    async def update(
        self,
        dashboard_id: str,
        *,
        expected_version: int,
        new_version: int,
        content: DashboardContent,
        undo_stack: Sequence[str],
        redo_stack: Sequence[str],
    ) -> int:
        """Update optimistik ``WHERE id = ? AND version = ?``; kembalikan rowcount (0/1).

        ``title`` kolom diselaraskan dengan ``content.title``.
        """
        return await self.db.execute(
            "UPDATE dashboards SET version = ?, title = ?, content_json = ?,"
            " undo_stack_json = ?, redo_stack_json = ?, updated_at = ?"
            " WHERE id = ? AND version = ?",
            (
                new_version,
                content.title,
                content.model_dump_json(),
                dumps(list(undo_stack)),
                dumps(list(redo_stack)),
                utc_now_iso(),
                dashboard_id,
                expected_version,
            ),
        )

    async def delete(self, dashboard_id: str) -> None:
        count = await self.db.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))
        if count == 0:
            raise not_found("Dashboard", dashboard_id)


# ---------------------------------------------------------------------------
# PatchEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PatchEventRecord(_Record):
    """Baris ``patch_events``; ``event`` = model domain ``PatchEvent``."""

    id: str
    dashboard_id: str
    version: int
    base_version: int
    source: PatchSource
    kind: PatchKind
    target_patch_id: str | None
    #: Command asli (JSON) untuk audit & ringkasan.
    command: dict[str, Any] | None
    event: PatchEvent
    actor_run_id: str | None
    created_at: datetime


def _patch(row: Any) -> PatchEventRecord:
    event = PatchEvent(
        id=row["id"],
        dashboard_id=row["dashboard_id"],
        version=row["version"],
        base_version=row["base_version"],
        source=row["source"],
        kind=row["kind"],
        target_patch_id=row["target_patch_id"],
        ops=OP_LIST_ADAPTER.validate_json(row["ops_json"]),
        inverse_ops=OP_LIST_ADAPTER.validate_json(row["inverse_ops_json"]),
        created_at=_ts(row["created_at"]),
    )
    return PatchEventRecord(
        id=event.id,
        dashboard_id=event.dashboard_id,
        version=event.version,
        base_version=event.base_version,
        source=event.source,
        kind=event.kind,
        target_patch_id=event.target_patch_id,
        command=_loads(row["command_json"], None),
        event=event,
        actor_run_id=row["actor_run_id"],
        created_at=event.created_at,
    )


def _command_json(command: Any) -> str:
    if command is None:
        return "null"
    if isinstance(command, BaseModel):
        return dumps(COMMAND_ADAPTER.dump_python(command, mode="json"))
    return dumps(command)


class PatchEventRepo(_Repo):
    """Log Patch_Event per Dashboard (``UNIQUE (dashboard_id, version)``)."""

    async def insert(
        self,
        event: PatchEvent,
        *,
        command: Any = None,
        actor_run_id: str | None = None,
    ) -> PatchEventRecord:
        await self._insert(
            "PatchEvent",
            "INSERT INTO patch_events (id, dashboard_id, version, base_version, source, kind,"
            " target_patch_id, command_json, ops_json, inverse_ops_json, actor_run_id,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.dashboard_id,
                event.version,
                event.base_version,
                event.source,
                event.kind,
                event.target_patch_id,
                _command_json(command),
                OP_LIST_ADAPTER.dump_json(event.ops).decode("utf-8"),
                OP_LIST_ADAPTER.dump_json(event.inverse_ops).decode("utf-8"),
                actor_run_id,
                _iso(event.created_at),
            ),
        )
        return await self.get(event.id)

    async def get_or_none(self, patch_id: str) -> PatchEventRecord | None:
        row = await self.db.fetch_one("SELECT * FROM patch_events WHERE id = ?", (patch_id,))
        return None if row is None else _patch(row)

    async def get(self, patch_id: str) -> PatchEventRecord:
        record = await self.get_or_none(patch_id)
        if record is None:
            raise not_found("PatchEvent", patch_id)
        return record

    async def list_by_dashboard(self, dashboard_id: str) -> list[PatchEventRecord]:
        rows = await self.db.fetch_all(
            "SELECT * FROM patch_events WHERE dashboard_id = ? ORDER BY version",
            (dashboard_id,),
        )
        return [_patch(r) for r in rows]

    async def list_since(self, dashboard_id: str, since_version: int) -> list[PatchEventRecord]:
        """Patch dengan ``version > since_version``, urut versi naik."""
        rows = await self.db.fetch_all(
            "SELECT * FROM patch_events WHERE dashboard_id = ? AND version > ? ORDER BY version",
            (dashboard_id, since_version),
        )
        return [_patch(r) for r in rows]

    async def list_by_source_since(
        self, dashboard_id: str, since_version: int, source: PatchSource
    ) -> list[PatchEventRecord]:
        """Patch ``source`` tertentu sejak versi (mis. edit manual sejak giliran agent terakhir)."""
        rows = await self.db.fetch_all(
            "SELECT * FROM patch_events WHERE dashboard_id = ? AND version > ? AND source = ?"
            " ORDER BY version",
            (dashboard_id, since_version, source),
        )
        return [_patch(r) for r in rows]


# ---------------------------------------------------------------------------
# ChatSession
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChatSessionRecord(_Record):
    id: str
    workspace_id: str
    title: str
    created_at: datetime
    last_agent_version: int


def _chat_session(row: Any) -> ChatSessionRecord:
    return ChatSessionRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        title=row["title"],
        created_at=_ts(row["created_at"]),
        last_agent_version=row["last_agent_version"],
    )


class ChatSessionRepo(_Repo):
    """Metadata sesi chat (id = id sesi ADK)."""

    async def create(
        self,
        workspace_id: str,
        title: str,
        *,
        session_id: str | None = None,
        last_agent_version: int = 0,
    ) -> ChatSessionRecord:
        sid = session_id or new_id()
        await self._insert(
            "ChatSession",
            "INSERT INTO chat_sessions (id, workspace_id, title, created_at, last_agent_version)"
            " VALUES (?, ?, ?, ?, ?)",
            (sid, workspace_id, title, utc_now_iso(), last_agent_version),
        )
        return await self.get(sid)

    async def get_or_none(self, session_id: str) -> ChatSessionRecord | None:
        row = await self.db.fetch_one("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
        return None if row is None else _chat_session(row)

    async def get(self, session_id: str, *, workspace_id: str | None = None) -> ChatSessionRecord:
        record = await self.get_or_none(session_id)
        if record is None or (workspace_id is not None and record.workspace_id != workspace_id):
            raise not_found("ChatSession", session_id)
        return record

    async def list_by_workspace(self, workspace_id: str) -> list[ChatSessionRecord]:
        rows = await self.db.fetch_all(
            "SELECT * FROM chat_sessions WHERE workspace_id = ? ORDER BY created_at DESC, id DESC",
            (workspace_id,),
        )
        return [_chat_session(r) for r in rows]

    async def rename(self, session_id: str, title: str) -> ChatSessionRecord:
        count = await self.db.execute(
            "UPDATE chat_sessions SET title = ? WHERE id = ?", (title, session_id)
        )
        if count == 0:
            raise not_found("ChatSession", session_id)
        return await self.get(session_id)

    async def set_last_agent_version(self, session_id: str, version: int) -> None:
        count = await self.db.execute(
            "UPDATE chat_sessions SET last_agent_version = ? WHERE id = ?", (version, session_id)
        )
        if count == 0:
            raise not_found("ChatSession", session_id)

    async def delete(self, session_id: str) -> None:
        count = await self.db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        if count == 0:
            raise not_found("ChatSession", session_id)


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProposalRecord(_Record):
    id: str
    session_id: str
    summary: str
    status: ProposalStatus
    created_at: datetime
    #: ``changes`` (propose_changes) atau ``blueprint`` (propose_dashboard_plan).
    kind: str = "changes"
    payload: Any = None


def _proposal(row: Any) -> ProposalRecord:
    keys = row.keys()
    return ProposalRecord(
        id=row["id"],
        session_id=row["session_id"],
        summary=row["summary"],
        status=row["status"],
        created_at=_ts(row["created_at"]),
        kind=row["kind"] if "kind" in keys else "changes",
        payload=_loads(row["payload_json"]) if "payload_json" in keys else None,
    )


class ProposalRepo(_Repo):
    """Usulan perubahan (``propose_changes``) untuk approval gate (Req 21.2–21.4)."""

    async def create(
        self,
        session_id: str,
        summary: str,
        *,
        kind: str = "changes",
        payload: Any = None,
    ) -> ProposalRecord:
        pid = new_id()
        await self._insert(
            "Proposal",
            "INSERT INTO proposals (id, session_id, summary, status, created_at, kind, payload_json)"
            " VALUES (?, ?, ?, 'pending', ?, ?, ?)",
            (
                pid,
                session_id,
                summary,
                utc_now_iso(),
                kind,
                None if payload is None else dumps(payload),
            ),
        )
        return await self.get(pid)

    async def get_or_none(self, proposal_id: str) -> ProposalRecord | None:
        row = await self.db.fetch_one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        return None if row is None else _proposal(row)

    async def get(self, proposal_id: str) -> ProposalRecord:
        record = await self.get_or_none(proposal_id)
        if record is None:
            raise not_found("Proposal", proposal_id)
        return record

    async def list_by_session(
        self, session_id: str, *, status: ProposalStatus | None = None
    ) -> list[ProposalRecord]:
        if status is None:
            rows = await self.db.fetch_all(
                "SELECT * FROM proposals WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            )
        else:
            rows = await self.db.fetch_all(
                "SELECT * FROM proposals WHERE session_id = ? AND status = ?"
                " ORDER BY created_at, id",
                (session_id, status),
            )
        return [_proposal(r) for r in rows]

    async def approve(self, proposal_id: str, *, session_id: str | None = None) -> bool:
        """``pending → approved`` secara atomik; ``False`` bila tidak pending/tidak cocok sesi."""
        sql = "UPDATE proposals SET status = 'approved' WHERE id = ? AND status = 'pending'"
        params: list[Any] = [proposal_id]
        if session_id is not None:
            sql += " AND session_id = ?"
            params.append(session_id)
        return await self.db.execute(sql, params) == 1

    async def expire_pending(self, session_id: str, *, except_id: str | None = None) -> int:
        """Tandai semua proposal ``pending`` sesi menjadi ``expired``; kembalikan jumlahnya."""
        sql = "UPDATE proposals SET status = 'expired' WHERE session_id = ? AND status = 'pending'"
        params: list[Any] = [session_id]
        if except_id is not None:
            sql += " AND id != ?"
            params.append(except_id)
        return await self.db.execute(sql, params)


# ---------------------------------------------------------------------------
# Semantic_Model (Req 31, 32, 34)
# ---------------------------------------------------------------------------

SemanticKindT = Literal["column", "metric", "term", "instruction", "verified_query"]
SemanticStatusT = Literal["candidate", "confirmed", "rejected"]
SemanticSourceT = Literal["auto", "user"]


@dataclass(frozen=True, slots=True)
class SemanticEntryRecord(_Record):
    id: str
    workspace_id: str
    kind: SemanticKindT
    entry_key: str
    dataset_id: str | None
    status: SemanticStatusT
    source: SemanticSourceT
    body: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


def _semantic_entry(row: Any) -> SemanticEntryRecord:
    return SemanticEntryRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        kind=row["kind"],
        entry_key=row["entry_key"],
        dataset_id=row["dataset_id"],
        status=row["status"],
        source=row["source"],
        body=_loads(row["body_json"], {}),
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
        decided_at=_parse_ts(row["decided_at"]),
    )


class SemanticRepo(_Repo):
    """Semantic_Entry per Workspace; ``entry_key`` unik per Workspace.

    Entri ``rejected`` tetap disimpan agar kuncinya dikecualikan dari draft
    berikutnya (Req 31.8). Setiap perubahan menaikkan
    ``semantic_meta.semantic_version`` (cache konteks & validitas VQ).
    """

    async def _bump(self, workspace_id: str) -> None:
        await self.db.execute(
            "INSERT INTO semantic_meta (workspace_id, semantic_version) VALUES (?, 1)"
            " ON CONFLICT(workspace_id) DO UPDATE SET semantic_version = semantic_meta.semantic_version + 1",
            (workspace_id,),
        )

    async def get_or_none(self, entry_id: str) -> SemanticEntryRecord | None:
        row = await self.db.fetch_one("SELECT * FROM semantic_entries WHERE id = ?", (entry_id,))
        return None if row is None else _semantic_entry(row)

    async def get(self, entry_id: str, *, workspace_id: str | None = None) -> SemanticEntryRecord:
        record = await self.get_or_none(entry_id)
        if record is None or (workspace_id is not None and record.workspace_id != workspace_id):
            raise not_found("SemanticEntry", entry_id)
        return record

    async def get_by_key(self, workspace_id: str, entry_key: str) -> SemanticEntryRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM semantic_entries WHERE workspace_id = ? AND entry_key = ?",
            (workspace_id, entry_key),
        )
        return None if row is None else _semantic_entry(row)

    async def list(
        self,
        workspace_id: str,
        *,
        kind: SemanticKindT | None = None,
        status: SemanticStatusT | Collection[str] | None = None,
    ) -> list[SemanticEntryRecord]:
        sql = "SELECT * FROM semantic_entries WHERE workspace_id = ?"
        params: list[Any] = [workspace_id]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if status is not None:
            statuses = [status] if isinstance(status, str) else sorted(status)
            sql += f" AND status IN ({', '.join('?' for _ in statuses)})"
            params.extend(statuses)
        rows = await self.db.fetch_all(sql + " ORDER BY kind, entry_key", params)
        return [_semantic_entry(r) for r in rows]

    async def rejected_keys(self, workspace_id: str) -> set[str]:
        rows = await self.db.fetch_all(
            "SELECT entry_key FROM semantic_entries WHERE workspace_id = ? AND status = 'rejected'",
            (workspace_id,),
        )
        return {r["entry_key"] for r in rows}

    async def upsert(
        self,
        workspace_id: str,
        *,
        kind: SemanticKindT,
        entry_key: str,
        body: Mapping[str, Any],
        status: SemanticStatusT = "candidate",
        source: SemanticSourceT = "auto",
        dataset_id: str | None = None,
    ) -> SemanticEntryRecord:
        """Insert atau ganti entri dengan ``entry_key`` yang sama (tanpa cek kebijakan merge)."""
        now = utc_now_iso()
        decided = now if status != "candidate" else None
        await self._insert(
            "SemanticEntry",
            "INSERT INTO semantic_entries (id, workspace_id, kind, entry_key, dataset_id, status,"
            " source, body_json, created_at, updated_at, decided_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(workspace_id, entry_key) DO UPDATE SET kind = excluded.kind,"
            " dataset_id = excluded.dataset_id, status = excluded.status, source = excluded.source,"
            " body_json = excluded.body_json, updated_at = excluded.updated_at,"
            " decided_at = excluded.decided_at",
            (
                new_id(),
                workspace_id,
                kind,
                entry_key,
                dataset_id,
                status,
                source,
                dumps(dict(body)),
                now,
                now,
                decided,
            ),
        )
        await self._bump(workspace_id)
        record = await self.get_by_key(workspace_id, entry_key)
        assert record is not None
        return record

    async def set_status(self, entry_id: str, status: SemanticStatusT) -> SemanticEntryRecord:
        record = await self.get(entry_id)
        now = utc_now_iso()
        await self.db.execute(
            "UPDATE semantic_entries SET status = ?, updated_at = ?, decided_at = ? WHERE id = ?",
            (status, now, now if status != "candidate" else None, entry_id),
        )
        await self._bump(record.workspace_id)
        return await self.get(entry_id)

    async def update_body(self, entry_id: str, body: Mapping[str, Any]) -> SemanticEntryRecord:
        """Edit pengguna: body baru, ``source=user``, ``status=confirmed`` (Req 31.7)."""
        record = await self.get(entry_id)
        now = utc_now_iso()
        await self.db.execute(
            "UPDATE semantic_entries SET body_json = ?, source = 'user', status = 'confirmed',"
            " updated_at = ?, decided_at = ? WHERE id = ?",
            (dumps(dict(body)), now, now, entry_id),
        )
        await self._bump(record.workspace_id)
        return await self.get(entry_id)

    async def confirm_all_candidates(
        self, workspace_id: str, *, kinds: Collection[str] | None = None
    ) -> int:
        now = utc_now_iso()
        sql = (
            "UPDATE semantic_entries SET status = 'confirmed', updated_at = ?, decided_at = ?"
            " WHERE workspace_id = ? AND status = 'candidate'"
        )
        params: list[Any] = [now, now, workspace_id]
        if kinds is not None:
            ks = sorted(kinds)
            sql += f" AND kind IN ({', '.join('?' for _ in ks)})"
            params.extend(ks)
        count = await self.db.execute(sql, params)
        if count:
            await self._bump(workspace_id)
        return count

    async def delete(self, entry_id: str) -> None:
        record = await self.get(entry_id)
        await self.db.execute("DELETE FROM semantic_entries WHERE id = ?", (entry_id,))
        await self._bump(record.workspace_id)

    async def delete_unrejected(self, workspace_id: str) -> int:
        """Hapus semua entri selain ``rejected`` (dipakai impor YAML penuh)."""
        count = await self.db.execute(
            "DELETE FROM semantic_entries WHERE workspace_id = ? AND status != 'rejected'",
            (workspace_id,),
        )
        await self._bump(workspace_id)
        return count


@dataclass(frozen=True, slots=True)
class SemanticMetaRecord(_Record):
    workspace_id: str
    domain: str | None
    domain_confidence: float | None
    assumptions: list[str]
    semantic_version: int


class SemanticMetaRepo(_Repo):
    async def get(self, workspace_id: str) -> SemanticMetaRecord:
        row = await self.db.fetch_one(
            "SELECT * FROM semantic_meta WHERE workspace_id = ?", (workspace_id,)
        )
        if row is None:
            return SemanticMetaRecord(workspace_id, None, None, [], 0)
        return SemanticMetaRecord(
            workspace_id=row["workspace_id"],
            domain=row["domain"],
            domain_confidence=row["domain_confidence"],
            assumptions=_loads(row["assumptions_json"], []),
            semantic_version=row["semantic_version"],
        )

    async def set_domain(
        self,
        workspace_id: str,
        domain: str | None,
        confidence: float | None,
        assumptions: Sequence[str],
    ) -> SemanticMetaRecord:
        await self.db.execute(
            "INSERT INTO semantic_meta (workspace_id, domain, domain_confidence, assumptions_json,"
            " semantic_version) VALUES (?, ?, ?, ?, 1)"
            " ON CONFLICT(workspace_id) DO UPDATE SET domain = excluded.domain,"
            " domain_confidence = excluded.domain_confidence,"
            " assumptions_json = excluded.assumptions_json,"
            " semantic_version = semantic_meta.semantic_version + 1",
            (workspace_id, domain, confidence, dumps(list(assumptions))),
        )
        return await self.get(workspace_id)


DraftRunStatus = Literal["running", "done", "llm_failed"]


@dataclass(frozen=True, slots=True)
class DraftRunRecord(_Record):
    id: str
    workspace_id: str
    trigger_dataset_id: str | None
    status: DraftRunStatus
    discarded: list[dict[str, Any]]
    started_at: datetime
    finished_at: datetime | None


def _draft_run(row: Any) -> DraftRunRecord:
    return DraftRunRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        trigger_dataset_id=row["trigger_dataset_id"],
        status=row["status"],
        discarded=_loads(row["discarded_json"], []),
        started_at=_ts(row["started_at"]),
        finished_at=_parse_ts(row["finished_at"]),
    )


class DraftRunRepo(_Repo):
    async def create(self, workspace_id: str, trigger_dataset_id: str | None) -> DraftRunRecord:
        rid = new_id()
        await self._insert(
            "DraftRun",
            "INSERT INTO semantic_draft_runs (id, workspace_id, trigger_dataset_id, status,"
            " started_at) VALUES (?, ?, ?, 'running', ?)",
            (rid, workspace_id, trigger_dataset_id, utc_now_iso()),
        )
        return await self.get(rid)

    async def get(self, run_id: str) -> DraftRunRecord:
        row = await self.db.fetch_one("SELECT * FROM semantic_draft_runs WHERE id = ?", (run_id,))
        if row is None:
            raise not_found("DraftRun", run_id)
        return _draft_run(row)

    async def finish(
        self, run_id: str, status: DraftRunStatus, discarded: Sequence[Mapping[str, Any]] = ()
    ) -> DraftRunRecord:
        await self.db.execute(
            "UPDATE semantic_draft_runs SET status = ?, discarded_json = ?, finished_at = ?"
            " WHERE id = ?",
            (status, dumps([dict(d) for d in discarded]), utc_now_iso(), run_id),
        )
        return await self.get(run_id)

    async def latest(self, workspace_id: str) -> DraftRunRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM semantic_draft_runs WHERE workspace_id = ?"
            " ORDER BY started_at DESC, id DESC LIMIT 1",
            (workspace_id,),
        )
        return None if row is None else _draft_run(row)


BlueprintStatus = Literal["active", "completed", "stopped"]


@dataclass(frozen=True, slots=True)
class BlueprintRecord(_Record):
    id: str
    dashboard_id: str
    proposal_id: str | None
    blueprint: dict[str, Any]
    slot_status: dict[str, dict[str, Any]]
    status: BlueprintStatus
    created_at: datetime
    finished_at: datetime | None


def _blueprint(row: Any) -> BlueprintRecord:
    return BlueprintRecord(
        id=row["id"],
        dashboard_id=row["dashboard_id"],
        proposal_id=row["proposal_id"],
        blueprint=_loads(row["blueprint_json"], {}),
        slot_status=_loads(row["slot_status_json"], {}),
        status=row["status"],
        created_at=_ts(row["created_at"]),
        finished_at=_parse_ts(row["finished_at"]),
    )


class BlueprintRepo(_Repo):
    """Dashboard_Blueprint aktif/terakhir per Dashboard (Req 37.6, 37.8)."""

    async def create(
        self,
        dashboard_id: str,
        blueprint: Mapping[str, Any],
        *,
        proposal_id: str | None = None,
    ) -> BlueprintRecord:
        # Hanya satu Blueprint aktif per Dashboard: yang lama dihentikan.
        await self.db.execute(
            "UPDATE dashboard_blueprints SET status = 'stopped', finished_at = ?"
            " WHERE dashboard_id = ? AND status = 'active'",
            (utc_now_iso(), dashboard_id),
        )
        bid = new_id()
        slots = blueprint.get("slots", [])
        slot_status = {s["slot_id"]: {"status": "pending"} for s in slots}
        await self._insert(
            "Blueprint",
            "INSERT INTO dashboard_blueprints (id, dashboard_id, proposal_id, blueprint_json,"
            " slot_status_json, status, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (bid, dashboard_id, proposal_id, dumps(dict(blueprint)), dumps(slot_status), utc_now_iso()),
        )
        return await self.get(bid)

    async def get_or_none(self, blueprint_id: str) -> BlueprintRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM dashboard_blueprints WHERE id = ?", (blueprint_id,)
        )
        return None if row is None else _blueprint(row)

    async def get(self, blueprint_id: str) -> BlueprintRecord:
        record = await self.get_or_none(blueprint_id)
        if record is None:
            raise not_found("Blueprint", blueprint_id)
        return record

    async def latest(self, dashboard_id: str) -> BlueprintRecord | None:
        row = await self.db.fetch_one(
            "SELECT * FROM dashboard_blueprints WHERE dashboard_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (dashboard_id,),
        )
        return None if row is None else _blueprint(row)

    async def update_slot(
        self,
        blueprint_id: str,
        slot_id: str,
        status: str,
        *,
        item_id: str | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> BlueprintRecord:
        async with self.db.transaction():
            record = await self.get(blueprint_id)
            if slot_id not in record.slot_status:
                raise not_found("BlueprintSlot", slot_id)
            entry: dict[str, Any] = {"status": status}
            if item_id is not None:
                entry["item_id"] = item_id
            if error is not None:
                entry["error"] = dict(error)
            slot_status = {**record.slot_status, slot_id: entry}
            await self.db.execute(
                "UPDATE dashboard_blueprints SET slot_status_json = ? WHERE id = ?",
                (dumps(slot_status), blueprint_id),
            )
        return await self.get(blueprint_id)

    async def finish(self, blueprint_id: str, status: BlueprintStatus) -> BlueprintRecord:
        await self.db.execute(
            "UPDATE dashboard_blueprints SET status = ?, finished_at = ? WHERE id = ?",
            (status, utc_now_iso(), blueprint_id),
        )
        return await self.get(blueprint_id)


# ---------------------------------------------------------------------------
# Agregat
# ---------------------------------------------------------------------------


class Repositories:
    """Kumpulan semua repositori di atas satu :class:`Database`."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.workspaces = WorkspaceRepo(db)
        self.uploads = UploadRepo(db)
        self.datasets = DatasetRepo(db)
        self.profiles = DatasetProfileRepo(db)
        self.relations = RelationRepo(db)
        self.queries = QueryRepo(db)
        self.dashboards = DashboardRepo(db)
        self.patches = PatchEventRepo(db)
        self.chat_sessions = ChatSessionRepo(db)
        self.proposals = ProposalRepo(db)
        self.semantic = SemanticRepo(db)
        self.semantic_meta = SemanticMetaRepo(db)
        self.draft_runs = DraftRunRepo(db)
        self.blueprints = BlueprintRepo(db)


__all__ = [
    "LOCAL_OWNER_ID",
    "MAX_SNAPSHOT_ROWS",
    "new_id",
    "utc_now",
    "utc_now_iso",
    "json_cell",
    "dumps",
    "not_found",
    "WorkspaceRecord",
    "WorkspaceRepo",
    "UploadRecord",
    "UploadRepo",
    "ColumnMapping",
    "DatasetRecord",
    "DatasetRepo",
    "DatasetProfileRecord",
    "DatasetProfileRepo",
    "RelationRecord",
    "RelationRepo",
    "QueryRecord",
    "QueryRepo",
    "DashboardRecord",
    "DashboardRepo",
    "PatchEventRecord",
    "PatchEventRepo",
    "ChatSessionRecord",
    "ChatSessionRepo",
    "ProposalRecord",
    "ProposalRepo",
    "SemanticEntryRecord",
    "SemanticRepo",
    "SemanticMetaRecord",
    "SemanticMetaRepo",
    "DraftRunRecord",
    "DraftRunRepo",
    "BlueprintRecord",
    "BlueprintRepo",
    "Repositories",
]

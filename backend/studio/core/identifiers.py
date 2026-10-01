"""Identifier murni untuk ingestion: nama kolom, nama tabel, nama file upload.

Semua fungsi di modul ini deterministik (kecuali prefiks ULID pada
``sanitize_filename``) dan tidak melakukan I/O selain ``safe_join`` yang hanya
me-resolve path. Target property test: Properti 1–4 (Req 2.4, 2.5, 10.1, 29.5).
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TypedDict

from ulid import ULID

from studio.api.errors import StudioError

__all__ = [
    "SQL_KEYWORDS",
    "SUPPORTED_EXTENSIONS",
    "ColumnMappingEntry",
    "UnsafePath",
    "UnsupportedFormat",
    "check_extension",
    "make_table_name",
    "normalize_columns",
    "safe_join",
    "sanitize_filename",
]

SUPPORTED_EXTENSIONS: tuple[str, ...] = (".csv", ".xlsx")


# ---------------------------------------------------------------------------
# Error domain
# ---------------------------------------------------------------------------


class UnsupportedFormat(StudioError):
    """Ekstensi file upload bukan ``.csv`` / ``.xlsx`` (Req 2.4)."""

    def __init__(self, filename: str, extension: str) -> None:
        supported = ", ".join(SUPPORTED_EXTENSIONS)
        shown = extension or "(tanpa ekstensi)"
        super().__init__(
            code="UNSUPPORTED_FORMAT",
            message=f"Format file {shown} tidak didukung. Format yang didukung: {supported}.",
            details={
                "filename": filename,
                "extension": extension,
                "supported": list(SUPPORTED_EXTENSIONS),
            },
            http_status=415,
        )


class UnsafePath(StudioError):
    """Path hasil gabungan keluar dari folder workspace (Req 29.5)."""

    def __init__(self, name: str) -> None:
        super().__init__(
            code="VALIDATION_ERROR",
            message="Nama file menghasilkan path di luar folder workspace.",
            details={"filename": name},
            http_status=422,
        )


# ---------------------------------------------------------------------------
# Nama kolom (Req 2.5, Property 1)
# ---------------------------------------------------------------------------


class ColumnMappingEntry(TypedDict):
    """Satu entri ``column_mapping_json``: ``{original, normalized}``."""

    original: str
    normalized: str


def normalize_columns(names: Sequence[str]) -> tuple[list[str], list[ColumnMappingEntry]]:
    """Normalisasi header menjadi unik dan tidak kosong.

    Aturan:
    - Setiap nama di-trim (whitespace unicode).
    - Nama kosong setelah trim diganti ``column_{i}`` (``i`` 1-based posisi kolom).
    - Kemunculan pertama suatu nama mempertahankan namanya; kemunculan berikutnya
      diberi sufiks ``_2``, ``_3``, ... yang tidak bertabrakan dengan nama lain.

    Nama yang sudah unik dan tidak kosong (setelah trim) tidak berubah, dan
    fungsi ini idempoten. Mapping sejajar per posisi dengan input.
    """
    originals = ["" if n is None else str(n) for n in names]
    trimmed = [n.strip() for n in originals]

    result: list[str | None] = [None] * len(trimmed)
    taken: set[str] = set()

    # Pass 1: kemunculan pertama setiap nama non-kosong dipertahankan, sehingga
    # nama yang sudah unik tidak pernah tergeser oleh nama hasil generate.
    for i, name in enumerate(trimmed):
        if name and name not in taken:
            result[i] = name
            taken.add(name)

    # Pass 2: duplikat dan nama kosong diberi nama baru yang belum terpakai.
    for i, name in enumerate(trimmed):
        if result[i] is not None:
            continue
        if name:
            base, k = name, 2
            candidate = f"{base}_{k}"
        else:
            base, k = f"column_{i + 1}", 1
            candidate = base
        while candidate in taken:
            k += 1
            candidate = f"{base}_{k}"
        result[i] = candidate
        taken.add(candidate)

    normalized = [r for r in result if r is not None]
    mapping: list[ColumnMappingEntry] = [
        {"original": o, "normalized": n} for o, n in zip(originals, normalized, strict=True)
    ]
    return normalized, mapping


# ---------------------------------------------------------------------------
# Nama tabel (Req 10.1, Property 4)
# ---------------------------------------------------------------------------

_BASE_SQL_KEYWORDS = frozenset(
    """
    abort action add all alter analyze and anti any array as asc asof at attach
    authorization autoincrement before begin between bigint binary bit blob boolean
    both by call cascade case cast char character check collate column commit
    constraint create cross cube current current_date current_time
    current_timestamp current_user cursor database date datetime day deallocate
    dec decimal declare default deferrable deferred delete desc describe detach
    distinct do double drop each else end escape except exclude exclusive exec
    execute exists explain false fetch filter first float following for foreign
    from full function glob grant group grouping having hour if ignore ilike
    immediate in index inner insert instead int integer intersect interval into
    is isnull join key last lateral leading left like limit local localtime
    localtimestamp match materialized merge minute month natural no not nothing
    notnull null nulls numeric of offset on only or order others out outer over
    overlaps partition percent pivot placing plan pragma preceding precision
    primary procedure qualify range real recursive references regexp reindex
    release rename replace restrict return returning returns revoke right
    rollback rollup row rows savepoint schema second select semi session_user
    set show similar smallint some start struct table temp temporary then ties
    time timestamp to trailing transaction trigger true truncate union unique
    unknown unnest unpivot update usage use user using vacuum values varchar
    varying view virtual when where window with within without year
    """.split()
)


def _load_sql_keywords() -> frozenset[str]:
    keywords = set(_BASE_SQL_KEYWORDS)
    try:  # sqlglot dipakai SQL_Validator; nama tabel tidak boleh menjadi keyword-nya.
        from sqlglot.tokens import Tokenizer

        keywords.update(
            k.lower() for k in Tokenizer.KEYWORDS if re.fullmatch(r"[A-Za-z_]+", k)
        )
    except Exception:  # pragma: no cover - sqlglot adalah dependensi wajib
        pass
    return frozenset(keywords)


SQL_KEYWORDS: frozenset[str] = _load_sql_keywords()

_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_NON_IDENT_CHARS = re.compile(r"[^a-z0-9_]+")
_MULTI_UNDERSCORE = re.compile(r"_+")
_MAX_TABLE_NAME_LEN = 48


def _is_valid_table_name(name: str) -> bool:
    return bool(_IDENT_RE.match(name)) and name not in SQL_KEYWORDS


def make_table_name(source_name: str, existing: Iterable[str]) -> str:
    """Buat identifier SQL valid dan unik dari nama file/sheet.

    Hasil memenuhi ``^[a-z_][a-z0-9_]*$``, bukan keyword SQL, dan tidak
    bertabrakan (case-insensitive) dengan ``existing``.
    """
    taken = {e.lower() for e in existing}

    stem = str(source_name or "").strip()
    root, ext = os.path.splitext(stem)
    if ext.lower() in SUPPORTED_EXTENSIONS:
        stem = root

    ascii_stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode("ascii")
    base = _NON_IDENT_CHARS.sub("_", ascii_stem.lower())
    base = _MULTI_UNDERSCORE.sub("_", base).strip("_")
    base = base[:_MAX_TABLE_NAME_LEN].rstrip("_")

    if not base:
        base = "table_data"
    elif base[0].isdigit():
        base = f"t_{base}"
    if base in SQL_KEYWORDS:
        base = f"{base}_data"

    candidate, k = base, 1
    while candidate in taken or not _is_valid_table_name(candidate):
        k += 1
        candidate = f"{base}_{k}"
    return candidate


# ---------------------------------------------------------------------------
# Nama file upload (Req 29.5, Property 3)
# ---------------------------------------------------------------------------

_FORBIDDEN_FILENAME_CHARS = re.compile(r'[<>:"|?*]')
_MAX_FILENAME_LEN = 150


def _strip_control(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))


def sanitize_filename(name: str) -> str:
    """Normalisasi nama file upload menjadi satu komponen path yang aman.

    Ambil basename setelah memisah ``/`` dan ``\\``, hapus ``..``, karakter
    kontrol, dan ``<>:"|?*``; hasil kosong → ``upload``. Hasil diberi prefiks
    ULID agar unik: ``{ULID}_{nama}``.
    """
    base = re.split(r"[/\\]", str(name or ""))[-1]
    base = _strip_control(base)
    base = _FORBIDDEN_FILENAME_CHARS.sub("", base)
    while ".." in base:
        base = base.replace("..", "")
    # Windows mengabaikan titik/spasi di akhir nama; buang agar nama konsisten.
    base = base.strip().rstrip(". ").strip()

    if len(base) > _MAX_FILENAME_LEN:
        root, ext = os.path.splitext(base)
        ext = ext if len(ext) <= 16 else ""
        base = root[: _MAX_FILENAME_LEN - len(ext)] + ext

    if not base or base == ".":
        base = "upload"
    return f"{ULID()}_{base}"


def safe_join(workspace_dir: str | os.PathLike[str], name: str) -> Path:
    """Gabungkan ``workspace_dir`` dengan ``name`` dan pastikan tetap di dalamnya.

    ``name`` seharusnya hasil ``sanitize_filename``; path final diverifikasi
    ``resolve().is_relative_to(workspace_dir)`` dan tidak boleh sama dengan
    folder itu sendiri. Pelanggaran → ``UnsafePath``.
    """
    base = Path(workspace_dir).resolve()
    target = (base / name).resolve()
    if target == base or not target.is_relative_to(base):
        raise UnsafePath(name)
    return target


# ---------------------------------------------------------------------------
# Gerbang format (Req 2.4, Property 2)
# ---------------------------------------------------------------------------


def check_extension(name: str) -> str:
    """Validasi ekstensi upload; kembalikan ekstensi lowercase (``.csv``/``.xlsx``).

    Selain itu raise ``UnsupportedFormat`` yang menyebut format yang didukung.
    """
    filename = str(name or "")
    lowered = filename.lower()
    for ext in SUPPORTED_EXTENSIONS:
        if lowered.endswith(ext):
            return ext
    raise UnsupportedFormat(filename, os.path.splitext(filename)[1].lower())

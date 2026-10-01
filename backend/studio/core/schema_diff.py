"""Perbandingan skema untuk re-upload Dataset (Req 26.1, 26.3, 26.4).

Fungsi murni tanpa I/O. ``diff_schemas`` membandingkan skema tersimpan
(``old``) dengan skema file baru (``new``) berdasarkan nama kolom dan
kompatibilitas tipe logis. Hasilnya kosong jika dan hanya jika himpunan nama
kolom sama dan setiap kolom bertipe kompatibel.

Aturan kompatibilitas (``types_compatible``):

- Tipe identik selalu kompatibel.
- ``integer`` dan ``float`` saling kompatibel (keduanya numerik; inferensi
  ulang dapat melebarkan integer→float tanpa merusak chart/query yang ada).
- Kombinasi lain tidak kompatibel (mis. ``date``↔``datetime``,
  ``integer``↔``string``, ``boolean``↔``integer``).

Bentuk ``SchemaDiff.as_details()`` adalah payload ``details`` untuk error
``SCHEMA_MISMATCH``: ``{missing: [str], added: [str],
changed: [{name, old_type, new_type}]}``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from studio.core.models import ColumnInfo, LogicalType

#: Kelompok tipe yang saling kompatibel satu sama lain.
_NUMERIC: frozenset[str] = frozenset({"integer", "float"})


def types_compatible(old: LogicalType, new: LogicalType) -> bool:
    """True bila kolom bertipe ``old`` boleh diganti data bertipe ``new``.

    Relasi ini simetris: identik, atau keduanya numerik (integer/float).
    """
    if old == new:
        return True
    return old in _NUMERIC and new in _NUMERIC


@dataclass(frozen=True, slots=True)
class TypeChange:
    """Kolom yang ada di kedua skema tetapi tipenya tidak kompatibel."""

    name: str
    old_type: LogicalType
    new_type: LogicalType

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "old_type": self.old_type, "new_type": self.new_type}


@dataclass(frozen=True, slots=True)
class SchemaDiff:
    """Selisih skema lama vs baru.

    - ``missing``: kolom di skema lama yang tidak ada di skema baru (urutan lama).
    - ``added``: kolom di skema baru yang tidak ada di skema lama (urutan baru).
    - ``changed``: kolom bersama dengan tipe tidak kompatibel (urutan lama).
    """

    missing: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    changed: tuple[TypeChange, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.missing or self.added or self.changed)

    def as_details(self) -> dict[str, Any]:
        """Payload ``details`` untuk error ``SCHEMA_MISMATCH``."""
        return {
            "missing": list(self.missing),
            "added": list(self.added),
            "changed": [c.as_dict() for c in self.changed],
        }


def _by_name(schema: Iterable[ColumnInfo]) -> dict[str, LogicalType]:
    """Peta nama → tipe dengan urutan kemunculan; nama duplikat memakai yang pertama."""
    result: dict[str, LogicalType] = {}
    for col in schema:
        result.setdefault(col.name, col.type)
    return result


def diff_schemas(old: Iterable[ColumnInfo], new: Iterable[ColumnInfo]) -> SchemaDiff:
    """Bandingkan skema tersimpan ``old`` dengan skema file baru ``new``."""
    old_map = _by_name(old)
    new_map = _by_name(new)

    missing = tuple(name for name in old_map if name not in new_map)
    added = tuple(name for name in new_map if name not in old_map)
    changed = tuple(
        TypeChange(name=name, old_type=old_type, new_type=new_map[name])
        for name, old_type in old_map.items()
        if name in new_map and not types_compatible(old_type, new_map[name])
    )
    return SchemaDiff(missing=missing, added=added, changed=changed)


__all__ = ["types_compatible", "TypeChange", "SchemaDiff", "diff_schemas"]

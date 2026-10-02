"use client";

import { useEffect, useState } from "react";
import {
  dateRange,
  describeFilters,
  formatScalar,
  inValues,
  removePredicate,
  setColumnFilter,
} from "@/lib/filters";
import type {
  Dataset,
  DatasetDetail,
  DashboardSnapshot,
  FilterSet,
  Predicate,
  Scalar,
} from "@/lib/types";
import { defaultFilterClient, errorMessage, isTimeColumn, type FilterClient } from "./client";
import styles from "./filters.module.css";

export interface GlobalFilterPanelProps {
  workspaceId: string;
  /** Snapshot Dashboard: versi dasar + Global_Filter aktif. */
  snapshot: DashboardSnapshot;
  /** Daftar Dataset Workspace (sumber kolom filter). */
  datasets: readonly Dataset[];
  /** Cross_Filter aktif (state tampilan, tidak disimpan). */
  crossFilters: Predicate[];
  /** Ubah Cross_Filter (toggle/hapus dari canvas/panel ini). */
  onCrossFiltersChange: (filters: FilterSet) => void;
  client?: FilterClient;
}

/** Kolom kategorikal dengan nilai unik lebih banyak dari ini dianggap bukan dimensi filter. */
const MAX_DISTINCT = 500;

/** `kode_provinsi` → `Kode provinsi`. */
export function columnLabel(name: string): string {
  const text = name.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Filter per kolom yang berlaku ke semua chart: dropdown multi-pilih untuk
 * dimensi, rentang tanggal untuk kolom waktu. Perubahan langsung dikirim
 * sebagai `set_global_filters` (tanpa tombol terapkan). Chip menampilkan
 * filter aktif dan Cross_Filter (Req 22.1, 22.3, 24.3, 24.4).
 */
export function GlobalFilterPanel({
  workspaceId,
  snapshot,
  datasets,
  crossFilters,
  onCrossFiltersChange,
  client = defaultFilterClient,
}: GlobalFilterPanelProps) {
  const globalFilters = snapshot.content.global_filters;
  const [details, setDetails] = useState<Record<string, DatasetDetail>>({});
  const [error, setError] = useState<string | null>(null);

  // Profil kolom per dataset: menentukan kolom mana yang layak jadi filter.
  const datasetKey = datasets.map((d) => `${d.id}:${d.data_version}`).join("|");
  useEffect(() => {
    let cancelled = false;
    for (const d of datasets) {
      client
        .getDataset(workspaceId, d.id)
        .then((res) => {
          if (!cancelled) setDetails((prev) => ({ ...prev, [d.id]: res }));
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(errorMessage(err));
        });
    }
    return () => {
      cancelled = true;
    };
    // `datasets` dipantau lewat `datasetKey` (identitas array tidak stabil).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, workspaceId, datasetKey]);

  function applyGlobal(next: FilterSet) {
    setError(null);
    client
      .command(snapshot.id, snapshot.version, { type: "set_global_filters", filters: next })
      .catch((err: unknown) => setError(errorMessage(err)));
  }

  const globalLabels = describeFilters(globalFilters);
  const crossLabels = describeFilters(crossFilters);

  const controls = datasets.flatMap((dataset) => {
    const detail = details[dataset.id];
    const profiles = new Map((detail?.column_profiles ?? []).map((p) => [p.name, p]));
    return dataset.schema.flatMap((col) => {
      if (isTimeColumn(col)) return [{ dataset, name: col.name, time: true }];
      const p = profiles.get(col.name);
      if (!p || p.role !== "dimension" || p.distinct_count > MAX_DISTINCT) return [];
      return [{ dataset, name: col.name, time: false, distinct: p.distinct_count }];
    });
  });

  return (
    <section aria-label="Filter" className={styles.panel}>
      <h2 className={styles.title}>Saring data</h2>
      <p className={styles.muted}>Berlaku ke semua chart di dashboard.</p>

      {datasets.length === 0 ? (
        <p className={styles.muted}>Unggah dataset untuk mulai menyaring data.</p>
      ) : controls.length === 0 ? (
        <p className={styles.muted}>Memuat kolom…</p>
      ) : (
        <div className={styles.controls}>
          {controls.map((c) =>
            c.time ? (
              <DateRangeControl
                key={`${c.dataset.id}.${c.name}`}
                table={c.dataset.table_name}
                column={c.name}
                filters={globalFilters}
                onChange={applyGlobal}
              />
            ) : (
              <ValuesControl
                key={`${c.dataset.id}.${c.name}`}
                workspaceId={workspaceId}
                dataset={c.dataset}
                column={c.name}
                filters={globalFilters}
                client={client}
                onChange={applyGlobal}
                onError={setError}
              />
            ),
          )}
        </div>
      )}

      {(globalLabels.length > 0 || crossLabels.length > 0) && (
        <ul className={styles.chips}>
          {globalFilters.map((pred, i) => (
            <li key={`g-${pred.table}.${pred.column}-${i}`} className={styles.chip}>
              <span>{globalLabels[i]}</span>
              <button
                type="button"
                className={styles.chipRemove}
                aria-label={`Hapus filter ${globalLabels[i]}`}
                onClick={() => applyGlobal(removePredicate(globalFilters, pred))}
              >
                ✕
              </button>
            </li>
          ))}
          {crossFilters.map((pred, i) => (
            <li key={`c-${pred.table}.${pred.column}-${i}`} className={`${styles.chip} ${styles.chipCross}`}>
              <span>Dipilih di chart · {crossLabels[i]}</span>
              <button
                type="button"
                className={styles.chipRemove}
                aria-label={`Hapus Cross_Filter ${crossLabels[i]}`}
                onClick={() => onCrossFiltersChange(removePredicate(crossFilters, pred))}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </section>
  );
}

function ValuesControl({
  workspaceId,
  dataset,
  column,
  filters,
  client,
  onChange,
  onError,
}: {
  workspaceId: string;
  dataset: Dataset;
  column: string;
  filters: FilterSet;
  client: FilterClient;
  onChange: (next: FilterSet) => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<{ values: Scalar[]; truncated: boolean } | null>(null);

  const table = dataset.table_name;
  const current = filters.find((p) => p.table === table && p.column === column && p.kind === "in");
  const selected: Scalar[] = current?.kind === "in" ? current.values : [];

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // Tunda sebentar saat mengetik agar tidak memanggil API tiap tombol.
    const timer = setTimeout(() => {
      client
        .columnValues(workspaceId, dataset.id, column, q)
        .then((res) => {
          if (!cancelled) setOptions(res);
        })
        .catch((err: unknown) => {
          if (!cancelled) onError(errorMessage(err));
        });
    }, q ? 200 : 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, q, client, workspaceId, dataset.id, column, onError]);

  function toggle(value: Scalar) {
    const has = selected.some((v) => v === value);
    const next = has ? selected.filter((v) => v !== value) : [...selected, value];
    onChange(setColumnFilter(filters, table, column, next.length ? inValues(table, column, next) : null));
  }

  const label = columnLabel(column);
  const summary =
    selected.length === 0
      ? "Semua"
      : selected.length === 1
        ? formatScalar(selected[0])
        : `${selected.length} dipilih`;

  return (
    <details className={styles.control} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className={styles.controlSummary}>
        <span className={styles.controlName}>{label}</span>
        <span className={selected.length ? styles.controlActive : styles.controlValue}>{summary}</span>
      </summary>
      <div className={styles.dropdown}>
        <input
          type="search"
          aria-label={`Cari ${label}`}
          placeholder={`Cari ${label.toLowerCase()}…`}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div className={styles.values} role="group" aria-label={label}>
          {options === null ? (
            <span className={styles.muted}>Memuat nilai…</span>
          ) : options.values.length === 0 ? (
            <span className={styles.muted}>Tidak ada yang cocok.</span>
          ) : (
            options.values.map((v) => {
              const text = formatScalar(v);
              return (
                <label key={text} className={styles.value}>
                  <input
                    type="checkbox"
                    checked={selected.some((s) => s === v)}
                    onChange={() => toggle(v)}
                  />
                  {text}
                </label>
              );
            })
          )}
          {options?.truncated && (
            <span className={styles.muted}>Ketik untuk mempersempit daftar.</span>
          )}
        </div>
      </div>
    </details>
  );
}

function DateRangeControl({
  table,
  column,
  filters,
  onChange,
}: {
  table: string;
  column: string;
  filters: FilterSet;
  onChange: (next: FilterSet) => void;
}) {
  const current = filters.find((p) => p.table === table && p.column === column && p.kind === "date_range");
  const start = current?.kind === "date_range" ? (current.start ?? "") : "";
  const end = current?.kind === "date_range" ? (current.end ?? "") : "";
  const label = columnLabel(column);

  function set(nextStart: string, nextEnd: string) {
    onChange(setColumnFilter(filters, table, column, dateRange(table, column, nextStart || null, nextEnd || null)));
  }

  return (
    <fieldset className={styles.control}>
      <legend className={styles.controlName}>{label}</legend>
      <div className={styles.row}>
        <label>
          Dari
          <input
            type="date"
            aria-label={`${label} dari`}
            value={start}
            onChange={(e) => set(e.target.value, end)}
          />
        </label>
        <label>
          Sampai
          <input
            type="date"
            aria-label={`${label} sampai`}
            value={end}
            onChange={(e) => set(start, e.target.value)}
          />
        </label>
      </div>
    </fieldset>
  );
}

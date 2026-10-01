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
import type { Dataset, DashboardSnapshot, FilterSet, Predicate } from "@/lib/types";
import {
  defaultFilterClient,
  errorMessage,
  isTimeColumn,
  topValues,
  type FilterClient,
} from "./client";
import styles from "./filters.module.css";

export interface GlobalFilterPanelProps {
  workspaceId: string;
  /** Snapshot Dashboard: versi dasar + Global_Filter aktif. */
  snapshot: DashboardSnapshot;
  /** Daftar Dataset Workspace (pilihan kolom filter). */
  datasets: readonly Dataset[];
  /** Cross_Filter aktif (state tampilan, tidak disimpan). */
  crossFilters: Predicate[];
  /** Ubah Cross_Filter (toggle/hapus dari canvas/panel ini). */
  onCrossFiltersChange: (filters: FilterSet) => void;
  client?: FilterClient;
}

/**
 * Global_Filter: rentang tanggal untuk kolom waktu dan pilihan nilai untuk
 * kolom kategorikal (command `set_global_filters`), chip filter aktif dengan
 * tombol hapus, serta chip Cross_Filter aktif (Req 22.1, 22.3, 24.3, 24.4).
 * Setiap perubahan memicu render ulang via SSE `patch.applied` (canvas).
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
  const version = snapshot.version;

  const [datasetId, setDatasetId] = useState("");
  const [column, setColumn] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [loaded, setLoaded] = useState<{
    datasetId: string;
    detail: Awaited<ReturnType<FilterClient["getDataset"]>>;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Detail milik dataset lain (pilihan sudah berganti) dianggap belum dimuat.
  const detail = loaded && loaded.datasetId === datasetId ? loaded.detail : null;

  const dataset = datasets.find((d) => d.id === datasetId) ?? null;
  const columnInfo = dataset?.schema.find((c) => c.name === column) ?? null;
  const isTime = columnInfo !== null && isTimeColumn(columnInfo);

  // Muat detail dataset (profil kolom) saat dataset dipilih → nilai kategorikal.
  useEffect(() => {
    let cancelled = false;
    if (!datasetId) return;
    client
      .getDataset(workspaceId, datasetId)
      .then((res) => {
        if (!cancelled) setLoaded({ datasetId, detail: res });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, datasetId]);

  function applyGlobal(next: FilterSet) {
    setError(null);
    client
      .command(snapshot.id, version, { type: "set_global_filters", filters: next })
      .catch((err: unknown) => setError(errorMessage(err)));
  }

  function onApply() {
    if (!dataset || !columnInfo) return;
    const pred = isTime
      ? dateRange(dataset.table_name, column, start || null, end || null)
      : inValues(
          dataset.table_name,
          column,
          chosen.map((v) => {
            const parsed = detail ? topValues(detail, column).find((x) => formatScalar(x) === v) : undefined;
            return parsed ?? v;
          }),
        );
    if (isTime && !start && !end) return; // rentang kosong = no-op
    if (!isTime && chosen.length === 0) return;
    applyGlobal(setColumnFilter(globalFilters, dataset.table_name, column, pred));
    setChosen([]);
  }

  const globalLabels = describeFilters(globalFilters);
  const crossLabels = describeFilters(crossFilters);

  return (
    <section aria-label="Filter" className={styles.panel}>
      <h2 className={styles.title}>Filter</h2>

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
              <span>Cross_Filter · {crossLabels[i]}</span>
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

      {datasets.length > 0 ? (
        <div className={styles.adder}>
          <select
            aria-label="Dataset filter"
            value={datasetId}
            onChange={(e) => {
              setDatasetId(e.target.value);
              setColumn("");
              setChosen([]);
              setStart("");
              setEnd("");
            }}
          >
            <option value="">Pilih dataset…</option>
            {datasets.map((d) => (
              <option key={d.id} value={d.id}>
                {d.source_name}
              </option>
            ))}
          </select>

          {dataset && (
            <select
              aria-label="Kolom filter"
              value={column}
              onChange={(e) => {
                setColumn(e.target.value);
                setChosen([]);
              }}
            >
              <option value="">Pilih kolom…</option>
              {dataset.schema.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} ({isTimeColumn(c) ? "waktu" : "kategorikal"})
                </option>
              ))}
            </select>
          )}

          {isTime && columnInfo && (
            <div className={styles.row}>
              <label>
                Dari
                <input
                  type="date"
                  aria-label="Tanggal mulai"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <label>
                Sampai
                <input
                  type="date"
                  aria-label="Tanggal akhir"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                />
              </label>
            </div>
          )}

          {!isTime && column && (
            <fieldset className={styles.values}>
              <legend>Nilai ({column})</legend>
              {detail ? (
                topValues(detail, column).length > 0 ? (
                  topValues(detail, column).map((v) => {
                    const label = formatScalar(v);
                    return (
                      <label key={label} className={styles.value}>
                        <input
                          type="checkbox"
                          checked={chosen.includes(label)}
                          onChange={(e) =>
                            setChosen((prev) =>
                              e.target.checked
                                ? [...prev, label]
                                : prev.filter((x) => x !== label),
                            )
                          }
                        />
                        {label}
                      </label>
                    );
                  })
                ) : (
                  <span className={styles.muted}>Tidak ada nilai teratas untuk kolom ini.</span>
                )
              ) : (
                <span className={styles.muted}>Memuat nilai…</span>
              )}
            </fieldset>
          )}

          <button
            type="button"
            className={styles.button}
            disabled={!datasetId || !column || (isTime ? !start && !end : chosen.length === 0)}
            onClick={onApply}
          >
            Terapkan filter
          </button>
        </div>
      ) : (
        <p className={styles.muted}>Unggah dataset untuk mulai memfilter.</p>
      )}

      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </section>
  );
}

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
import { CaretDownIcon, InfoIcon, XIcon } from "@phosphor-icons/react/ssr";
import { IconAction } from "@/components/IconAction";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

const MUTED = "text-xs text-muted-foreground";
/** Trigger berbentuk chip: `Provinsi: Semua ▾`. */
const CONTROL = "relative m-0 min-w-0 rounded-full border bg-surface-2 text-xs transition-colors hover:border-border-strong";
/** Kontrol yang sedang menyaring: latar aksen lembut agar terlihat sekilas. */
const CONTROL_ACTIVE = "border-transparent bg-accent-soft text-primary";
const CONTROL_NAME = "p-0 text-xs text-muted-foreground";
const CHIP = "inline-flex max-w-full items-center gap-1 rounded-full bg-accent-soft py-0.5 pr-0.5 pl-2.5 text-xs text-primary";
const CHECK = "size-4 accent-foreground";

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
    <section aria-label="Filter" className="flex min-h-11 flex-wrap items-center gap-2 py-1.5">
      <h2 className="section-label flex items-center">
        Filter
        <IconAction label="Tentang filter" tip="Berlaku ke semua chart di halaman ini." className="size-6">
          <InfoIcon />
        </IconAction>
      </h2>

      {datasets.length === 0 ? (
        <p className={MUTED}>Unggah dataset untuk mulai menyaring data.</p>
      ) : controls.length === 0 ? (
        <p className={MUTED}>Memuat kolom</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
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
        <ul className="flex list-none flex-wrap items-center gap-1.5" aria-label="Filter aktif">
          {globalFilters.map((pred, i) => (
            <li key={`g-${pred.table}.${pred.column}-${i}`} className={CHIP}>
              <span className="truncate" title={globalLabels[i]}>{globalLabels[i]}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`Hapus filter ${globalLabels[i]}`}
                onClick={() => applyGlobal(removePredicate(globalFilters, pred))}
              >
                <XIcon />
              </Button>
            </li>
          ))}
          {crossFilters.map((pred, i) => (
            <li key={`c-${pred.table}.${pred.column}-${i}`} className={cn(CHIP, "ring-1 ring-primary/40")}>
              <span className="truncate" title={crossLabels[i]}>Dipilih di chart · {crossLabels[i]}</span>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`Hapus Cross_Filter ${crossLabels[i]}`}
                onClick={() => onCrossFiltersChange(removePredicate(crossFilters, pred))}
              >
                <XIcon />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <Alert variant="destructive" className="basis-full">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
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
      : selected.length <= 2
        ? selected.map(formatScalar).join(", ")
        : `${selected.slice(0, 2).map(formatScalar).join(", ")} (+${selected.length - 2})`;

  return (
    <details
      className={cn(CONTROL, selected.length > 0 && CONTROL_ACTIVE, "open:z-20")}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      onKeyDown={(e) => {
        if (e.key === "Escape" && e.currentTarget.open) {
          e.currentTarget.open = false;
          e.currentTarget.querySelector("summary")?.focus();
        }
      }}
    >
      <summary className="flex h-7 max-w-64 cursor-pointer list-none items-center gap-1 rounded-full px-3 outline-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <span className={cn(CONTROL_NAME, selected.length > 0 && "text-primary")}>{label}:</span>
        <span className={cn("truncate", selected.length > 0 && "font-medium")}>{summary}</span>
        <CaretDownIcon aria-hidden className="size-3 shrink-0 text-muted-foreground transition-transform in-open:rotate-180" />
      </summary>
      {/* Melayang di atas canvas agar membuka dropdown tidak menggeser tata letak. */}
      <div className="absolute top-full left-0 z-20 mt-1 flex w-60 flex-col gap-1.5 rounded-lg border bg-popover p-2 text-foreground shadow-(--shadow-md)">
        <Input
          className="h-7"
          type="search"
          aria-label={`Cari ${label}`}
          placeholder={`Cari ${label.toLowerCase()}`}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <div
          className="flex max-h-56 flex-col gap-1 overflow-auto px-1 py-1"
          role="group"
          aria-label={label}
        >
          {options === null ? (
            <span className={MUTED}>Memuat nilai</span>
          ) : options.values.length === 0 ? (
            <span className={MUTED}>Tidak ada yang cocok.</span>
          ) : (
            options.values.map((v) => {
              const text = formatScalar(v);
              return (
                <label key={text} className="flex items-center gap-2 rounded-sm px-1 py-0.5 text-xs hover:bg-muted">
                  <input
                    type="checkbox"
                    data-slot="checkbox"
                    className={CHECK}
                    checked={selected.some((s) => s === v)}
                    onChange={() => toggle(v)}
                  />
                  {text}
                </label>
              );
            })
          )}
          {options?.truncated && (
            <span className={MUTED}>Ketik untuk mempersempit daftar.</span>
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
    <fieldset className={cn(CONTROL, (start || end) && CONTROL_ACTIVE, "flex h-7 items-center gap-1.5 px-3")}>
      <legend className="sr-only">{label}</legend>
      <span aria-hidden className={cn(CONTROL_NAME, (start || end) && "text-primary")}>{label}:</span>
      <div className="flex items-center gap-1 [&_input]:h-6 [&_input]:w-32 [&_input]:border-0 [&_input]:bg-transparent [&_input]:px-1 [&_input]:text-xs [&_label]:flex [&_label]:items-center [&_label]:gap-1 [&_label]:text-muted-foreground">
        <label>
          <span className="sr-only">Dari</span>
          <Input
            className="h-7"
            type="date"
            aria-label={`${label} dari`}
            value={start}
            onChange={(e) => set(e.target.value, end)}
          />
        </label>
        <span aria-hidden className="text-muted-foreground">–</span>
        <label>
          <span className="sr-only">Sampai</span>
          <Input
            className="h-7"
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

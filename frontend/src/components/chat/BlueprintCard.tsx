"use client";

import { useMemo, useState } from "react";
import type {
  BlueprintSlot,
  BlueprintSlotStatus,
  DashboardBlueprint,
  VisualType,
} from "@/lib/types";
import { Markdown } from "./Markdown";
import styles from "./chat.module.css";

const VISUALS: VisualType[] = ["kpi", "line", "bar", "pie", "scatter", "heatmap", "waterfall", "insight"];

const VISUAL_LABELS: Record<VisualType, string> = {
  kpi: "KPI",
  line: "Garis",
  bar: "Batang",
  pie: "Pie",
  scatter: "Scatter",
  heatmap: "Heatmap",
  waterfall: "Waterfall",
  insight: "Insight",
};

const STATUS_LABELS: Record<BlueprintSlotStatus, string> = {
  pending: "Menunggu",
  building: "Dibangun…",
  done: "Selesai",
  failed: "Gagal",
  skipped: "Dilewati",
};

export interface BlueprintCardProps {
  summary: string;
  blueprint: DashboardBlueprint;
  approved: boolean;
  selectedSlotIds?: string[];
  progress: Record<string, BlueprintSlotStatus>;
  disabled: boolean;
  /** Setujui slot terpilih (semua bila `selected` sama dengan seluruh slot). */
  onApprove: (selected: string[]) => void;
  /** Kirim permintaan revisi rancangan sebagai pesan chat. */
  onRevise: (message: string) => void;
}

interface Edit {
  purpose: string;
  visual: VisualType;
}

/** Ringkasan perubahan slot untuk pesan revisi. */
export function revisionMessage(
  slots: readonly BlueprintSlot[],
  edits: Readonly<Record<string, Edit>>,
  selected: ReadonlySet<string>,
): string {
  const lines: string[] = [];
  for (const slot of slots) {
    const edit = edits[slot.slot_id];
    if (!selected.has(slot.slot_id)) lines.push(`- Hapus slot ${slot.slot_id} (${slot.purpose}).`);
    else if (edit && (edit.purpose !== slot.purpose || edit.visual !== slot.visual)) {
      const parts: string[] = [];
      if (edit.purpose !== slot.purpose) parts.push(`tujuan menjadi "${edit.purpose}"`);
      if (edit.visual !== slot.visual) parts.push(`visual menjadi ${edit.visual}`);
      lines.push(`- Slot ${slot.slot_id}: ${parts.join(", ")}.`);
    }
  }
  return lines.length ? `Revisi rancangan dashboard:\n${lines.join("\n")}` : "Revisi rancangan dashboard.";
}

/** Kartu Dashboard_Blueprint: wireframe grid 12 kolom + persetujuan per slot (Req 37.5). */
export function BlueprintCard({
  summary,
  blueprint,
  approved,
  selectedSlotIds,
  progress,
  disabled,
  onApprove,
  onRevise,
}: BlueprintCardProps) {
  const slots = blueprint.slots;
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(selectedSlotIds ?? slots.map((s) => s.slot_id)),
  );
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const rows = useMemo(
    () => Math.max(1, ...slots.map((s) => (s.layout ? s.layout.y + s.layout.h : 0))),
    [slots],
  );
  const edited = slots.some((s) => {
    const e = edits[s.slot_id];
    return e && (e.purpose !== s.purpose || e.visual !== s.visual);
  });
  const shown = approved ? new Set(selectedSlotIds ?? slots.map((s) => s.slot_id)) : selected;

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const edit = (slot: BlueprintSlot, patch: Partial<Edit>) =>
    setEdits((prev) => {
      const base: Edit = prev[slot.slot_id] ?? { purpose: slot.purpose, visual: slot.visual };
      return { ...prev, [slot.slot_id]: { ...base, ...patch } };
    });

  return (
    <section className={styles.card} aria-label="Rancangan dashboard">
      <h3 className={styles.cardTitle}>Rancangan dashboard</h3>
      <Markdown text={summary} />
      {blueprint.brief.purpose && (
        <p className={styles.muted}>
          Tujuan: {blueprint.brief.purpose}
          {blueprint.brief.audience ? ` · Audiens: ${blueprint.brief.audience}` : ""}
        </p>
      )}
      {blueprint.brief.assumptions.length > 0 && (
        <p className={styles.muted}>Asumsi: {blueprint.brief.assumptions.join("; ")}</p>
      )}

      <div
        className={styles.wireframe}
        role="img"
        aria-label={`Wireframe ${slots.length} slot`}
        style={{ gridTemplateRows: `repeat(${rows}, 10px)` }}
      >
        {slots.map((s) =>
          s.layout ? (
            <div
              key={s.slot_id}
              className={`${styles.wireSlot} ${shown.has(s.slot_id) ? "" : styles.wireSlotOff}`}
              data-section={s.section}
              style={{
                gridColumn: `${s.layout.x + 1} / span ${s.layout.w}`,
                gridRow: `${s.layout.y + 1} / span ${s.layout.h}`,
              }}
              title={s.purpose}
            >
              {VISUAL_LABELS[(edits[s.slot_id]?.visual ?? s.visual) as VisualType]}
            </div>
          ) : null,
        )}
      </div>

      <ul className={styles.plain}>
        {slots.map((s) => {
          const status = progress[s.slot_id];
          const e = edits[s.slot_id];
          return (
            <li key={s.slot_id} className={styles.slotRow}>
              {!approved && (
                <input
                  type="checkbox"
                  checked={selected.has(s.slot_id)}
                  onChange={() => toggle(s.slot_id)}
                  aria-label={`Pilih slot ${s.slot_id}`}
                  disabled={disabled}
                />
              )}
              {approved ? (
                <span>
                  <strong>{VISUAL_LABELS[s.visual]}</strong> · {s.purpose}
                </span>
              ) : (
                <>
                  <select
                    aria-label={`Visual slot ${s.slot_id}`}
                    value={e?.visual ?? s.visual}
                    onChange={(ev) => edit(s, { visual: ev.target.value as VisualType })}
                    disabled={disabled}
                  >
                    {VISUALS.map((v) => (
                      <option key={v} value={v}>
                        {VISUAL_LABELS[v]}
                      </option>
                    ))}
                  </select>
                  <input
                    className={styles.slotPurpose}
                    aria-label={`Tujuan slot ${s.slot_id}`}
                    value={e?.purpose ?? s.purpose}
                    onChange={(ev) => edit(s, { purpose: ev.target.value })}
                    disabled={disabled}
                  />
                </>
              )}
              {status && (
                <span className={`${styles.badge} ${styles[`slot_${status}`] ?? ""}`}>{STATUS_LABELS[status]}</span>
              )}
            </li>
          );
        })}
      </ul>

      {approved ? (
        <span className={styles.muted}>Disetujui ({shown.size} slot)</span>
      ) : (
        <span className={styles.actions}>
          <button
            type="button"
            disabled={disabled || selected.size === 0 || edited}
            onClick={() => onApprove(slots.filter((s) => selected.has(s.slot_id)).map((s) => s.slot_id))}
          >
            Setujui terpilih ({selected.size})
          </button>
          <button
            type="button"
            disabled={disabled || edited}
            onClick={() => onApprove(slots.map((s) => s.slot_id))}
          >
            Setujui semua
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onRevise(revisionMessage(slots, edits, selected))}
          >
            Revisi
          </button>
        </span>
      )}
    </section>
  );
}

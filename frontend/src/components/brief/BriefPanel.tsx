"use client";

import { useState, type FormEvent } from "react";
import { applyCommand, isApiError } from "@/lib/api";
import type { Command, DashboardSnapshot, DesignBrief, PatchEvent, TimeGrain } from "@/lib/types";
import styles from "./brief.module.css";

export interface BriefClient {
  command(dashboardId: string, baseVersion: number, command: Command): Promise<PatchEvent>;
}

export const defaultBriefClient: BriefClient = {
  command: (id, base, command) => applyCommand(id, base, command),
};

export interface BriefPanelProps {
  snapshot: DashboardSnapshot;
  client?: BriefClient;
  /** Patch berhasil (state diperbarui lewat SSE; callback untuk test/penyegaran cepat). */
  onSaved?: (patch: PatchEvent) => void;
}

export const EMPTY_BRIEF: DesignBrief = {
  purpose: "",
  audience: "",
  key_questions: [],
  kpis: [],
  sections: [],
  time_grain: null,
  assumptions: [],
};

const GRAINS: { value: TimeGrain | ""; label: string }[] = [
  { value: "", label: "Tidak ditentukan" },
  { value: "day", label: "Harian" },
  { value: "week", label: "Mingguan" },
  { value: "month", label: "Bulanan" },
  { value: "quarter", label: "Kuartalan" },
  { value: "year", label: "Tahunan" },
];

const lines = (text: string) =>
  text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

/** Ubah isian form menjadi Design_Brief (KPI & bagian dipertahankan dari brief saat ini). */
export function briefFromForm(
  current: DesignBrief | null | undefined,
  form: { purpose: string; audience: string; questions: string; assumptions: string; grain: string },
): DesignBrief {
  const base = current ?? EMPTY_BRIEF;
  return {
    ...base,
    purpose: form.purpose.trim(),
    audience: form.audience.trim(),
    key_questions: lines(form.questions).slice(0, 10),
    assumptions: lines(form.assumptions).slice(0, 10),
    time_grain: (form.grain || null) as TimeGrain | null,
  };
}

/** Panel Design_Brief: lihat & edit maksud desain Dashboard (Req 36.3). */
export function BriefPanel({ snapshot, client = defaultBriefClient, onSaved }: BriefPanelProps) {
  const brief = snapshot.content.brief ?? null;
  const [form, setForm] = useState(() => toForm(brief));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // `brief` berubah bila patch baru diterapkan (agent atau pengguna lain):
  // sesuaikan form saat render (pola React "adjusting state when a prop changes").
  const briefKey = JSON.stringify(brief);
  const [syncedKey, setSyncedKey] = useState(briefKey);
  if (briefKey !== syncedKey) {
    setSyncedKey(briefKey);
    setForm(toForm(brief));
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const patch = await client.command(snapshot.id, snapshot.version, {
        type: "set_brief",
        brief: briefFromForm(brief, form),
      });
      setSavedAt(Date.now());
      onSaved?.(patch);
    } catch (err) {
      setError(isApiError(err) ? err.message : "Gagal menyimpan brief.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className={styles.panel}>
      <summary className={styles.summary}>
        Design Brief{brief?.purpose ? `: ${brief.purpose}` : " (belum diisi)"}
      </summary>
      <form className={styles.form} onSubmit={onSubmit}>
        <label>
          Tujuan
          <input value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} />
        </label>
        <label>
          Audiens
          <input value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })} />
        </label>
        <label>
          Pertanyaan bisnis kunci (satu per baris)
          <textarea rows={3} value={form.questions} onChange={(e) => setForm({ ...form, questions: e.target.value })} />
        </label>
        <label>
          Asumsi (satu per baris)
          <textarea rows={2} value={form.assumptions} onChange={(e) => setForm({ ...form, assumptions: e.target.value })} />
        </label>
        <label>
          Grain waktu
          <select value={form.grain} onChange={(e) => setForm({ ...form, grain: e.target.value })}>
            {GRAINS.map((g) => (
              <option key={g.value} value={g.value}>
                {g.label}
              </option>
            ))}
          </select>
        </label>
        {brief && brief.kpis.length > 0 && (
          <p className={styles.muted}>
            KPI: {brief.kpis.map((k) => `${k.metric} (${k.compare === "previous_period" ? "vs periode lalu" : k.compare === "target" ? "vs target" : "tanpa pembanding"})`).join(", ")}
          </p>
        )}
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        <span className={styles.actions}>
          <button type="submit" disabled={saving}>
            {saving ? "Menyimpan…" : "Simpan brief"}
          </button>
          {savedAt && !saving && !error && <span className={styles.muted}>Tersimpan</span>}
        </span>
      </form>
    </details>
  );
}

function toForm(brief: DesignBrief | null) {
  return {
    purpose: brief?.purpose ?? "",
    audience: brief?.audience ?? "",
    questions: (brief?.key_questions ?? []).join("\n"),
    assumptions: (brief?.assumptions ?? []).join("\n"),
    grain: brief?.time_grain ?? "",
  };
}

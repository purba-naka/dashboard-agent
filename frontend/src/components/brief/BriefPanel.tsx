"use client";

import { useState, type FormEvent } from "react";
import { CaretDownIcon } from "@phosphor-icons/react/ssr";
import { applyCommand, isApiError } from "@/lib/api";
import type { Command, DashboardSnapshot, DesignBrief, PatchEvent, TimeGrain } from "@/lib/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

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

// Radix Select menolak value kosong: "none" dipetakan ke grain null.
const NO_GRAIN = "none";
const GRAINS: { value: TimeGrain | typeof NO_GRAIN; label: string }[] = [
  { value: NO_GRAIN, label: "Tidak ditentukan" },
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
    <Card size="sm" className="py-0">
      <Collapsible>
        <CollapsibleTrigger className="group flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm font-semibold">
          <span className="min-w-0 flex-1 truncate">
            Design Brief{brief?.purpose ? `: ${brief.purpose}` : " (belum diisi)"}
          </span>
          <CaretDownIcon className="shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <form onSubmit={onSubmit} className="border-t px-3 pt-3 pb-3">
            <FieldGroup className="grid grid-cols-[repeat(auto-fit,minmax(16rem,1fr))] gap-3">
              <Field>
                <FieldLabel htmlFor="brief-purpose">Tujuan</FieldLabel>
                <Input id="brief-purpose" value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="brief-audience">Audiens</FieldLabel>
                <Input id="brief-audience" value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="brief-questions">Pertanyaan bisnis kunci (satu per baris)</FieldLabel>
                <Textarea id="brief-questions" rows={3} value={form.questions} onChange={(e) => setForm({ ...form, questions: e.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="brief-assumptions">Asumsi (satu per baris)</FieldLabel>
                <Textarea id="brief-assumptions" rows={2} value={form.assumptions} onChange={(e) => setForm({ ...form, assumptions: e.target.value })} />
              </Field>
              <Field>
                <FieldLabel htmlFor="brief-grain">Grain waktu</FieldLabel>
                <Select
                  value={form.grain || NO_GRAIN}
                  onValueChange={(v) => setForm({ ...form, grain: v === NO_GRAIN ? "" : v })}
                >
                  <SelectTrigger id="brief-grain" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {GRAINS.map((g) => (
                        <SelectItem key={g.value} value={g.value}>
                          {g.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
              {brief && brief.kpis.length > 0 && (
                <FieldDescription className="col-span-full">
                  KPI: {brief.kpis.map((k) => `${k.metric} (${k.compare === "previous_period" ? "vs periode lalu" : k.compare === "target" ? "vs target" : "tanpa pembanding"})`).join(", ")}
                </FieldDescription>
              )}
              {error && (
                <Alert variant="destructive" className="col-span-full">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <div className="col-span-full flex items-center gap-2">
                <Button type="submit" size="sm" disabled={saving}>
                  {saving ? "Menyimpan" : "Simpan brief"}
                </Button>
                {savedAt && !saving && !error && (
                  <span role="status" className="text-[0.8125rem] text-muted-foreground">Tersimpan</span>
                )}
              </div>
            </FieldGroup>
          </form>
        </CollapsibleContent>
      </Collapsible>
    </Card>
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

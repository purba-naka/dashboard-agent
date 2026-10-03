"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PencilSimpleIcon, WarningCircleIcon, WarningIcon, XIcon } from "@phosphor-icons/react/ssr";
import type { SemanticEntry, SemanticImportIssue, SemanticModelResponse, SemanticStatus } from "@/lib/types";
import { IconAction } from "@/components/IconAction";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  EDITABLE_FIELDS,
  FIELD_LABELS,
  KIND_LABELS,
  KIND_ORDER,
  STATUS_LABELS,
  defaultSemanticClient,
  entryTitle,
  errorMessage,
  groupEntries,
  importIssues,
  type SemanticClient,
} from "./client";

export interface SemanticPanelProps {
  workspaceId: string;
  /** Berubah (mis. event `semantic.updated`) → muat ulang. */
  refreshKey?: number | string;
  client?: SemanticClient;
  /** Jumlah entri berstatus usulan berubah; dipakai badge tab sidebar. */
  onCandidatesChange?: (count: number) => void;
}

type Filter = SemanticStatus | "all";

const STATUS_BADGE = {
  candidate: "secondary",
  confirmed: "outline",
  rejected: "destructive",
} as const satisfies Record<SemanticStatus, "secondary" | "outline" | "destructive">;

/**
 * Panel Model Semantik: entri per jenis dengan filter status, edit inline,
 * konfirmasi/tolak, konfirmasi semua, ekspor/impor YAML, dan entri yang
 * dibuang drafter (Req 31.7, 31.10, 31.11, 32.10).
 */
export function SemanticPanel({
  workspaceId,
  refreshKey = 0,
  client = defaultSemanticClient,
  onCandidatesChange,
}: SemanticPanelProps) {
  const [model, setModel] = useState<SemanticModelResponse | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<SemanticImportIssue[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    try {
      setModel(await client.load(workspaceId));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [client, workspaceId]);

  useEffect(() => {
    let cancelled = false;
    client
      .load(workspaceId)
      .then((next) => {
        if (cancelled) return;
        setModel(next);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [client, workspaceId, refreshKey]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (entry: SemanticEntry) => {
    setEditing(entry.id);
    setDraft(Object.fromEntries(EDITABLE_FIELDS[entry.kind].map((f) => [f, String(entry.body[f] ?? "")])));
  };

  const saveEdit = (entry: SemanticEntry) =>
    act(async () => {
      await client.update(workspaceId, entry.id, draft);
      setEditing(null);
    });

  const doExport = () =>
    act(async () => {
      const text = await client.exportYaml(workspaceId);
      const url = URL.createObjectURL(new Blob([text], { type: "text/yaml" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `semantic-${workspaceId}.yaml`;
      a.click();
      URL.revokeObjectURL(url);
    });

  const doImport = async (file: File) => {
    setIssues(null);
    setBusy(true);
    setError(null);
    try {
      setModel(await client.importYaml(workspaceId, await file.text()));
    } catch (err) {
      const found = importIssues(err);
      if (found) setIssues(found);
      else setError(errorMessage(err));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const entries = model?.entries ?? [];
  const groups = groupEntries(entries, filter);
  const candidates = entries.filter((e) => e.status === "candidate").length;
  const run = model?.draft_run;

  useEffect(() => {
    onCandidatesChange?.(candidates);
  }, [candidates, onCandidatesChange]);

  return (
    <section className="flex flex-col gap-3" aria-label="Model semantik">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Model semantik</h2>
        <span className="flex flex-wrap items-center gap-1">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy || candidates === 0}
            onClick={() => act(() => client.confirmAll(workspaceId))}
          >
            Konfirmasi semua ({candidates})
          </Button>
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={doExport}>
            Ekspor YAML
          </Button>
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => fileRef.current?.click()}>
            Impor YAML
          </Button>
          <input
            ref={fileRef}
            type="file"
            accept=".yaml,.yml,text/yaml"
            hidden
            aria-label="Berkas YAML model semantik"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void doImport(file);
            }}
          />
        </span>
      </header>

      {model?.domain && (
        <p className="text-xs text-muted-foreground">
          Domain: <strong className="text-foreground">{model.domain}</strong>
          {model.assumptions.length > 0 && ` · Asumsi: ${model.assumptions.join("; ")}`}
        </p>
      )}
      {run?.status === "running" && (
        <p className="text-xs text-muted-foreground">Agent sedang menyusun draft pemahaman data</p>
      )}
      {run?.status === "llm_failed" && (
        <Alert role="status">
          <WarningIcon />
          <AlertTitle>Pemahaman data belum lengkap</AlertTitle>
          <AlertDescription className="flex flex-col items-start gap-2">
            <p>
              Agent gagal melengkapi pemahaman data. Yang tampil hanya draft otomatis: label dan agregasi
              dasar. Deskripsi, sinonim, dan metrik usulan belum ada, jadi jawaban chat bisa kurang tepat.
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => act(() => client.redraft(workspaceId))}
            >
              Coba lagi
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <WarningCircleIcon />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {issues && (
        <Alert variant="destructive">
          <WarningCircleIcon />
          <AlertTitle>Impor ditolak, tidak ada yang diubah</AlertTitle>
          <AlertDescription>
            <ul className="flex flex-col gap-0.5">
              {issues.map((i, n) => (
                <li key={n}>
                  <code>{i.entry_key ?? i.path}</code>: {i.reason}
                </li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        spacing={0}
        aria-label="Filter status"
        value={filter}
        onValueChange={(v) => v && setFilter(v as Filter)}
      >
        {(["all", "candidate", "confirmed", "rejected"] as Filter[]).map((f) => (
          <ToggleGroupItem key={f} value={f}>
            {f === "all" ? "Semua" : STATUS_LABELS[f]}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {entries.length === 0 && (
        <p className="text-sm text-muted-foreground">Belum ada model semantik. Unggah dataset terlebih dahulu.</p>
      )}

      {KIND_ORDER.map((kind) =>
        groups[kind].length === 0 ? null : (
          <div key={kind} className="flex flex-col gap-1.5">
            <h3 className="text-xs font-medium text-muted-foreground">
              {KIND_LABELS[kind]} <span className="tabular-nums">({groups[kind].length})</span>
            </h3>
            <ul className="flex flex-col gap-1.5">
              {groups[kind].map((entry) => {
                const title = entryTitle(entry);
                return (
                  <li key={entry.id} className="flex items-start gap-2 rounded-md border px-2.5 py-2">
                    <div className="flex min-w-0 flex-1 flex-col gap-1 text-sm break-words">
                      <span className="flex flex-wrap items-center gap-1">
                        <strong className="font-medium">{title}</strong>
                        <Badge variant={STATUS_BADGE[entry.status]}>{STATUS_LABELS[entry.status]}</Badge>
                        {!entry.valid && <Badge variant="destructive">Tidak valid</Badge>}
                        {entry.source === "user" && <Badge variant="outline">Diedit pengguna</Badge>}
                      </span>
                      {entry.kind === "metric" && (
                        <code className="font-mono text-xs break-all">{String(entry.body.expr ?? "")}</code>
                      )}
                      {entry.kind === "verified_query" && (
                        <code className="font-mono text-xs break-all">{String(entry.body.sql ?? "")}</code>
                      )}
                      {typeof entry.body.description === "string" && entry.body.description && (
                        <span className="text-xs text-muted-foreground">{entry.body.description}</span>
                      )}
                      {editing === entry.id && (
                        <form
                          className="pt-1"
                          onSubmit={(e) => {
                            e.preventDefault();
                            void saveEdit(entry);
                          }}
                        >
                          <FieldGroup className="gap-2">
                            {EDITABLE_FIELDS[entry.kind].map((field) => (
                              <Field key={field} className="gap-1">
                                <FieldLabel htmlFor={`${entry.id}-${field}`}>{FIELD_LABELS[field] ?? field}</FieldLabel>
                                <Input
                                  id={`${entry.id}-${field}`}
                                  value={draft[field] ?? ""}
                                  onChange={(e) => setDraft((d) => ({ ...d, [field]: e.target.value }))}
                                />
                              </Field>
                            ))}
                            <span className="flex gap-1">
                              <Button type="submit" size="sm" disabled={busy}>
                                Simpan
                              </Button>
                              <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(null)}>
                                Batal
                              </Button>
                            </span>
                          </FieldGroup>
                        </form>
                      )}
                    </div>
                    <span className="flex shrink-0 items-center gap-0.5">
                      {entry.status !== "confirmed" && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`Konfirmasi ${title}`}
                          onClick={() => act(() => client.confirm(workspaceId, entry.id))}
                        >
                          Konfirmasi
                        </Button>
                      )}
                      {entry.status !== "rejected" && (
                        <IconAction
                          label={`Tolak ${title}`}
                          tip="Tolak"
                          disabled={busy}
                          onClick={() => act(() => client.reject(workspaceId, entry.id))}
                        >
                          <XIcon />
                        </IconAction>
                      )}
                      {EDITABLE_FIELDS[entry.kind].length > 0 && editing !== entry.id && (
                        <IconAction label={`Edit ${title}`} tip="Edit" disabled={busy} onClick={() => startEdit(entry)}>
                          <PencilSimpleIcon />
                        </IconAction>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        ),
      )}

      {run && run.discarded.length > 0 && (
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">{run.discarded.length} usulan dibuang drafter</summary>
          <ul className="flex flex-col gap-0.5 pt-1">
            {run.discarded.map((d) => (
              <li key={d.entry_key}>
                <code>{d.entry_key}</code>: {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

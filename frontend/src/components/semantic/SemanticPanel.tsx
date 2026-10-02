"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { SemanticEntry, SemanticImportIssue, SemanticModelResponse, SemanticStatus } from "@/lib/types";
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
import styles from "./semantic.module.css";

export interface SemanticPanelProps {
  workspaceId: string;
  /** Berubah (mis. event `semantic.updated`) → muat ulang. */
  refreshKey?: number | string;
  client?: SemanticClient;
}

type Filter = SemanticStatus | "all";

/**
 * Panel Model Semantik: entri per jenis dengan filter status, edit inline,
 * konfirmasi/tolak, konfirmasi semua, ekspor/impor YAML, dan entri yang
 * dibuang drafter (Req 31.7, 31.10, 31.11, 32.10).
 */
export function SemanticPanel({ workspaceId, refreshKey = 0, client = defaultSemanticClient }: SemanticPanelProps) {
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

  return (
    <section className={styles.panel} aria-label="Model semantik">
      <header className={styles.header}>
        <h2 className={styles.title}>Model semantik</h2>
        <span className={styles.actions}>
          <button type="button" disabled={busy || candidates === 0} onClick={() => act(() => client.confirmAll(workspaceId))}>
            Konfirmasi semua ({candidates})
          </button>
          <button type="button" disabled={busy} onClick={doExport}>
            Ekspor YAML
          </button>
          <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
            Impor YAML
          </button>
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
        <p className={styles.muted}>
          Domain: <strong>{model.domain}</strong>
          {model.assumptions.length > 0 && ` · Asumsi: ${model.assumptions.join("; ")}`}
        </p>
      )}
      {run?.status === "running" && <p className={styles.muted}>Agent sedang menyusun draft pemahaman data…</p>}
      {run?.status === "llm_failed" && (
        <div className={styles.warn} role="status">
          <p>
            Agent gagal melengkapi pemahaman data. Yang tampil hanya draft otomatis: label dan agregasi
            dasar. Deskripsi, sinonim, dan metrik usulan belum ada, jadi jawaban chat bisa kurang tepat.
          </p>
          <button type="button" disabled={busy} onClick={() => act(() => client.redraft(workspaceId))}>
            Coba lagi
          </button>
        </div>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {issues && (
        <div className={styles.error} role="alert">
          <p>Impor ditolak; tidak ada yang diubah. Perbaiki entri berikut:</p>
          <ul>
            {issues.map((i, n) => (
              <li key={n}>
                <code>{i.entry_key ?? i.path}</code>: {i.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className={styles.filters} role="radiogroup" aria-label="Filter status">
        {(["all", "candidate", "confirmed", "rejected"] as Filter[]).map((f) => (
          <label key={f}>
            <input type="radio" name="semantic-filter" checked={filter === f} onChange={() => setFilter(f)} />
            {f === "all" ? "Semua" : STATUS_LABELS[f]}
          </label>
        ))}
      </div>

      {entries.length === 0 && <p className={styles.muted}>Belum ada model semantik. Unggah dataset terlebih dahulu.</p>}

      {KIND_ORDER.map((kind) =>
        groups[kind].length === 0 ? null : (
          <div key={kind} className={styles.group}>
            <h3 className={styles.groupTitle}>
              {KIND_LABELS[kind]} ({groups[kind].length})
            </h3>
            <ul className={styles.list}>
              {groups[kind].map((entry) => {
                const title = entryTitle(entry);
                return (
                  <li key={entry.id} className={styles.row}>
                    <div className={styles.rowMain}>
                      <span>
                        <strong>{title}</strong>{" "}
                        <span className={`${styles.badge} ${styles[`status_${entry.status}`] ?? ""}`}>
                          {STATUS_LABELS[entry.status]}
                        </span>
                        {!entry.valid && <span className={`${styles.badge} ${styles.status_rejected}`}>Tidak valid</span>}
                        {entry.source === "user" && <span className={styles.badge}>Diedit pengguna</span>}
                      </span>
                      {entry.kind === "metric" && <code className={styles.code}>{String(entry.body.expr ?? "")}</code>}
                      {entry.kind === "verified_query" && <code className={styles.code}>{String(entry.body.sql ?? "")}</code>}
                      {typeof entry.body.description === "string" && entry.body.description && (
                        <span className={styles.muted}>{entry.body.description}</span>
                      )}
                      {editing === entry.id && (
                        <form
                          className={styles.editForm}
                          onSubmit={(e) => {
                            e.preventDefault();
                            void saveEdit(entry);
                          }}
                        >
                          {EDITABLE_FIELDS[entry.kind].map((field) => (
                            <label key={field}>
                              {FIELD_LABELS[field] ?? field}
                              <input
                                value={draft[field] ?? ""}
                                onChange={(e) => setDraft((d) => ({ ...d, [field]: e.target.value }))}
                              />
                            </label>
                          ))}
                          <span className={styles.actions}>
                            <button type="submit" disabled={busy}>
                              Simpan
                            </button>
                            <button type="button" onClick={() => setEditing(null)}>
                              Batal
                            </button>
                          </span>
                        </form>
                      )}
                    </div>
                    <span className={styles.actions}>
                      {entry.status !== "confirmed" && (
                        <button
                          type="button"
                          disabled={busy}
                          aria-label={`Konfirmasi ${title}`}
                          onClick={() => act(() => client.confirm(workspaceId, entry.id))}
                        >
                          Konfirmasi
                        </button>
                      )}
                      {entry.status !== "rejected" && (
                        <button
                          type="button"
                          disabled={busy}
                          aria-label={`Tolak ${title}`}
                          onClick={() => act(() => client.reject(workspaceId, entry.id))}
                        >
                          Tolak
                        </button>
                      )}
                      {EDITABLE_FIELDS[entry.kind].length > 0 && editing !== entry.id && (
                        <button type="button" disabled={busy} aria-label={`Edit ${title}`} onClick={() => startEdit(entry)}>
                          Edit
                        </button>
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
        <details>
          <summary className={styles.muted}>{run.discarded.length} usulan dibuang drafter</summary>
          <ul>
            {run.discarded.map((d) => (
              <li key={d.entry_key} className={styles.muted}>
                <code>{d.entry_key}</code>: {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

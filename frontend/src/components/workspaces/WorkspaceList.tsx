"use client";

import { useCallback, useEffect, useState } from "react";
import { isAbortError } from "@/lib/api";
import type { Workspace } from "@/lib/types";
import { CreateWorkspaceForm } from "./CreateWorkspaceForm";
import { DeleteWorkspaceDialog } from "./DeleteWorkspaceDialog";
import { WorkspaceRow } from "./WorkspaceRow";
import { defaultWorkspaceClient, errorMessage, type WorkspaceClient } from "./client";
import styles from "./workspaces.module.css";

export interface WorkspaceListProps {
  client?: WorkspaceClient;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; workspaces: Workspace[] };

/** Daftar Workspace: buat, ganti nama, dan hapus dengan konfirmasi nama (Req 1.1, 1.3, 1.4). */
export function WorkspaceList({ client = defaultWorkspaceClient }: WorkspaceListProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [deleting, setDeleting] = useState<Workspace | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    client
      .list({ signal: ctrl.signal })
      .then((workspaces) => setState({ kind: "ready", workspaces }))
      .catch((err: unknown) => {
        if (!isAbortError(err)) setState({ kind: "error", message: errorMessage(err) });
      });
    return () => ctrl.abort();
  }, [client, reloadKey]);

  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setReloadKey((k) => k + 1);
  }, []);

  const update = (fn: (list: Workspace[]) => Workspace[]) =>
    setState((s) => (s.kind === "ready" ? { kind: "ready", workspaces: fn(s.workspaces) } : s));

  async function handleCreate(name: string) {
    const created = await client.create(name);
    update((list) => [created, ...list.filter((w) => w.id !== created.id)]);
    setStatus(`Workspace “${created.name}” dibuat.`);
  }

  async function handleRename(id: string, name: string) {
    const renamed = await client.rename(id, name);
    update((list) => list.map((w) => (w.id === renamed.id ? renamed : w)));
    setStatus(`Nama Workspace diganti menjadi “${renamed.name}”.`);
  }

  async function handleDelete(target: Workspace, confirmName: string) {
    await client.remove(target.id, confirmName);
    update((list) => list.filter((w) => w.id !== target.id));
    setDeleting(null);
    setStatus(`Workspace “${target.name}” dihapus.`);
  }

  return (
    <section aria-labelledby="workspace-list-heading" className={styles.card}>
      <h2 id="workspace-list-heading">Workspace</h2>

      <CreateWorkspaceForm onCreate={handleCreate} />

      <p role="status" aria-live="polite" className={styles.muted}>
        {status}
      </p>

      {state.kind === "loading" && (
        <div aria-busy="true" className={styles.section}>
          <span className={styles.muted}>Memuat Workspace…</span>
          <div className={styles.skeleton} />
          <div className={styles.skeleton} />
        </div>
      )}

      {state.kind === "error" && (
        <div role="alert" className={styles.section}>
          <p className={styles.error}>Gagal memuat Workspace: {state.message}</p>
          <div>
            <button type="button" className={styles.button} onClick={retry}>
              Coba lagi
            </button>
          </div>
        </div>
      )}

      {state.kind === "ready" &&
        (state.workspaces.length === 0 ? (
          <p className={styles.empty}>
            Belum ada Workspace. Buat Workspace pertama untuk mulai mengunggah data.
          </p>
        ) : (
          <ul className={styles.list} aria-label="Daftar Workspace">
            {state.workspaces.map((ws) => (
              <WorkspaceRow
                key={ws.id}
                workspace={ws}
                onRename={(name) => handleRename(ws.id, name)}
                onRequestDelete={() => setDeleting(ws)}
              />
            ))}
          </ul>
        ))}

      {deleting && (
        <DeleteWorkspaceDialog
          workspace={deleting}
          onConfirm={(confirmName) => handleDelete(deleting, confirmName)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </section>
  );
}

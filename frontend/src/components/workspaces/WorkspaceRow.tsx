"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";
import type { Workspace } from "@/lib/types";
import {
  errorMessage,
  formatDateTime,
  MAX_WORKSPACE_NAME_LENGTH,
  validateWorkspaceName,
} from "./client";
import styles from "./workspaces.module.css";

export interface WorkspaceRowProps {
  workspace: Workspace;
  /** Dipanggil dengan nama baru yang sudah di-trim (Req 1.3). */
  onRename: (name: string) => Promise<void>;
  onRequestDelete: () => void;
}

/** Satu baris daftar Workspace dengan ganti nama inline dan pemicu hapus. */
export function WorkspaceRow({ workspace, onRename, onRequestDelete }: WorkspaceRowProps) {
  const inputId = useId();
  const errorId = useId();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(workspace.name);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function startEdit() {
    setDraft(workspace.name);
    setError(null);
    setEditing(true);
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const invalid = validateWorkspaceName(draft);
    if (invalid) {
      setError(invalid);
      return;
    }
    const next = draft.trim();
    if (next === workspace.name) {
      setEditing(false);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await onRename(next);
      setEditing(false);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  if (editing) {
    return (
      <li className={styles.row}>
        <form
          className={styles.form}
          style={{ flex: 1 }}
          onSubmit={handleSubmit}
          aria-label={`Ganti nama ${workspace.name}`}
          noValidate
        >
          <div className={styles.field}>
            <label htmlFor={inputId}>Nama baru untuk “{workspace.name}”</label>
            <input
              id={inputId}
              className={styles.input}
              value={draft}
              maxLength={MAX_WORKSPACE_NAME_LENGTH}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && !pending) setEditing(false);
              }}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? errorId : undefined}
              disabled={pending}
              autoComplete="off"
              autoFocus
            />
          </div>
          <div className={styles.actions}>
            <button type="submit" className={styles.button} disabled={pending}>
              {pending ? "Menyimpan…" : "Simpan"}
            </button>
            <button
              type="button"
              className={styles.button}
              onClick={() => setEditing(false)}
              disabled={pending}
            >
              Batal
            </button>
          </div>
          {error && (
            <p id={errorId} role="alert" className={styles.error} style={{ flexBasis: "100%" }}>
              {error}
            </p>
          )}
        </form>
      </li>
    );
  }

  return (
    <li className={styles.row}>
      <div className={styles.rowMain}>
        <Link href={`/w/${encodeURIComponent(workspace.id)}`} className={styles.link}>
          {workspace.name}
        </Link>
        <span className={styles.muted}>Dibuat {formatDateTime(workspace.created_at)}</span>
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.button}
          onClick={startEdit}
          aria-label={`Ganti nama ${workspace.name}`}
        >
          Ganti nama
        </button>
        <button
          type="button"
          className={styles.dangerButton}
          onClick={onRequestDelete}
          aria-label={`Hapus ${workspace.name}`}
        >
          Hapus
        </button>
      </div>
    </li>
  );
}

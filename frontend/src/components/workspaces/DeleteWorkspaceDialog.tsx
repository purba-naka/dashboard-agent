"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import type { Workspace } from "@/lib/types";
import { errorMessage } from "./client";
import styles from "./workspaces.module.css";

export interface DeleteWorkspaceDialogProps {
  workspace: Workspace;
  /** Dipanggil dengan nama konfirmasi apa adanya; lempar error untuk menampilkannya. */
  onConfirm: (confirmName: string) => Promise<void>;
  onCancel: () => void;
}

/**
 * Dialog konfirmasi hapus Workspace (Req 1.4). Tombol hapus baru aktif bila
 * pengguna mengetik nama Workspace persis sama (backend membandingkan apa adanya).
 */
export function DeleteWorkspaceDialog({ workspace, onConfirm, onCancel }: DeleteWorkspaceDialogProps) {
  const titleId = useId();
  const descId = useId();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const matches = typed === workspace.name;

  // Fokus ke input saat dibuka; kembalikan fokus ke pemicu saat ditutup.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!matches || pending) return;
    setPending(true);
    setError(null);
    try {
      await onConfirm(typed);
    } catch (err) {
      setError(errorMessage(err));
      setPending(false);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape" && !pending) {
      e.stopPropagation();
      onCancel();
    }
  }

  return (
    <div className={styles.backdrop} onKeyDown={handleKeyDown}>
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className={styles.dialog}
      >
        <h2 id={titleId}>Hapus Workspace “{workspace.name}”?</h2>
        <p id={descId}>
          Semua Dataset, file upload, Dashboard, dan riwayat chat di Workspace ini akan dihapus
          permanen. Ketik <strong>{workspace.name}</strong> untuk mengonfirmasi.
        </p>
        <form className={styles.section} onSubmit={handleSubmit}>
          <div className={styles.field}>
            <label htmlFor={inputId}>Nama Workspace untuk konfirmasi</label>
            <input
              ref={inputRef}
              id={inputId}
              className={styles.input}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={pending}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          {error && (
            <p role="alert" className={styles.error}>
              {error}
            </p>
          )}
          <div className={styles.actions}>
            <button type="submit" className={styles.dangerButton} disabled={!matches || pending}>
              {pending ? "Menghapus" : "Hapus permanen"}
            </button>
            <button type="button" className={styles.button} onClick={onCancel} disabled={pending}>
              Batal
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

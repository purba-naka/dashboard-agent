"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import type { WorkspaceSummary } from "@/lib/types";
import {
  errorMessage,
  formatDateTime,
  formatRelative,
  MAX_WORKSPACE_NAME_LENGTH,
  validateWorkspaceName,
} from "./client";
import styles from "./workspaces.module.css";

export interface WorkspaceRowProps {
  workspace: WorkspaceSummary;
  /** Dipanggil dengan nama baru yang sudah di-trim (Req 1.3). */
  onRename: (name: string) => Promise<void>;
  onRequestDelete: () => void;
}

function contentSummary(ws: WorkspaceSummary): string {
  if (ws.dataset_count === 0 && ws.dashboard_count === 0) return "Belum ada data";
  return `${ws.dataset_count} dataset · ${ws.dashboard_count} dashboard`;
}

/** Kartu Workspace: seluruh kartu menuju studio, aksi ada di menu. */
export function WorkspaceRow({ workspace, onRename, onRequestDelete }: WorkspaceRowProps) {
  const inputId = useId();
  const errorId = useId();
  const menuId = useId();
  const [editing, setEditing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState(workspace.name);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    menuRef.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const onPointerDown = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menuOpen]);

  function closeMenu() {
    setMenuOpen(false);
    triggerRef.current?.focus();
  }

  function startEdit() {
    setMenuOpen(false);
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
      <li className={styles.tile}>
        <form
          className={styles.renameForm}
          onSubmit={handleSubmit}
          aria-label={`Ganti nama ${workspace.name}`}
          noValidate
        >
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
          {error && (
            <p id={errorId} role="alert" className={styles.error}>
              {error}
            </p>
          )}
          <div className={styles.actions}>
            <button type="submit" disabled={pending}>
              {pending ? "Menyimpan" : "Simpan"}
            </button>
            <button type="button" onClick={() => setEditing(false)} disabled={pending}>
              Batal
            </button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li className={styles.tile}>
      <h3 className={styles.tileTitle}>
        <Link href={`/w/${encodeURIComponent(workspace.id)}`} className={styles.tileLink}>
          {workspace.name}
        </Link>
      </h3>
      <p className={styles.tileStats}>{contentSummary(workspace)}</p>
      <p className={styles.muted}>
        Aktif{" "}
        <time dateTime={workspace.last_activity_at} title={formatDateTime(workspace.last_activity_at)}>
          {formatRelative(workspace.last_activity_at)}
        </time>
      </p>

      <div
        ref={menuRef}
        className={styles.menuWrap}
        onKeyDown={(e) => {
          if (e.key === "Escape" && menuOpen) closeMenu();
        }}
      >
        <button
          ref={triggerRef}
          type="button"
          className={styles.menuTrigger}
          aria-label={`Aksi untuk ${workspace.name}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-controls={menuOpen ? menuId : undefined}
          onClick={() => setMenuOpen((o) => !o)}
        >
          <span aria-hidden="true">⋯</span>
        </button>
        {menuOpen && (
          <div id={menuId} role="menu" className={styles.menu} aria-label={`Aksi ${workspace.name}`}>
            <button type="button" role="menuitem" onClick={startEdit}>
              Ganti nama
            </button>
            <button
              type="button"
              role="menuitem"
              className={styles.menuDanger}
              onClick={() => {
                setMenuOpen(false);
                onRequestDelete();
              }}
            >
              Hapus
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

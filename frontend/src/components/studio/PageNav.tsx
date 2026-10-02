"use client";

import { useState, type FormEvent } from "react";
import type { DashboardSummary } from "@/lib/types";
import styles from "./studio.module.css";

export interface PageNavProps {
  pages: readonly DashboardSummary[];
  activeId: string | null;
  busy?: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

/** Navigasi halaman dashboard: satu halaman = satu Dashboard di Workspace. */
export function PageNav({ pages, activeId, busy, onSelect, onCreate, onRename, onDelete }: PageNavProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const active = pages.find((p) => p.id === activeId);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const title = draft.trim();
    if (editing && title && title !== active?.title) onRename(editing, title);
    setEditing(null);
  };

  return (
    <nav aria-label="Halaman dashboard" className={styles.pageNav} data-export-hide>
      <ul className={styles.pageList}>
        {pages.map((p) => (
          <li key={p.id}>
            {editing === p.id ? (
              <form onSubmit={submit}>
                <input
                  aria-label="Nama halaman"
                  value={draft}
                  autoFocus
                  maxLength={100}
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => setEditing(null)}
                  onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
                />
              </form>
            ) : (
              <button
                type="button"
                className={styles.pageButton}
                aria-current={p.id === activeId ? "page" : undefined}
                onClick={() => onSelect(p.id)}
              >
                {p.title}
              </button>
            )}
          </li>
        ))}
      </ul>
      <div className={styles.pageActions}>
        <button type="button" disabled={busy} onClick={onCreate}>
          + Halaman
        </button>
        {active && (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(active.title);
                setEditing(active.id);
              }}
            >
              Ganti nama
            </button>
            <button type="button" disabled={pages.length <= 1} onClick={() => onDelete(active.id)}>
              Hapus
            </button>
          </>
        )}
      </div>
    </nav>
  );
}

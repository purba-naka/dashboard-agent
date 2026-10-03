"use client";

import { useState, type FormEvent } from "react";
import { PencilSimpleIcon, PlusIcon, TrashIcon } from "@phosphor-icons/react/ssr";
import type { DashboardSummary } from "@/lib/types";
import { IconAction } from "@/components/IconAction";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

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
  const [confirmDelete, setConfirmDelete] = useState(false);
  const active = pages.find((p) => p.id === activeId);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const title = draft.trim();
    if (editing && title && title !== active?.title) onRename(editing, title);
    setEditing(null);
  };

  return (
    <nav aria-label="Halaman dashboard" className="flex flex-col gap-2" data-export-hide>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Halaman</h2>
        <span className="flex items-center gap-0.5">
          {active && (
            <>
              <IconAction
                label="Ganti nama"
                onClick={() => {
                  setDraft(active.title);
                  setEditing(active.id);
                }}
              >
                <PencilSimpleIcon />
              </IconAction>
              <IconAction label="Hapus" disabled={pages.length <= 1} onClick={() => setConfirmDelete(true)}>
                <TrashIcon />
              </IconAction>
            </>
          )}
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCreate}>
            <PlusIcon data-icon="inline-start" />
            Halaman
          </Button>
        </span>
      </div>
      <ul className="flex flex-col gap-0.5">
        {pages.map((p) => (
          <li key={p.id}>
            {editing === p.id ? (
              <form onSubmit={submit}>
                <Input
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
              <Button
                type="button"
                variant="ghost"
                className="w-full justify-start border-l-2 border-transparent rounded-l-none aria-[current=page]:border-l-primary aria-[current=page]:bg-muted aria-[current=page]:font-semibold"
                aria-current={p.id === activeId ? "page" : undefined}
                onClick={() => onSelect(p.id)}
              >
                <span className="truncate">{p.title}</span>
              </Button>
            )}
          </li>
        ))}
      </ul>

      <AlertDialog open={confirmDelete && Boolean(active)} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Hapus halaman “{active?.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Semua item di halaman ini ikut terhapus. Tindakan ini tidak bisa dibatalkan.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Batal</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => active && onDelete(active.id)}>
              Hapus
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </nav>
  );
}

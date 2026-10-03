"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";
import { DotsThreeIcon, PencilSimpleIcon, TrashIcon } from "@phosphor-icons/react/ssr";
import type { WorkspaceSummary } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  errorMessage,
  formatDateTime,
  formatRelative,
  MAX_WORKSPACE_NAME_LENGTH,
  validateWorkspaceName,
} from "./client";

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

const TILE = "relative flex min-w-0 flex-col gap-1 rounded-lg border bg-card p-4 transition-colors";

/** Kartu Workspace: seluruh kartu menuju studio, aksi ada di menu. */
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
      <li className={TILE}>
        <form
          className="flex flex-col gap-3"
          onSubmit={handleSubmit}
          aria-label={`Ganti nama ${workspace.name}`}
          noValidate
        >
          <Field data-invalid={error ? true : undefined} data-disabled={pending || undefined}>
            <FieldLabel htmlFor={inputId}>Nama baru untuk “{workspace.name}”</FieldLabel>
            <Input
              id={inputId}
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
              <FieldError id={errorId}>
                {error}
              </FieldError>
            )}
          </Field>
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={pending}>
              {pending ? "Menyimpan" : "Simpan"}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={pending}>
              Batal
            </Button>
          </div>
        </form>
      </li>
    );
  }

  return (
    <li className={cn(TILE, "group pr-12 hover:border-input focus-within:border-input")}>
      <h3 className="text-base font-semibold break-words">
        {/* ::after menutup seluruh kartu agar semua area bisa diklik; menu tetap di atasnya. */}
        <Link
          href={`/w/${encodeURIComponent(workspace.id)}`}
          className="outline-none after:absolute after:inset-0 after:rounded-[inherit] group-hover:underline focus-visible:after:outline-2 focus-visible:after:outline-offset-2 focus-visible:after:outline-ring"
        >
          {workspace.name}
        </Link>
      </h3>
      <p className="text-sm tabular-nums">{contentSummary(workspace)}</p>
      <p className="text-[0.8125rem] text-muted-foreground">
        Aktif{" "}
        <time dateTime={workspace.last_activity_at} title={formatDateTime(workspace.last_activity_at)}>
          {formatRelative(workspace.last_activity_at)}
        </time>
      </p>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-2 right-2 size-11"
            aria-label={`Aksi untuk ${workspace.name}`}
          >
            <DotsThreeIcon weight="bold" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" aria-label={`Aksi ${workspace.name}`}>
          <DropdownMenuGroup>
            <DropdownMenuItem onSelect={startEdit}>
              <PencilSimpleIcon />
              Ganti nama
            </DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onSelect={onRequestDelete}>
              <TrashIcon />
              Hapus
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  );
}

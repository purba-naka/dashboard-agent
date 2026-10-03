"use client";

import { useId, useRef, useState, type FormEvent } from "react";
import type { Workspace } from "@/lib/types";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { errorMessage } from "./client";

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
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const matches = typed === workspace.name;

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

  return (
    <AlertDialog open onOpenChange={(open) => !open && !pending && onCancel()}>
      <AlertDialogContent
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          inputRef.current?.focus();
        }}
        onEscapeKeyDown={(e) => pending && e.preventDefault()}
      >
        <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
          <AlertDialogHeader>
            <AlertDialogTitle>Hapus Workspace “{workspace.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Semua Dataset, file upload, Dashboard, dan riwayat chat di Workspace ini akan dihapus
              permanen. Ketik <strong className="text-foreground">{workspace.name}</strong> untuk
              mengonfirmasi.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Field data-invalid={error ? true : undefined} data-disabled={pending || undefined}>
            <FieldLabel htmlFor={inputId}>Nama Workspace untuk konfirmasi</FieldLabel>
            <Input
              ref={inputRef}
              id={inputId}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              disabled={pending}
              autoComplete="off"
              spellCheck={false}
            />
            {error && <FieldError>{error}</FieldError>}
          </Field>
          <AlertDialogFooter>
            <AlertDialogCancel type="button" disabled={pending}>
              Batal
            </AlertDialogCancel>
            <Button type="submit" variant="destructive" disabled={!matches || pending}>
              {pending ? "Menghapus" : "Hapus permanen"}
            </Button>
          </AlertDialogFooter>
        </form>
      </AlertDialogContent>
    </AlertDialog>
  );
}

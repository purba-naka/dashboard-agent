"use client";

import { useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { errorMessage, MAX_WORKSPACE_NAME_LENGTH, validateWorkspaceName } from "./client";

export interface CreateWorkspaceFormProps {
  /** Dipanggil dengan nama yang sudah di-trim; lempar error untuk menampilkannya. */
  onCreate: (name: string) => Promise<void>;
  /** Bila ada, tampilkan tombol Batal (dan Escape) untuk menutup form. */
  onCancel?: () => void;
}

/** Form buat Workspace baru (Req 1.1). */
export function CreateWorkspaceForm({ onCreate, onCancel }: CreateWorkspaceFormProps) {
  const inputId = useId();
  const errorId = useId();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const invalid = validateWorkspaceName(name);
    if (invalid) {
      setError(invalid);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await onCreate(name.trim());
      setName("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={handleSubmit}
      aria-label="Buat Workspace"
      noValidate
    >
      <Field
        className="min-w-64 flex-1"
        data-invalid={error ? true : undefined}
        data-disabled={pending || undefined}
      >
        <FieldLabel htmlFor={inputId}>Nama Workspace baru</FieldLabel>
        <Input
          id={inputId}
          value={name}
          maxLength={MAX_WORKSPACE_NAME_LENGTH}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && onCancel && !pending) onCancel();
          }}
          autoFocus={onCancel !== undefined}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          disabled={pending}
          autoComplete="off"
        />
      </Field>
      <Button type="submit" disabled={pending}>
        {pending ? "Membuat" : "Buat Workspace"}
      </Button>
      {onCancel && (
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>
          Batal
        </Button>
      )}
      {error && (
        <FieldError id={errorId} className="basis-full">
          {error}
        </FieldError>
      )}
    </form>
  );
}

"use client";

import { useId, useState, type FormEvent } from "react";
import { errorMessage, MAX_WORKSPACE_NAME_LENGTH, validateWorkspaceName } from "./client";
import styles from "./workspaces.module.css";

export interface CreateWorkspaceFormProps {
  /** Dipanggil dengan nama yang sudah di-trim; lempar error untuk menampilkannya. */
  onCreate: (name: string) => Promise<void>;
}

/** Form buat Workspace baru (Req 1.1). */
export function CreateWorkspaceForm({ onCreate }: CreateWorkspaceFormProps) {
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
    <form className={styles.form} onSubmit={handleSubmit} aria-label="Buat Workspace" noValidate>
      <div className={styles.field}>
        <label htmlFor={inputId}>Nama Workspace baru</label>
        <input
          id={inputId}
          className={styles.input}
          value={name}
          maxLength={MAX_WORKSPACE_NAME_LENGTH}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          disabled={pending}
          autoComplete="off"
        />
      </div>
      <button type="submit" className={styles.button} disabled={pending}>
        {pending ? "Membuat…" : "Buat Workspace"}
      </button>
      {error && (
        <p id={errorId} role="alert" className={styles.error} style={{ flexBasis: "100%" }}>
          {error}
        </p>
      )}
    </form>
  );
}

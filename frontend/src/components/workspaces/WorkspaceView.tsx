"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { isAbortError, isApiError } from "@/lib/api";
import type { WorkspaceDetail } from "@/lib/types";
import { defaultWorkspaceClient, errorMessage, formatDateTime, type WorkspaceClient } from "./client";
import styles from "./workspaces.module.css";

export interface WorkspaceViewProps {
  workspaceId: string;
  client?: WorkspaceClient;
  /**
   * Pembuat isi slot komponen (task 23.1): menerima detail Workspace terbaru
   * dan `reload`, mengembalikan node untuk tiap `data-slot`.
   */
  renderSlots?: (ctx: {
    detail: WorkspaceDetail;
    reload: () => void;
  }) => Partial<
    Record<
      "export" | "datasets" | "relations" | "semantic" | "brief" | "filters" | "canvas" | "chat",
      ReactNode
    >
  >;
  /** Sesi chat yang sedang aktif di Chat_Panel; dipakai menyorot item terpilih. */
  activeSessionId?: string | null;
  /** Dipanggil saat pengguna memilih sesi chat lama dari panel "Sesi chat". */
  onSelectSession?: (sessionId: string) => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "not_found" }
  | { kind: "error"; message: string }
  | { kind: "ready"; detail: WorkspaceDetail };

/**
 * Kerangka halaman Workspace: memuat dan menampilkan daftar Dataset, Dashboard,
 * dan sesi chat (Req 1.2). Area bertanda `data-slot` adalah tempat komponen
 * dari task berikutnya dipasang; perangkaian akhirnya di task 23.1.
 */
export function WorkspaceView({
  workspaceId,
  client = defaultWorkspaceClient,
  renderSlots,
  activeSessionId,
  onSelectSession,
}: WorkspaceViewProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    client
      .get(workspaceId, { signal: ctrl.signal })
      .then((detail) => setState({ kind: "ready", detail }))
      .catch((err: unknown) => {
        if (isAbortError(err)) return;
        if (isApiError(err) && err.status === 404) setState({ kind: "not_found" });
        else setState({ kind: "error", message: errorMessage(err) });
      });
    return () => ctrl.abort();
  }, [client, workspaceId, reloadKey]);

  const backLink = (
    <nav aria-label="Navigasi">
      <Link href="/" className={styles.backLink}>
        ← Semua Workspace
      </Link>
    </nav>
  );

  if (state.kind !== "ready") {
    return (
      <main className={styles.page}>
        {backLink}
        <h1>Workspace</h1>
        {state.kind === "loading" && (
          <div aria-busy="true" className={styles.section}>
            <span className={styles.muted}>Memuat Workspace…</span>
            <div className={styles.skeleton} />
            <div className={styles.skeleton} />
          </div>
        )}
        {state.kind === "not_found" && (
          <p role="alert">Workspace tidak ditemukan. Mungkin sudah dihapus.</p>
        )}
        {state.kind === "error" && (
          <div role="alert" className={styles.section}>
            <p className={styles.error}>Gagal memuat Workspace: {state.message}</p>
            <div>
              <button
                type="button"
                className={styles.button}
                onClick={() => {
                  setState({ kind: "loading" });
                  setReloadKey((k) => k + 1);
                }}
              >
                Coba lagi
              </button>
            </div>
          </div>
        )}
      </main>
    );
  }

  const { workspace, datasets, dashboards, chat_sessions } = state.detail;

  // Isi slot dari perangkai (task 23.1); `reload` memicu pemuatan ulang detail.
  const slots = renderSlots?.({ detail: state.detail, reload: () => setReloadKey((k) => k + 1) }) ?? {};

  return (
    <main className={styles.studio} data-workspace-id={workspace.id}>
      <header className={styles.studioHeader}>
        <div className={styles.studioTitle}>
          {backLink}
          <h1>{workspace.name}</h1>
          <p className={styles.muted}>Dibuat {formatDateTime(workspace.created_at)}</p>
        </div>
        {/* Slot ekspor PNG/PDF (task 21.10). */}
        <div data-slot="export">{slots.export}</div>
      </header>

      <div className={styles.grid}>
        <aside className={styles.sidebar} aria-label="Panel Workspace">
          {/* Slot upload & detail Dataset (task 21.2) sudah berisi kartunya
              sendiri, jadi dipasang tanpa Panel pembungkus agar tidak dobel. */}
          {slots.datasets !== undefined ? (
            <section aria-label="Dataset" data-slot="datasets" className={styles.slotPlain}>
              {slots.datasets}
            </section>
          ) : (
            <Panel id="datasets" title="Dataset" count={datasets.length}>
              {datasets.length === 0 ? (
                <p className={styles.muted}>Belum ada Dataset. Unggah file CSV atau XLSX.</p>
              ) : (
                <ul className={styles.itemList}>
                  {datasets.map((d) => (
                    <li key={d.id}>
                      <strong>{d.table_name}</strong>{" "}
                      <span className={styles.muted}>
                        {d.source_name}
                        {d.sheet_name ? ` · ${d.sheet_name}` : ""} ·{" "}
                        {d.row_count.toLocaleString("id-ID")} baris · {d.schema.length} kolom
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <div data-slot="datasets" hidden />
            </Panel>
          )}

          {/* Slot daftar relasi candidate/confirmed/rejected (task 21.3). */}
          <div data-slot="relations">{slots.relations}</div>

          {/* Slot Model Semantik (task 33.2). */}
          {slots.semantic !== undefined && <div data-slot="semantic">{slots.semantic}</div>}

          <Panel id="dashboards" title="Dashboard" count={dashboards.length}>
            {dashboards.length === 0 ? (
              <p className={styles.muted}>Belum ada Dashboard.</p>
            ) : (
              <ul className={styles.itemList}>
                {dashboards.map((d) => (
                  <li key={d.id}>
                    <strong>{d.title}</strong>{" "}
                    <span className={styles.muted}>
                      v{d.version} · diperbarui {formatDateTime(d.updated_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel id="chat-sessions" title="Sesi chat" count={chat_sessions.length}>
            {chat_sessions.length === 0 ? (
              <p className={styles.muted}>Belum ada sesi chat.</p>
            ) : (
              <ul className={styles.itemList}>
                {chat_sessions.map((s) => (
                  <li key={s.id}>
                    <button
                      type="button"
                      className={styles.sessionItem}
                      aria-current={s.id === activeSessionId ? "true" : undefined}
                      onClick={() => onSelectSession?.(s.id)}
                    >
                      <strong>{s.title}</strong>{" "}
                      <span className={styles.muted}>{formatDateTime(s.created_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </aside>

        <div className={styles.main}>
          {/* Slot Global_Filter & penanda Cross_Filter (task 21.7). */}
          {/* Slot Design_Brief (task 33.5). */}
          {slots.brief !== undefined && <div data-slot="brief">{slots.brief}</div>}
          <div data-slot="filters">{slots.filters}</div>
          {/* Slot Canvas_Editor (task 21.6). */}
          <section aria-label="Canvas" data-slot="canvas" className={styles.slot}>
            {slots.canvas ?? <p className={styles.muted}>Canvas Dashboard akan tampil di sini.</p>}
          </section>
        </div>

        {/* Slot Chat_Panel (task 21.4): kolom kanan, tetap terlihat saat canvas di-scroll. */}
        <section aria-label="Chat" data-slot="chat" className={styles.chatColumn}>
          {slots.chat ?? <p className={styles.muted}>Panel chat agent akan tampil di sini.</p>}
        </section>
      </div>
    </main>
  );
}

function Panel({
  id,
  title,
  count,
  children,
}: {
  id: string;
  title: string;
  count: number;
  children: ReactNode;
}) {
  const headingId = `ws-panel-${id}`;
  return (
    <section aria-labelledby={headingId} className={styles.panel}>
      <h2 id={headingId} className={styles.panelTitle}>
        {title} <span className={styles.count}>{count}</span>
      </h2>
      {children}
    </section>
  );
}

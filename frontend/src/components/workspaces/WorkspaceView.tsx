"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { isAbortError, isApiError } from "@/lib/api";
import type { WorkspaceDetail } from "@/lib/types";
import { defaultWorkspaceClient, errorMessage, formatDateTime, type WorkspaceClient } from "./client";
import { ChatCircleTextIcon, SidebarSimpleIcon } from "@phosphor-icons/react/ssr";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
      "export" | "datasets" | "relations" | "semantic" | "pages" | "brief" | "filters" | "canvas" | "chat",
      ReactNode
    >
  >;
  /** Sesi chat yang sedang aktif di Chat_Panel; dipakai menyorot item terpilih. */
  activeSessionId?: string | null;
  /** Dipanggil saat pengguna memilih sesi chat lama dari panel "Sesi chat". */
  onSelectSession?: (sessionId: string) => void;
  /** Jumlah usulan yang menunggu review per tab sidebar; tampil sebagai badge. */
  pending?: Partial<Record<"relations" | "semantic", number>>;
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
  pending,
}: WorkspaceViewProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [chatOpen, setChatOpen] = useState(true);

  // Pulihkan pilihan panel dari sesi sebelumnya.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSidebarOpen(window.localStorage.getItem("studio.sidebar") !== "0");
    setChatOpen(window.localStorage.getItem("studio.chat") !== "0");
  }, []);
  const toggle = (key: "sidebar" | "chat") => {
    const set = key === "sidebar" ? setSidebarOpen : setChatOpen;
    const now = key === "sidebar" ? sidebarOpen : chatOpen;
    set(!now);
    window.localStorage.setItem(`studio.${key}`, now ? "0" : "1");
  };

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
            <span className={styles.muted}>Memuat Workspace</span>
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
        <div className={styles.headerActions}>
          <Button variant="ghost" aria-pressed={sidebarOpen} onClick={() => toggle("sidebar")}>
            <SidebarSimpleIcon data-icon="inline-start" weight={sidebarOpen ? "fill" : "regular"} />
            {sidebarOpen ? "Sembunyikan data" : "Tampilkan data"}
          </Button>
          <Button variant="ghost" aria-pressed={chatOpen} onClick={() => toggle("chat")}>
            <ChatCircleTextIcon data-icon="inline-start" weight={chatOpen ? "fill" : "regular"} />
            {chatOpen ? "Sembunyikan chat" : "Tampilkan chat"}
          </Button>
          {/* Slot ekspor PNG/PDF (task 21.10). */}
          <div data-slot="export">{slots.export}</div>
          <ThemeToggle />
        </div>
      </header>

      <div
        className={styles.grid}
        data-sidebar={sidebarOpen ? "open" : "closed"}
        data-chat={chatOpen ? "open" : "closed"}
      >
        <aside className={styles.sidebar} aria-label="Panel Workspace" hidden={!sidebarOpen}>
          <Tabs defaultValue="datasets" className="gap-3">
            <TabsList className="w-full">
              <SidebarTab value="datasets" label="Dataset" />
              <SidebarTab value="relations" label="Relasi" pending={pending?.relations} />
              {slots.semantic !== undefined && (
                <SidebarTab value="semantic" label="Semantik" pending={pending?.semantic} />
              )}
            </TabsList>
            {/* forceMount: panel tetap hidup (state, SSE, hitungan) saat tab lain aktif. */}
            <TabsContent value="datasets" forceMount className="data-[state=inactive]:hidden">
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
            </TabsContent>

            {/* Slot daftar relasi candidate/confirmed/rejected (task 21.3). */}
            <TabsContent value="relations" forceMount className="data-[state=inactive]:hidden">
              <div data-slot="relations">{slots.relations}</div>
            </TabsContent>

            {/* Slot Model Semantik (task 33.2). */}
            {slots.semantic !== undefined && (
              <TabsContent value="semantic" forceMount className="data-[state=inactive]:hidden">
                <div data-slot="semantic">{slots.semantic}</div>
              </TabsContent>
            )}
          </Tabs>

          <Separator />

          {slots.pages !== undefined ? (
            <div data-slot="pages">{slots.pages}</div>
          ) : (
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
          )}

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
        <section aria-label="Chat" data-slot="chat" className={styles.chatColumn} hidden={!chatOpen}>
          {slots.chat ?? <p className={styles.muted}>Panel chat agent akan tampil di sini.</p>}
        </section>
      </div>
    </main>
  );
}

function SidebarTab({ value, label, pending = 0 }: { value: string; label: string; pending?: number }) {
  return (
    <TabsTrigger value={value} aria-label={pending > 0 ? `${label}, ${pending} usulan` : label}>
      {label}
      {pending > 0 && (
        <Badge variant="secondary" className="tabular-nums" aria-hidden>
          {pending}
        </Badge>
      )}
    </TabsTrigger>
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

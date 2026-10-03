"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { isAbortError, isApiError } from "@/lib/api";
import type { WorkspaceDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { defaultWorkspaceClient, errorMessage, formatDateTime, type WorkspaceClient } from "./client";
import { ArrowLeftIcon, ChatCircleTextIcon, SidebarSimpleIcon } from "@phosphor-icons/react/ssr";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

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

const MUTED = "text-[0.8125rem] text-muted-foreground";
const PANEL = "flex min-w-0 flex-col gap-2.5 rounded-lg border bg-card px-4 py-3.5";
const ITEM_LIST = "flex list-none flex-col gap-2 text-sm *:flex *:min-w-0 *:flex-col";

// Kolom grid per kombinasi panel. Tablet (769 sampai 1280px): chat turun ke baris sendiri.
// Desktop (1281px ke atas): tiga kolom setinggi layar, tiap kolom scroll sendiri.
const COLS = {
  both: "min-[769px]:grid-cols-[280px_minmax(0,1fr)] min-[1281px]:grid-cols-[300px_minmax(0,1fr)_380px]",
  sidebar: "min-[769px]:grid-cols-[280px_minmax(0,1fr)] min-[1281px]:grid-cols-[300px_minmax(0,1fr)]",
  chat: "min-[1281px]:grid-cols-[minmax(0,1fr)_380px]",
  none: "",
} as const;
const SCROLL_COL =
  "flex min-w-0 flex-col gap-4 *:shrink-0 min-[1281px]:min-h-0 min-[1281px]:overflow-y-auto min-[1281px]:overscroll-contain min-[1281px]:pr-1 min-[1281px]:[scrollbar-gutter:stable]";

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
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeftIcon aria-hidden />
        Semua Workspace
      </Link>
    </nav>
  );

  if (state.kind !== "ready") {
    return (
      <main className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 px-4 pt-8 pb-12 md:px-6 md:pt-12 md:pb-16">
        {backLink}
        <h1>Workspace</h1>
        {state.kind === "loading" && (
          <div aria-busy="true" className="flex flex-col gap-3">
            <span className={MUTED}>Memuat Workspace</span>
            <Skeleton className="h-13" />
            <Skeleton className="h-13" />
          </div>
        )}
        {state.kind === "not_found" && (
          <Alert>
            <AlertDescription>Workspace tidak ditemukan. Mungkin sudah dihapus.</AlertDescription>
          </Alert>
        )}
        {state.kind === "error" && (
          <Alert variant="destructive">
            <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
              <span>Gagal memuat Workspace: {state.message}</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setState({ kind: "loading" });
                  setReloadKey((k) => k + 1);
                }}
              >
                Coba lagi
              </Button>
            </AlertDescription>
          </Alert>
        )}
      </main>
    );
  }

  const { workspace, datasets, dashboards, chat_sessions } = state.detail;

  // Isi slot dari perangkai (task 23.1); `reload` memicu pemuatan ulang detail.
  const slots = renderSlots?.({ detail: state.detail, reload: () => setReloadKey((k) => k + 1) }) ?? {};
  const cols = COLS[sidebarOpen && chatOpen ? "both" : sidebarOpen ? "sidebar" : chatOpen ? "chat" : "none"];

  return (
    <main
      className="mx-auto flex w-full max-w-[1680px] flex-col gap-4 p-3 md:px-5 md:pt-4 md:pb-8 min-[1281px]:h-dvh min-[1281px]:overflow-hidden min-[1281px]:pb-4"
      data-workspace-id={workspace.id}
    >
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-x-6 gap-y-3 border-b pb-4">
        <div className="flex min-w-0 flex-col gap-1">
          {backLink}
          <h1 className="break-words">{workspace.name}</h1>
          <p className={MUTED}>Dibuat {formatDateTime(workspace.created_at)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
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
        className={cn(
          "grid grid-cols-1 items-start gap-4 min-[1281px]:min-h-0 min-[1281px]:flex-1 min-[1281px]:grid-rows-[minmax(0,1fr)] min-[1281px]:items-stretch",
          cols,
        )}
        data-sidebar={sidebarOpen ? "open" : "closed"}
        data-chat={chatOpen ? "open" : "closed"}
      >
        <aside className={SCROLL_COL} aria-label="Panel Workspace" hidden={!sidebarOpen}>
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
                <section aria-label="Dataset" data-slot="datasets" className="flex min-w-0 flex-col">
                  {slots.datasets}
                </section>
              ) : (
                <Panel id="datasets" title="Dataset" count={datasets.length}>
                  {datasets.length === 0 ? (
                    <p className={MUTED}>Belum ada Dataset. Unggah file CSV atau XLSX.</p>
                  ) : (
                    <ul className={ITEM_LIST}>
                      {datasets.map((d) => (
                        <li key={d.id}>
                          <strong className="font-semibold break-words">{d.table_name}</strong>{" "}
                          <span className={cn(MUTED, "tabular-nums")}>
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
                <p className={MUTED}>Belum ada Dashboard.</p>
              ) : (
                <ul className={ITEM_LIST}>
                  {dashboards.map((d) => (
                    <li key={d.id}>
                      <strong className="font-semibold break-words">{d.title}</strong>{" "}
                      <span className={cn(MUTED, "tabular-nums")}>
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
              <p className={MUTED}>Belum ada sesi chat.</p>
            ) : (
              <ul className={ITEM_LIST}>
                {chat_sessions.map((s) => (
                  <li key={s.id}>
                    <Button
                      variant="ghost"
                      className="h-auto w-full flex-col items-start gap-0.5 border border-transparent px-2 py-1.5 text-left font-normal whitespace-normal aria-[current=true]:border-input aria-[current=true]:bg-muted"
                      aria-current={s.id === activeSessionId ? "true" : undefined}
                      onClick={() => onSelectSession?.(s.id)}
                    >
                      <strong className="font-semibold break-words">{s.title}</strong>{" "}
                      <span className={MUTED}>{formatDateTime(s.created_at)}</span>
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </aside>

        <div className={SCROLL_COL}>
          {/* Slot Global_Filter & penanda Cross_Filter (task 21.7). */}
          {/* Slot Design_Brief (task 33.5). */}
          {slots.brief !== undefined && <div data-slot="brief">{slots.brief}</div>}
          <div data-slot="filters">{slots.filters}</div>
          {/* Slot Canvas_Editor (task 21.6). */}
          <section aria-label="Canvas" data-slot="canvas" className="min-w-0 rounded-lg border bg-card p-4">
            {slots.canvas ?? <p className={MUTED}>Canvas Dashboard akan tampil di sini.</p>}
          </section>
        </div>

        {/* Slot Chat_Panel (task 21.4): kolom kanan, tetap terlihat saat canvas di-scroll. */}
        <section
          aria-label="Chat"
          data-slot="chat"
          className="col-span-full flex h-128 min-h-0 flex-col rounded-lg border bg-card p-4 min-[1281px]:col-auto min-[1281px]:h-auto"
          hidden={!chatOpen}
        >
          {slots.chat ?? <p className={MUTED}>Panel chat agent akan tampil di sini.</p>}
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
    <section aria-labelledby={headingId} className={PANEL}>
      <h2 id={headingId} className="flex items-center gap-2">
        {title}
        <Badge variant="secondary" className="tabular-nums">
          {count}
        </Badge>
      </h2>
      {children}
    </section>
  );
}

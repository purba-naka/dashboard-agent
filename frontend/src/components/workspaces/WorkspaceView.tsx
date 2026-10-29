"use client";

import { groupByDate } from "@/lib/group-by-date";
import { formatNumber } from "@/lib/format-number";
import Link from "next/link";
import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";
import { isAbortError, isApiError } from "@/lib/api";
import type { WorkspaceDetail } from "@/lib/types";
import { cn } from "@/lib/utils";
import { defaultWorkspaceClient, errorMessage, formatDateTime, type WorkspaceClient } from "./client";
import { ArrowLeftIcon, CaretRightIcon, ChatCircleTextIcon, MagnifyingGlassIcon, SidebarSimpleIcon, XIcon } from "@phosphor-icons/react/ssr";
import { IconAction } from "@/components/IconAction";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { CommandMenu } from "@/components/shell/CommandMenu";
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
  /** Dipanggil saat pengguna memilih halaman (Dashboard) dari command palette. */
  onSelectPage?: (dashboardId: string) => void;
  /** Dipanggil saat pengguna memilih "Tanya: <teks>" di command palette. */
  onAskAssistant?: (text: string) => void;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "not_found" }
  | { kind: "error"; message: string }
  | { kind: "ready"; detail: WorkspaceDetail };

const MUTED = "text-[0.8125rem] text-muted-foreground";
const PANEL = "flex min-w-0 flex-col gap-2";
const ITEM_LIST = "flex list-none flex-col gap-2 text-sm *:flex *:min-w-0 *:flex-col";

/**
 * Breakpoint panel. Di bawahnya panel tampil sebagai drawer di atas konten,
 * bukan kolom, supaya canvas tidak terjepit. Sinkron dengan kelas `lg:`/`xl:` di bawah.
 */
const DRAWER_QUERY = { sidebar: "(max-width: 1023px)", chat: "(max-width: 1279px)" } as const;
type PanelKey = keyof typeof DRAWER_QUERY;
/**
 * Lebar panel (px) yang bisa diatur pengguna. `grow`: arah pointer/panah yang melebarkan
 * panel (sidebar di kiri melebar ke kanan, asisten di kanan melebar ke kiri).
 */
const PANEL_W = {
  sidebar: { min: 220, max: 400, initial: 260, step: 16, grow: 1, label: "panel data" },
  chat: { min: 320, max: 560, initial: 380, step: 16, grow: -1, label: "panel asisten" },
} as const;
const clampWidth = (key: PanelKey, w: number) =>
  Math.min(PANEL_W[key].max, Math.max(PANEL_W[key].min, Math.round(w)));
const widthKey = (key: PanelKey) => `studio.${key}Width`;

const isDrawer = (key: PanelKey) =>
  typeof window.matchMedia === "function" && window.matchMedia(DRAWER_QUERY[key]).matches;

/** localStorage bisa melempar (mode privat, kuota, diblokir); preferensi panel tidak kritis. */
const store = {
  get: (k: string) => {
    try {
      return window.localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k: string, v: string) => {
    try {
      window.localStorage.setItem(k, v);
    } catch {
      /* abaikan */
    }
  },
};

const SIDE_PANEL = "relative flex min-h-0 flex-col bg-panel";
// Mode drawer (layar sempit): melayang di atas canvas. Mode kolom: ikut alur flex.
const SIDEBAR_CLASS =
  "fixed inset-y-0 left-0 z-40 w-[min(20rem,88vw)] border-r shadow-(--shadow-lg) lg:relative lg:inset-auto lg:z-auto lg:w-(--sidebar-w) lg:shrink-0 lg:shadow-none";
const CHAT_CLASS =
  "fixed inset-y-0 right-0 z-40 w-[min(26rem,94vw)] border-l shadow-(--shadow-lg) xl:relative xl:inset-auto xl:z-auto xl:w-(--chat-w) xl:shrink-0 xl:shadow-none";
/** Handle hanya tampil saat panel berupa kolom (bukan drawer). */
const HANDLE_CLASS = { sidebar: "-right-1 lg:block", chat: "-left-1 xl:block" } as const;

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
  onSelectPage,
  onAskAssistant,
}: WorkspaceViewProps) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [chatOpen, setChatOpen] = useState(true);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [widths, setWidths] = useState<Record<PanelKey, number>>({
    sidebar: PANEL_W.sidebar.initial,
    chat: PANEL_W.chat.initial,
  });
  const widthsRef = useRef(widths);

  // Pulihkan pilihan panel dari sesi sebelumnya. Layar sempit mulai dengan drawer tertutup.
  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    setSidebarOpen(!isDrawer("sidebar") && store.get("studio.sidebar") !== "0");
    setChatOpen(!isDrawer("chat") && store.get("studio.chat") !== "0");
    const saved = { ...widthsRef.current };
    for (const key of ["sidebar", "chat"] as const) {
      const w = Number(store.get(widthKey(key)));
      if (w) saved[key] = clampWidth(key, w);
    }
    setWidths((widthsRef.current = saved));
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);
  const setOpen = (key: PanelKey, open: boolean) => {
    (key === "sidebar" ? setSidebarOpen : setChatOpen)(open);
    // Drawer di layar sempit bersifat sementara; hanya pilihan layar lebar yang diingat.
    if (!isDrawer(key)) store.set(`studio.${key}`, open ? "1" : "0");
  };
  const toggle = (key: PanelKey) => setOpen(key, !(key === "sidebar" ? sidebarOpen : chatOpen));
  /** Esc menutup panel hanya saat tampil sebagai drawer. */
  const closeDrawerOnEsc = (key: PanelKey) => (e: KeyboardEvent) => {
    if (e.key === "Escape" && isDrawer(key)) setOpen(key, false);
  };

  // Ctrl/⌘+K membuka palette; diabaikan saat fokus di kolom teks agar tidak bentrok dengan input.
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key.toLowerCase() !== "k" || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])")) return;
      e.preventDefault();
      setPaletteOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const applyWidth = (key: PanelKey, w: number, persist: boolean) => {
    widthsRef.current = { ...widthsRef.current, [key]: clampWidth(key, w) };
    setWidths(widthsRef.current);
    if (persist) store.set(widthKey(key), String(widthsRef.current[key]));
  };
  const startResize = (key: PanelKey) => (e: PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = widthsRef.current[key];
    const move = (ev: globalThis.PointerEvent) =>
      applyWidth(key, startW + (ev.clientX - startX) * PANEL_W[key].grow, false);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.removeProperty("cursor");
      applyWidth(key, widthsRef.current[key], true);
    };
    document.body.style.cursor = "col-resize";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const resizeByKey = (key: PanelKey) => (e: KeyboardEvent) => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    applyWidth(key, widthsRef.current[key] + dir * PANEL_W[key].grow * PANEL_W[key].step, true);
  };
  const resizeHandle = (key: PanelKey) => (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`Ubah lebar ${PANEL_W[key].label}`}
      aria-valuemin={PANEL_W[key].min}
      aria-valuemax={PANEL_W[key].max}
      aria-valuenow={widths[key]}
      tabIndex={0}
      className={cn(
        "absolute inset-y-0 z-10 hidden w-2 cursor-col-resize outline-none after:absolute after:inset-y-0 after:left-1/2 after:w-px after:-translate-x-1/2 after:transition-colors hover:after:bg-primary focus-visible:after:w-0.5 focus-visible:after:bg-ring",
        HANDLE_CLASS[key],
      )}
      onPointerDown={startResize(key)}
      onKeyDown={resizeByKey(key)}
      onDoubleClick={() => applyWidth(key, PANEL_W[key].initial, true)}
    />
  );

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

  return (
    <TooltipProvider delayDuration={300}>
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden" data-workspace-id={workspace.id}>
      <header className="flex h-12 shrink-0 items-center gap-1.5 border-b bg-panel px-2 sm:px-3">
        <IconAction
          label={sidebarOpen ? "Sembunyikan panel data" : "Tampilkan panel data"}
          aria-pressed={sidebarOpen}
          aria-controls="workspace-sidebar"
          onClick={() => toggle("sidebar")}
        >
          <SidebarSimpleIcon weight={sidebarOpen ? "fill" : "regular"} />
        </IconAction>
        <nav aria-label="Navigasi" className="flex min-w-0 flex-1 items-center gap-1 text-sm">
          <Link
            href="/"
            aria-label="Semua Workspace"
            className="hidden shrink-0 rounded-sm px-1 text-muted-foreground hover:text-foreground sm:inline"
          >
            Workspace
          </Link>
          <CaretRightIcon aria-hidden className="hidden size-3 shrink-0 text-muted-foreground sm:block" />
          <h1
            className="min-w-0 truncate text-sm font-semibold"
            title={`${workspace.name} · dibuat ${formatDateTime(workspace.created_at)}`}
          >
            {workspace.name}
          </h1>
        </nav>
        {/* Slot ekspor PNG/PDF (task 21.10). */}
        <div data-slot="export" className="flex items-center">
          {slots.export}
        </div>
        <Button
          variant="outline"
          className="shrink-0 text-muted-foreground"
          aria-label="Cari atau jalankan perintah (Ctrl+K)"
          aria-keyshortcuts="Control+K Meta+K"
          onClick={() => setPaletteOpen(true)}
        >
          <MagnifyingGlassIcon data-icon="inline-start" />
          <span className="hidden md:inline">Cari atau jalankan perintah…</span>
          <kbd className="ml-2 hidden rounded-sm border bg-surface-2 px-1 font-sans text-xs md:inline">⌘K</kbd>
        </Button>
        <ThemeToggle />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant={chatOpen ? "secondary" : "ghost"}
              aria-pressed={chatOpen}
              aria-controls="workspace-chat"
              aria-label={chatOpen ? "Sembunyikan asisten" : "Tampilkan asisten"}
              onClick={() => toggle("chat")}
            >
              <ChatCircleTextIcon data-icon="inline-start" weight={chatOpen ? "fill" : "regular"} />
              <span className="hidden sm:inline">Asisten</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>{chatOpen ? "Sembunyikan asisten" : "Tampilkan asisten"}</TooltipContent>
        </Tooltip>
      </header>

      <div
        className="relative flex min-h-0 flex-1"
        data-sidebar={sidebarOpen ? "open" : "closed"}
        data-chat={chatOpen ? "open" : "closed"}
      >
        {/* Latar drawer layar sempit; klik menutup panel. */}
        {sidebarOpen && (
          <div aria-hidden className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setOpen("sidebar", false)} />
        )}
        {chatOpen && (
          <div aria-hidden className="fixed inset-0 z-30 bg-black/40 xl:hidden" onClick={() => setOpen("chat", false)} />
        )}

        <aside
          id="workspace-sidebar"
          className={cn(SIDE_PANEL, SIDEBAR_CLASS)}
          style={{ "--sidebar-w": `${widths.sidebar}px` } as CSSProperties}
          aria-label="Panel Workspace"
          hidden={!sidebarOpen}
          onKeyDown={closeDrawerOnEsc("sidebar")}
        >
          {resizeHandle("sidebar")}
          <div className="flex h-12 shrink-0 items-center justify-between border-b px-3 lg:hidden">
            <span className="text-sm font-semibold">Data & halaman</span>
            <IconAction label="Tutup panel data" onClick={() => setOpen("sidebar", false)}>
              <XIcon />
            </IconAction>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto overscroll-contain px-3 py-3 [scrollbar-gutter:stable] *:shrink-0">
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
                            <strong className="truncate font-semibold" title={d.table_name}>
                              {d.table_name}
                            </strong>{" "}
                            <span className={cn(MUTED, "tabular-nums")}>
                              {d.source_name}
                              {d.sheet_name ? ` · ${d.sheet_name}` : ""} ·{" "}
                              {formatNumber(d.row_count)} baris · {d.schema.length} kolom
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
                        <strong className="truncate font-semibold" title={d.title}>
                          {d.title}
                        </strong>{" "}
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
                // Riwayat panjang scroll di dalam daftar, bukan memanjangkan sidebar.
                <div className="-mx-1 flex max-h-72 flex-col gap-2 overflow-y-auto overscroll-contain px-1">
                  {groupByDate(chat_sessions).map(({ group, items }) => (
                    <div key={group} className="flex flex-col gap-0.5">
                      <h4 className="section-label px-2">{group}</h4>
                      <ul className="flex list-none flex-col gap-0.5">
                        {items.map((s) => (
                          <li key={s.id} className="min-w-0">
                      <button
                        type="button"
                        className="flex w-full min-w-0 flex-col items-start rounded-md border-l-2 border-transparent px-2 py-1.5 text-left text-sm transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=true]:border-l-primary aria-[current=true]:bg-muted"
                        aria-current={s.id === activeSessionId ? "true" : undefined}
                        title={s.title}
                        onClick={() => {
                          onSelectSession?.(s.id);
                          if (isDrawer("sidebar")) setOpen("sidebar", false);
                        }}
                      >
                        <span className="w-full truncate font-medium">{s.title}</span>
                        <span className="text-xs text-muted-foreground tabular-nums">{formatDateTime(s.created_at)}</span>
                      </button>
                    </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto overscroll-contain [scrollbar-gutter:stable]">
          <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4 px-4 py-4 sm:px-6 sm:py-6">
            {/* Global_Filter + Cross_Filter (task 21.7): menempel di atas saat canvas di-scroll. */}
            <div data-slot="filters" className="sticky top-0 z-10 -mx-4 -mt-4 border-b bg-background/95 px-4 backdrop-blur-sm sm:-mx-6 sm:-mt-6 sm:px-6">
              {slots.filters}
            </div>
            {/* Slot Design_Brief (task 33.5). */}
            {slots.brief !== undefined && <div data-slot="brief">{slots.brief}</div>}
            {/* Slot Canvas_Editor (task 21.6). Item canvas sudah berbentuk kartu; tanpa bingkai ganda. */}
            <section aria-label="Canvas" data-slot="canvas" className="min-w-0">
              {slots.canvas ?? <p className={MUTED}>Canvas Dashboard akan tampil di sini.</p>}
            </section>
          </div>
        </main>

        {/* Slot Chat_Panel (task 21.4): kolom kanan yang bisa diubah lebarnya; drawer di layar sempit. */}
        <section
          id="workspace-chat"
          aria-label="Chat"
          data-slot="chat"
          className={cn(SIDE_PANEL, CHAT_CLASS)}
          style={{ "--chat-w": `${widths.chat}px` } as CSSProperties}
          hidden={!chatOpen}
          onKeyDown={closeDrawerOnEsc("chat")}
        >
          {resizeHandle("chat")}
          <div className="flex h-12 shrink-0 items-center justify-between gap-2 border-b pr-2 pl-4">
            <h2 className="text-sm font-semibold">Asisten data</h2>
            <IconAction label="Tutup asisten" onClick={() => setOpen("chat", false)}>
              <XIcon />
            </IconAction>
          </div>
          <div className="flex min-h-0 flex-1 flex-col px-4 pt-3 pb-4">
            {slots.chat ?? <p className={MUTED}>Panel chat agent akan tampil di sini.</p>}
          </div>
        </section>
      </div>
      <CommandMenu
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        pages={dashboards}
        onSelectPage={onSelectPage}
        onToggleSidebar={() => toggle("sidebar")}
        onToggleChat={() => toggle("chat")}
        sidebarOpen={sidebarOpen}
        chatOpen={chatOpen}
        onAskAssistant={onAskAssistant}
      />
    </div>
    </TooltipProvider>
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
      <h2 id={headingId} className="section-label flex items-center gap-2">
        {title}
        <span className="font-normal tabular-nums">{count}</span>
      </h2>
      {children}
    </section>
  );
}

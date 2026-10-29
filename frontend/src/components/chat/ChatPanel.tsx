"use client";

import {
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useReducer,
  useRef,
  useState,
  type FormEvent,
  type Ref,
} from "react";
import { ArrowDownIcon, CaretDownIcon, CheckIcon, ClockCounterClockwiseIcon, PlusIcon, ChatCircleDotsIcon, PaperPlaneRightIcon, StopIcon } from "@phosphor-icons/react/ssr";
import { formatCardinality, formatOverlap, relationTitle } from "@/components/relations/client";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { isAbortError } from "@/lib/api";
import { toast } from "sonner";
import { focusTile } from "@/lib/focus-tile";
import { groupByDate } from "@/lib/group-by-date";
import type { ChatSession, Dataset, PatchEvent, Relation } from "@/lib/types";
import { suggestQuestions } from "./suggestions";
import {
  agentLabel,
  chatReducer,
  defaultChatClient,
  errorMessage,
  initialChatState,
  type ChatClient,
  type ChatEntry,
} from "./client";
import { BlueprintCard } from "./BlueprintCard";
import { CANDIDATE_ROW, ChatCard, DecisionActions } from "./ChatCard";
import { Markdown } from "./Markdown";
import { ReviewCard } from "./ReviewCard";
import { SemanticDraftCard } from "./SemanticDraftCard";

/** Jarak dari dasar log yang masih dianggap "di bawah". */
const SCROLL_STICK_PX = 48;

/** Tracked new messages when user scrolled away. */
interface NewMessageState {
  count: number;
  lastEntryId: number;
}

export interface ChatPanelHandle {
  /** Kirim pesan secara programatik (mis. setelah `job.done` upload). */
  send(message: string): Promise<void>;
}

export interface ChatPanelProps {
  workspaceId: string;
  /** Sesi yang dilanjutkan; riwayatnya dimuat saat mount/berubah. */
  sessionId?: string | null;
  client?: ChatClient;
  /** Dataset Workspace: sumber contoh pertanyaan pada tampilan kosong. */
  datasets?: readonly Dataset[];
  /** Setiap `patch.applied` dari stream chat. */
  onPatch?: (patch: PatchEvent) => void;
  /** Relasi dikonfirmasi/ditolak dari kartu kandidat. */
  onRelationsChanged?: () => void;
  /** Sesi baru dibuat backend (`run.started`). */
  onSessionChange?: (sessionId: string) => void;
  /** Halaman aktif; dikirim tiap giliran agar agent mengedit halaman yang sama dengan UI. */
  dashboardId?: string | null;
  /** Mulai percakapan baru (parent mengosongkan `sessionId`). */
  onNewSession?: () => void;
  /** Sesi tersimpan untuk dropdown riwayat. */
  sessions?: readonly ChatSession[];
  /** Pilih sesi dari dropdown. */
  onSelectSession?: (id: string) => void;
  ref?: Ref<ChatPanelHandle>;
}

export function ChatPanel({
  workspaceId,
  sessionId = null,
  client = defaultChatClient,
  datasets = [],
  onPatch,
  onRelationsChanged,
  onSessionChange,
  dashboardId = null,
  onNewSession,
  sessions = [],
  onSelectSession,
  ref,
}: ChatPanelProps) {
  const [state, dispatch] = useReducer(chatReducer, sessionId, initialChatState);
  const [busyRelation, setBusyRelation] = useState<string | null>(null);
  const [newMessages, setNewMessages] = useState<NewMessageState>({ count: 0, lastEntryId: -1 });
  const stateRef = useRef(state);
  const callbacks = useRef({ onPatch, onRelationsChanged, onSessionChange, dashboardId });
  useEffect(() => {
    stateRef.current = state;
    callbacks.current = { onPatch, onRelationsChanged, onSessionChange, dashboardId };
  });
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLOListElement>(null);
  /** Sesi yang dibuat oleh run panel ini sendiri. */
  const ownSessionRef = useRef<string | null>(null);
  /** Detected tile additions from patches. */
  const [tileAdded, setTileAdded] = useState<{ id: string; timestamp: number } | null>(null);

  // Auto-hide tile action card after 6 seconds
  useEffect(() => {
    if (!tileAdded) return;
    const timeout = setTimeout(() => setTileAdded(null), 6000);
    return () => clearTimeout(timeout);
  }, [tileAdded]);

  useEffect(() => {
    // Sesi yang baru dibuat run ini (`run.started`) sudah ada di state; memuat
    // riwayat akan menimpa pesan yang sedang berjalan.
    if (!sessionId || sessionId === ownSessionRef.current) return;
    ownSessionRef.current = null;
    const ctrl = new AbortController();
    client
      .history(workspaceId, sessionId)
      .then((entries) => {
        if (!ctrl.signal.aborted) dispatch({ type: "history", sessionId, entries });
      })
      .catch((err) => {
        if (!ctrl.signal.aborted) dispatch({ type: "failed", message: `Riwayat gagal dimuat: ${errorMessage(err)}` });
      });
    return () => ctrl.abort();
  }, [client, workspaceId, sessionId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Parent mengosongkan sesi ("Percakapan baru"): bersihkan panel. Mount awal dilewati.
  const hadSessionRef = useRef(sessionId !== null);
  useEffect(() => {
    if (sessionId) {
      hadSessionRef.current = true;
      return;
    }
    if (!hadSessionRef.current || stateRef.current.running) return;
    hadSessionRef.current = false;
    ownSessionRef.current = null;
    dispatch({ type: "reset" });
  }, [sessionId]);

  /** Ikuti stream hanya bila pengguna sudah di dasar log (tidak sedang membaca ke atas). */
  const stickRef = useRef(true);
  useEffect(() => {
    const el = logRef.current;
    if (el && stickRef.current) {
      el.scrollTop = el.scrollHeight;
      // Reset new message indicator when auto-scrolling
      setNewMessages({ count: 0, lastEntryId: state.entries[state.entries.length - 1]?.id ?? -1 });
    } else if (!stickRef.current && state.entries.length > 0) {
      // Track new messages when not at bottom
      const lastId = state.entries[state.entries.length - 1].id;
      setNewMessages((prev) => {
        if (lastId === prev.lastEntryId) return prev;
        return { count: prev.count + 1, lastEntryId: lastId };
      });
    }
  }, [state.entries]);

  const send = useCallback(
    async (message: string, proposalId?: string, selectedSlotIds?: string[]) => {
      const text = message.trim();
      if (!text || stateRef.current.running) return;
      stickRef.current = true;
      dispatch({ type: "send", text });
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const sid = stateRef.current.sessionId;
      const page = callbacks.current.dashboardId;
      try {
        await client.send(
          workspaceId,
          {
            message: text,
            ...(sid ? { session_id: sid } : {}),
            ...(page ? { dashboard_id: page } : {}),
            ...(proposalId
              ? {
                  approval: {
                    proposal_id: proposalId,
                    ...(selectedSlotIds ? { selected_slot_ids: selectedSlotIds } : {}),
                  },
                }
              : {}),
          },
          {
            onEvent: (event) => {
              dispatch({ type: "event", event });
              if (event.event === "patch.applied") {
                callbacks.current.onPatch?.(event.data);
                const added = event.data.ops.find((o) => o.op === "add_item");
                if (added) setTileAdded({ id: added.item.id, timestamp: Date.now() });
              }
              if (event.event === "run.started" && event.data.session_id !== sid) {
                ownSessionRef.current = event.data.session_id;
                callbacks.current.onSessionChange?.(event.data.session_id);
              }
            },
          },
          { signal: ctrl.signal },
        );
        dispatch({ type: "ended" });
      } catch (err) {
        if (isAbortError(err)) dispatch({ type: "ended" });
        else dispatch({ type: "failed", message: errorMessage(err) });
      } finally {
        if (abortRef.current === ctrl) abortRef.current = null;
      }
    },
    [client, workspaceId],
  );

  useImperativeHandle(ref, () => ({ send: (message) => send(message) }), [send]);

  const onSend = useCallback((text: string) => void send(text), [send]);

  const stop = useCallback(async () => {
    const runId = stateRef.current.runId;
    if (!runId) return;
    try {
      await client.stop(workspaceId, runId);
    } catch (err) {
      dispatch({ type: "failed", message: `Gagal menghentikan: ${errorMessage(err)}` });
    }
  }, [client, workspaceId]);

  const decideRelation = async (relation: Relation, confirm: boolean) => {
    setBusyRelation(relation.id);
    try {
      const updated = await (confirm ? client.confirmRelation : client.rejectRelation)(workspaceId, relation.id);
      dispatch({ type: "relation", relation: updated });
      callbacks.current.onRelationsChanged?.();
    } catch (err) {
      dispatch({ type: "failed", message: errorMessage(err) });
    } finally {
      setBusyRelation(null);
    }
  };

  const approve = (proposalId: string, selectedSlotIds?: string[]) => {
    dispatch({ type: "approved", proposalId, ...(selectedSlotIds ? { selectedSlotIds } : {}) });
    const message = selectedSlotIds
      ? `Setujui rancangan (${selectedSlotIds.length} slot)`
      : "Setujui";
    void send(message, proposalId, selectedSlotIds);
  };

  const decideSemantic = async (cardId: number, entryId: string, confirm: boolean) => {
    const fn = confirm ? client.confirmSemantic : client.rejectSemantic;
    if (!fn) return;
    try {
      await fn(workspaceId, entryId);
      dispatch({ type: "semanticDecided", entryId: cardId, decisions: { [entryId]: confirm ? "confirmed" : "rejected" } });
    } catch (err) {
      dispatch({ type: "failed", message: errorMessage(err) });
    }
  };

  const confirmAllSemantic = async (entry: Extract<ChatEntry, { kind: "semantic" }>) => {
    if (!client.confirmAllSemantic) return;
    try {
      await client.confirmAllSemantic(workspaceId);
      const decisions: Record<string, "confirmed"> = {};
      const current = (id: string, status: string): string =>
        (entry.decided[id] as string | undefined) ?? status;
      for (const m of entry.data.metrics) if (current(m.id, m.status) === "candidate") decisions[m.id] = "confirmed";
      for (const c of entry.data.columns_highlight)
        if (current(c.id, c.status) === "candidate") decisions[c.id] = "confirmed";
      dispatch({ type: "semanticDecided", entryId: entry.id, decisions });
    } catch (err) {
      dispatch({ type: "failed", message: errorMessage(err) });
    }
  };

  const renderEntry = (entry: ChatEntry) => {
    switch (entry.kind) {
      case "user":
        return (
          <p className="ml-auto w-fit max-w-[88%] rounded-lg rounded-br-sm bg-surface-2 px-3 py-2 text-sm whitespace-pre-wrap text-foreground [overflow-wrap:anywhere]">
            {entry.text}
          </p>
        );
      case "agent":
        return (
          <div className="flex min-w-0 flex-col gap-1.5 border-l-2 border-primary/40 pl-3 text-sm">
            <span className="section-label normal-case tracking-normal">{agentLabel(entry.agent)}</span>
            {entry.thought && (
              <details
                className="border-l-2 border-input pl-2.5 text-[0.8125rem] text-muted-foreground"
                open={!entry.text || undefined}
              >
                <summary className="w-fit cursor-pointer rounded-sm font-medium select-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none">
                  {entry.text ? "Proses berpikir" : "Sedang berpikir"}
                </summary>
                <p className="mt-1.5 max-h-64 overflow-y-auto leading-normal whitespace-pre-wrap [overflow-wrap:anywhere]">
                  {entry.thought}
                </p>
              </details>
            )}
            {entry.text && <Markdown text={entry.text} />}
          </div>
        );
      case "profile":
        return (
          <ChatCard label="Ringkasan profil dataset" title="Ringkasan profil">
            <Markdown text={entry.summary} />
          </ChatCard>
        );
      case "relations":
        return (
          <ChatCard label="Kandidat relasi" title="Kandidat relasi">
            <ul className="flex flex-col gap-2">
              {entry.relations.map((r) => {
                const title = relationTitle(r);
                return (
                  <li key={r.id} className={CANDIDATE_ROW}>
                    <span className="min-w-0">
                      <strong>{title}</strong>{" "}
                      <span className="text-muted-foreground tabular-nums">
                        · {formatCardinality(r.cardinality)} · overlap {formatOverlap(r.overlap_pct)}
                      </span>
                    </span>
                    <DecisionActions
                      status={r.status}
                      label={title}
                      disabled={busyRelation === r.id}
                      onDecide={(confirm) => decideRelation(r, confirm)}
                    />
                  </li>
                );
              })}
            </ul>
          </ChatCard>
        );
      case "semantic":
        return (
          <SemanticDraftCard
            data={entry.data}
            decided={entry.decided}
            disabled={state.running}
            onDecide={(entryId, confirm) => decideSemantic(entry.id, entryId, confirm)}
            onConfirmAll={() => confirmAllSemantic(entry)}
            onCorrect={(message) => void send(message)}
          />
        );
      case "review":
        return <ReviewCard findings={entry.findings} disabled={state.running} onApply={(m) => void send(m)} />;
      case "approval":
        if (entry.blueprint) {
          return (
            <BlueprintCard
              summary={entry.summary}
              blueprint={entry.blueprint}
              approved={entry.approved}
              selectedSlotIds={entry.selectedSlotIds}
              progress={entry.progress}
              disabled={state.running}
              onApprove={(selected) => approve(entry.proposalId, selected)}
              onRevise={(message) => void send(message)}
            />
          );
        }
        return (
          <ChatCard
            label="Permintaan persetujuan"
            title="Usulan perubahan Dashboard"
            footer={
              entry.approved ? (
                <span className="text-muted-foreground">Disetujui</span>
              ) : (
                <Button size="sm" disabled={state.running} onClick={() => approve(entry.proposalId)}>
                  Setujui
                </Button>
              )
            }
          >
            <Markdown text={entry.summary} />
            {entry.themes.length > 0 && (
              <ul className="flex list-disc flex-col gap-1 pl-5">
                {entry.themes.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            )}
          </ChatCard>
        );
      case "error":
        return (
          <Alert variant="destructive">
            <AlertDescription>
              {entry.agent ? `${agentLabel(entry.agent)}: ` : ""}
              {entry.message}
            </AlertDescription>
          </Alert>
        );
      case "notice":
        return <p className="text-sm text-muted-foreground">{entry.text}</p>;
    }
  };

  const { running, activeAgent, runningTool } = state;

  const scrollToBottom = useCallback(() => {
    const el = logRef.current;
    if (el) {
      stickRef.current = true;
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      setNewMessages({ count: 0, lastEntryId: state.entries[state.entries.length - 1]?.id ?? -1 });
    }
  }, [state.entries]);

  return (
    <section className="flex h-full min-h-0 flex-1 flex-col gap-3" aria-label="Chat">
      <header className="flex shrink-0 items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Asisten data</h2>
        <div className="flex items-center gap-1">
          {onSelectSession && sessions.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="ghost" title="Riwayat percakapan" disabled={running}>
                  <ClockCounterClockwiseIcon data-icon="inline-start" />
                  Riwayat
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="max-h-80 w-64 overflow-y-auto">
                {groupByDate(sessions).map(({ group, items }) => (
                  <DropdownMenuGroup key={group}>
                    <DropdownMenuLabel>{group}</DropdownMenuLabel>
                    {items.map((s) => (
                      <DropdownMenuItem key={s.id} onSelect={() => onSelectSession(s.id)}>
                        <span className="min-w-0 flex-1 truncate">{s.title || "Percakapan"}</span>
                        {s.id === state.sessionId && <CheckIcon aria-label="Sesi aktif" weight="bold" />}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuGroup>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {onNewSession && (
            <Button size="sm" variant="ghost" title="Percakapan baru" disabled={running} onClick={onNewSession}>
              <PlusIcon data-icon="inline-start" />
              Percakapan baru
            </Button>
          )}
        </div>
      </header>
      <ol
        className="-mr-2 flex min-h-0 flex-1 list-none flex-col gap-4 overflow-y-auto overscroll-contain pr-2 [scrollbar-gutter:stable]"
        ref={logRef}
        aria-live="polite"
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_STICK_PX;
          if (stickRef.current) setNewMessages((p) => (p.count ? { ...p, count: 0 } : p));
        }}
      >
        {state.entries.length === 0 && (
          <li className="flex flex-1 items-center justify-center">
            <Empty className="max-w-md p-0 text-center">
              <EmptyHeader className="items-center text-center">
                <EmptyMedia variant="icon">
                  <ChatCircleDotsIcon />
                </EmptyMedia>
                <EmptyTitle>Tanya tentang data Anda</EmptyTitle>
                <EmptyDescription className="text-center">
                  {datasets.length
                    ? "Asisten menjawab dari dataset di workspace ini."
                    : "Unggah dataset, lalu mulai bertanya."}
                </EmptyDescription>
              </EmptyHeader>
              {datasets.length > 0 && (
                <EmptyContent className="max-w-none items-center gap-2">
                  {suggestQuestions(datasets).map((q) => (
                    <button
                      key={q}
                      type="button"
                      className="rounded-full border border-border bg-surface px-4 py-2 text-sm transition-colors hover:border-border-strong hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
                      disabled={running}
                      onClick={() => onSend(q)}
                    >
                      {q}
                    </button>
                  ))}
                </EmptyContent>
              )}
            </Empty>
          </li>
        )}
        {state.entries.map((e) => (
          <li key={e.id}>{renderEntry(e)}</li>
        ))}
      </ol>

      {newMessages.count > 0 && (
        <button
          type="button"
          className="self-center rounded-full border border-border bg-surface px-3 py-1.5 text-sm shadow-sm transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={scrollToBottom}
        >
          <ArrowDownIcon data-icon="inline-start" weight="bold" />
          {newMessages.count === 1 ? "Pesan baru" : `${newMessages.count} pesan baru`}
        </button>
      )}

      {tileAdded && (
        <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface p-3 text-sm shadow-sm">
          <span>
            <strong>Tile ditambahkan</strong>
            <span className="ml-2 text-muted-foreground">·</span>
          </span>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              if (!focusTile(tileAdded.id)) toast.info("Tile ada di halaman lain atau belum tampil.");
              setTileAdded(null);
            }}
          >
            Lihat
          </Button>
        </div>
      )}

      {running && runningTool && (
        <details className="group rounded-lg border border-border bg-surface text-sm">
          <summary
            role="status"
            className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
          >
            <span aria-hidden className="size-1.5 shrink-0 animate-pulse rounded-full bg-primary" />
            <span className="min-w-0 flex-1">
              {activeAgent ? `Agent ${agentLabel(activeAgent)} aktif` : "Memproses"} · menjalankan {runningTool.tool}
            </span>
            {runningTool.argsSummary && (
              <CaretDownIcon aria-hidden className="size-4 transition-transform group-open:rotate-180" weight="bold" />
            )}
          </summary>
          {runningTool.argsSummary && (
            <div className="border-t border-border px-3 py-2 text-muted-foreground">
              {runningTool.argsSummary}
            </div>
          )}
        </details>
      )}
      {running && !runningTool && (
        <div
          className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm"
          role="status"
        >
          <span aria-hidden className="size-1.5 shrink-0 animate-pulse rounded-full bg-primary" />
          {activeAgent ? `Agent ${agentLabel(activeAgent)} aktif` : "Memproses"}
        </div>
      )}

      <Composer running={running} canStop={Boolean(state.runId)} onSend={onSend} onStop={stop} />
    </section>
  );
}

/**
 * Input chat dengan state `draft` sendiri: mengetik hanya merender komponen ini,
 * bukan seluruh riwayat chat.
 */
const Composer = memo(function Composer({
  running,
  canStop,
  onSend,
  onStop,
}: {
  running: boolean;
  canStop: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const [draft, setDraft] = useState("");
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    // Saat run berjalan `send` menolak pesan; jangan buang draft pengguna.
    if (running || !draft.trim()) return;
    onSend(draft);
    setDraft("");
  };
  return (
    <form
      className="flex shrink-0 flex-col gap-2 rounded-lg border bg-background p-2 focus-within:border-ring"
      onSubmit={onSubmit}
    >
      <Label className="sr-only" htmlFor="chat-input">
        Pesan
      </Label>
      <Textarea
        id="chat-input"
        className="max-h-48 min-h-11 resize-none border-0 bg-transparent px-1 py-1 shadow-none focus-visible:ring-0 dark:bg-transparent"
        rows={2}
        value={draft}
        placeholder="Tanyakan sesuatu tentang data Anda"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            e.currentTarget.form?.requestSubmit();
          }
        }}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">Enter kirim · Shift+Enter baris baru</span>
        {running ? (
          <Button type="button" size="icon-sm" variant="outline" title="Stop" aria-label="Stop" onClick={onStop} disabled={!canStop}>
            <StopIcon weight="fill" />
          </Button>
        ) : (
          <Button type="submit" size="icon-sm" title="Kirim" aria-label="Kirim" disabled={!draft.trim()}>
            <PaperPlaneRightIcon />
          </Button>
        )}
      </div>
    </form>
  );
});

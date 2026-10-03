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
import { ChatCircleDotsIcon, PaperPlaneRightIcon, StopIcon } from "@phosphor-icons/react/ssr";
import { formatCardinality, formatOverlap, relationTitle } from "@/components/relations/client";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { isAbortError } from "@/lib/api";
import type { Dataset, PatchEvent, Relation } from "@/lib/types";
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
  ref,
}: ChatPanelProps) {
  const [state, dispatch] = useReducer(chatReducer, sessionId, initialChatState);
  const [busyRelation, setBusyRelation] = useState<string | null>(null);
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

  /** Ikuti stream hanya bila pengguna sudah di dasar log (tidak sedang membaca ke atas). */
  const stickRef = useRef(true);
  useEffect(() => {
    const el = logRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
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
              if (event.event === "patch.applied") callbacks.current.onPatch?.(event.data);
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
          <p className="ml-auto w-fit max-w-[85%] rounded-lg rounded-br-sm bg-secondary px-3 py-2 whitespace-pre-wrap text-secondary-foreground [overflow-wrap:anywhere]">
            {entry.text}
          </p>
        );
      case "agent":
        return (
          <div className="flex max-w-[92%] flex-col gap-1">
            <span className="text-xs font-semibold text-muted-foreground">{agentLabel(entry.agent)}</span>
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

  return (
    <section className="flex h-full min-h-0 flex-1 flex-col gap-3" aria-label="Chat">
      <ol
        className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto overscroll-contain pr-1"
        ref={logRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_STICK_PX;
        }}
      >
        {state.entries.length === 0 && (
          <li className="flex flex-1">
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <ChatCircleDotsIcon />
                </EmptyMedia>
                <EmptyTitle>Tanya tentang data Anda</EmptyTitle>
                <EmptyDescription>
                  {datasets.length ? "Coba tanyakan:" : "Unggah dataset, lalu mulai bertanya."}
                </EmptyDescription>
              </EmptyHeader>
              {datasets.length > 0 && (
                <EmptyContent className="items-stretch">
                  {suggestQuestions(datasets).map((q) => (
                    <Button
                      key={q}
                      variant="outline"
                      className="h-auto justify-start py-2 text-left whitespace-normal"
                      disabled={running}
                      onClick={() => onSend(q)}
                    >
                      {q}
                    </Button>
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

      {running && (
        <div className="rounded-md bg-muted px-2.5 py-1.5 text-[0.8125rem] text-muted-foreground" role="status">
          {activeAgent ? `Agent ${agentLabel(activeAgent)} aktif` : "Memproses"}
          {runningTool && ` · menjalankan ${runningTool.tool}`}
          {runningTool?.argsSummary && <span> ({runningTool.argsSummary})</span>}
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
    <form className="flex items-end gap-2 border-t pt-3" onSubmit={onSubmit}>
      <Label className="sr-only" htmlFor="chat-input">
        Pesan
      </Label>
      <Textarea
        id="chat-input"
        className="max-h-40 min-h-11 flex-1 resize-y"
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
      {running ? (
        <Button type="button" variant="outline" onClick={onStop} disabled={!canStop}>
          <StopIcon data-icon="inline-start" weight="fill" />
          Stop
        </Button>
      ) : (
        <Button type="submit" disabled={!draft.trim()}>
          <PaperPlaneRightIcon data-icon="inline-start" />
          Kirim
        </Button>
      )}
    </form>
  );
});

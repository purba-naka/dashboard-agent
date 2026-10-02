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
import { formatCardinality, formatOverlap, relationTitle } from "@/components/relations/client";
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
import { Markdown } from "./Markdown";
import { ReviewCard } from "./ReviewCard";
import { SemanticDraftCard } from "./SemanticDraftCard";
import styles from "./chat.module.css";

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
  ref,
}: ChatPanelProps) {
  const [state, dispatch] = useReducer(chatReducer, sessionId, initialChatState);
  const [busyRelation, setBusyRelation] = useState<string | null>(null);
  const stateRef = useRef(state);
  const callbacks = useRef({ onPatch, onRelationsChanged, onSessionChange });
  useEffect(() => {
    stateRef.current = state;
    callbacks.current = { onPatch, onRelationsChanged, onSessionChange };
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
      try {
        await client.send(
          workspaceId,
          {
            message: text,
            ...(sid ? { session_id: sid } : {}),
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
        return <p className={styles.user}>{entry.text}</p>;
      case "agent":
        return (
          <div className={styles.agent}>
            <span className={styles.author}>{agentLabel(entry.agent)}</span>
            {entry.thought && (
              <details className={styles.thought} open={!entry.text || undefined}>
                <summary>{entry.text ? "Proses berpikir" : "Sedang berpikir…"}</summary>
                <p className={styles.thoughtText}>{entry.thought}</p>
              </details>
            )}
            {entry.text && <Markdown text={entry.text} />}
          </div>
        );
      case "profile":
        return (
          <section className={styles.card} aria-label="Ringkasan profil dataset">
            <h3 className={styles.cardTitle}>Ringkasan profil</h3>
            <Markdown text={entry.summary} />
          </section>
        );
      case "relations":
        return (
          <section className={styles.card} aria-label="Kandidat relasi">
            <h3 className={styles.cardTitle}>Kandidat relasi</h3>
            <ul className={styles.plain}>
              {entry.relations.map((r) => {
                const title = relationTitle(r);
                return (
                  <li key={r.id} className={styles.relation}>
                    <span>
                      <strong>{title}</strong> · {formatCardinality(r.cardinality)} · overlap {formatOverlap(r.overlap_pct)}
                    </span>
                    {r.status === "candidate" ? (
                      <span className={styles.actions}>
                        <button
                          type="button"
                          disabled={busyRelation === r.id}
                          aria-label={`Konfirmasi ${title}`}
                          onClick={() => decideRelation(r, true)}
                        >
                          Konfirmasi
                        </button>
                        <button
                          type="button"
                          disabled={busyRelation === r.id}
                          aria-label={`Tolak ${title}`}
                          onClick={() => decideRelation(r, false)}
                        >
                          Tolak
                        </button>
                      </span>
                    ) : (
                      <span className={styles.muted}>{r.status === "confirmed" ? "Dikonfirmasi" : "Ditolak"}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
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
          <section className={styles.card} aria-label="Permintaan persetujuan">
            <h3 className={styles.cardTitle}>Usulan perubahan Dashboard</h3>
            <Markdown text={entry.summary} />
            {entry.themes.length > 0 && (
              <ul>
                {entry.themes.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            )}
            {entry.approved ? (
              <span className={styles.muted}>Disetujui</span>
            ) : (
              <button type="button" disabled={state.running} onClick={() => approve(entry.proposalId)}>
                Setujui
              </button>
            )}
          </section>
        );
      case "error":
        return (
          <p className={styles.error} role="alert">
            {entry.agent ? `${agentLabel(entry.agent)}: ` : ""}
            {entry.message}
          </p>
        );
      case "notice":
        return <p className={styles.muted}>{entry.text}</p>;
    }
  };

  const { running, activeAgent, runningTool } = state;

  return (
    <section className={styles.panel} aria-label="Chat">
      <ol
        className={styles.log}
        ref={logRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_STICK_PX;
        }}
      >
        {state.entries.length === 0 && (
          <li className={styles.empty}>
            <p className={styles.muted}>
              {datasets.length ? "Coba tanyakan:" : "Unggah dataset, lalu mulai bertanya."}
            </p>
            {suggestQuestions(datasets).map((q) => (
              <button key={q} type="button" className={styles.suggestion} disabled={running} onClick={() => onSend(q)}>
                {q}
              </button>
            ))}
          </li>
        )}
        {state.entries.map((e) => (
          <li key={e.id}>{renderEntry(e)}</li>
        ))}
      </ol>

      {running && (
        <div className={styles.status} role="status">
          {activeAgent ? `Agent ${agentLabel(activeAgent)} aktif` : "Memproses…"}
          {runningTool && ` · menjalankan ${runningTool.tool}`}
          {runningTool?.argsSummary && <span className={styles.muted}> ({runningTool.argsSummary})</span>}
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
    <form className={styles.form} onSubmit={onSubmit}>
      <label className={styles.srOnly} htmlFor="chat-input">
        Pesan
      </label>
      <textarea
        id="chat-input"
        className={styles.input}
        rows={2}
        value={draft}
        placeholder="Tanyakan sesuatu tentang data Anda…"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            e.currentTarget.form?.requestSubmit();
          }
        }}
      />
      {running ? (
        <button type="button" onClick={onStop} disabled={!canStop}>
          Stop
        </button>
      ) : (
        <button type="submit" disabled={!draft.trim()}>
          Kirim
        </button>
      )}
    </form>
  );
});

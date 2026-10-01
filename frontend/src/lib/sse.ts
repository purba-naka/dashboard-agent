/**
 * Klien Server-Sent Events (design.md "REST & SSE API contracts", Req 16.1,
 * 16.3, 16.5).
 *
 * - {@link createSseParser}: parser SSE inkremental sesuai spesifikasi HTML
 *   (WHATWG "server-sent events"): menerima potongan string sembarang (boleh
 *   terpotong di tengah baris / di antara `\r\n`), akhir baris `\r\n`, `\n`,
 *   atau `\r`, BOM di awal stream, beberapa baris `data:` digabung dengan
 *   `\n`, komentar (`: ping`, `: connected`) diabaikan, field `id`/`retry`.
 * - {@link decodeSseMessage}: decode JSON `data` menjadi event bertipe.
 *   Tipe event yang TIDAK dikenal tidak diteruskan ke `onEvent`; ia
 *   dilaporkan ke handler opsional `onUnhandled` (reason `unknown_event`)
 *   lalu diabaikan. `data` yang bukan JSON valid diperlakukan sama (reason
 *   `invalid_json`). Bentuk `data` tidak divalidasi lebih jauh (dipercaya
 *   dari backend).
 * - {@link connectWorkspaceEvents}: `EventSource` `GET /workspaces/{ws}/events`
 *   dengan reconnect otomatis + pelacakan `Last-Event-ID`.
 * - {@link streamChat}: `POST /workspaces/{ws}/chat` via `fetch` +
 *   `ReadableStream`.
 *
 * SSR-safe: tidak ada akses `EventSource`/`fetch`/`TextDecoder` saat import;
 * semuanya dirujuk ketika fungsi dipanggil.
 */

import { ApiError, chatUrl, isAbortError, parseApiError, workspaceEventsUrl } from "./api";
import type {
  ChatRequest,
  ChatSseEvent,
  ChatSseEventType,
  SseEnvelope,
  WorkspaceSseEvent,
  WorkspaceSseEventType,
} from "./types";

// ---------------------------------------------------------------------------
// Parser SSE inkremental
// ---------------------------------------------------------------------------

/** Satu event SSE mentah hasil parser (sebelum decode JSON). */
export interface SseMessage {
  /** Nama event (`event:`); `"message"` bila tidak ada. */
  event: string;
  /** Gabungan baris `data:` (dipisah `\n`, tanpa `\n` akhir). */
  data: string;
  /**
   * Buffer last-event-id saat event di-dispatch. Sesuai spesifikasi nilainya
   * persisten antar event (event tanpa `id:` mewarisi id sebelumnya); `""`
   * bila belum pernah ada.
   */
  lastEventId: string;
  /** `true` bila blok event ini sendiri membawa field `id:`. */
  hasId: boolean;
}

export interface SseParserOptions {
  /** Dipanggil untuk field `retry:` yang valid (milidetik). */
  onRetry?: (ms: number) => void;
  /** Dipanggil untuk baris komentar (teks setelah `:`, spasi awal dibuang). */
  onComment?: (text: string) => void;
}

export interface SseParser {
  /** Masukkan potongan teks berikutnya dari stream. */
  feed(chunk: string): void;
  /**
   * Tandai akhir stream. Baris/event yang belum diakhiri baris kosong
   * dibuang (sesuai spesifikasi). Parser dapat dipakai ulang untuk stream
   * berikutnya; `lastEventId` dipertahankan.
   */
  end(): void;
  /** Buffer last-event-id saat ini (`""` bila belum ada). */
  readonly lastEventId: string;
  /** Nilai `retry:` terakhir (ms) atau `null`. */
  readonly retry: number | null;
}

const LF = 10;
const CR = 13;
const COLON = 58;
const SPACE = 32;
const BOM = 0xfeff;

/** Buat parser SSE inkremental; `onEvent` dipanggil per event yang lengkap. */
export function createSseParser(
  onEvent: (message: SseMessage) => void,
  options: SseParserOptions = {},
): SseParser {
  // State per stream.
  let lineBuffer = "";
  let skipLeadingLF = false; // chunk sebelumnya berakhir dengan `\r`
  let atStreamStart = true;
  let eventType = "";
  let dataBuffer = "";
  let blockHasId = false;
  // State persisten.
  let lastEventId = "";
  let retry: number | null = null;

  const dispatch = () => {
    const hasId = blockHasId;
    blockHasId = false;
    if (dataBuffer === "") {
      eventType = "";
      return;
    }
    const data = dataBuffer.endsWith("\n") ? dataBuffer.slice(0, -1) : dataBuffer;
    const message: SseMessage = {
      event: eventType || "message",
      data,
      lastEventId,
      hasId,
    };
    eventType = "";
    dataBuffer = "";
    onEvent(message);
  };

  const processLine = (line: string) => {
    if (line === "") {
      dispatch();
      return;
    }
    if (line.charCodeAt(0) === COLON) {
      if (options.onComment) {
        const text = line.slice(1);
        options.onComment(text.charCodeAt(0) === SPACE ? text.slice(1) : text);
      }
      return;
    }
    const colon = line.indexOf(":");
    let field: string;
    let value: string;
    if (colon === -1) {
      field = line;
      value = "";
    } else {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.charCodeAt(0) === SPACE) value = value.slice(1);
    }
    switch (field) {
      case "event":
        eventType = value;
        break;
      case "data":
        dataBuffer += `${value}\n`;
        break;
      case "id":
        if (!value.includes("\u0000")) {
          lastEventId = value;
          blockHasId = true;
        }
        break;
      case "retry":
        if (/^[0-9]+$/.test(value)) {
          retry = Number(value);
          options.onRetry?.(retry);
        }
        break;
      default:
        // Field tidak dikenal diabaikan (spesifikasi).
        break;
    }
  };

  return {
    feed(chunk: string) {
      if (!chunk) return;
      let text = chunk;
      if (atStreamStart) {
        atStreamStart = false;
        if (text.charCodeAt(0) === BOM) text = text.slice(1);
      }
      let start = 0;
      let i = 0;
      if (skipLeadingLF) {
        skipLeadingLF = false;
        if (text.charCodeAt(0) === LF) {
          start = 1;
          i = 1;
        }
      }
      for (; i < text.length; i++) {
        const c = text.charCodeAt(i);
        if (c !== LF && c !== CR) continue;
        const line = lineBuffer + text.slice(start, i);
        lineBuffer = "";
        if (c === CR) {
          if (i + 1 < text.length) {
            if (text.charCodeAt(i + 1) === LF) i++;
          } else {
            // `\n` pasangan `\r\n` mungkin datang di chunk berikutnya.
            skipLeadingLF = true;
          }
        }
        start = i + 1;
        processLine(line);
      }
      if (start < text.length) lineBuffer += text.slice(start);
    },
    end() {
      lineBuffer = "";
      skipLeadingLF = false;
      atStreamStart = true;
      eventType = "";
      dataBuffer = "";
      blockHasId = false;
    },
    get lastEventId() {
      return lastEventId;
    },
    get retry() {
      return retry;
    },
  };
}

// ---------------------------------------------------------------------------
// Decode event bertipe
// ---------------------------------------------------------------------------

// Record → kompilasi gagal bila union di types.ts bertambah tanpa diperbarui.
const CHAT_EVENT_TYPE_MAP: Record<ChatSseEventType, true> = {
  "run.started": true,
  "agent.active": true,
  "text.delta": true,
  "thought.delta": true,
  "tool.call": true,
  "tool.result": true,
  "patch.applied": true,
  "approval.request": true,
  "relation.candidates": true,
  "profile.summary": true,
  "semantic.draft": true,
  "blueprint.progress": true,
  "review.findings": true,
  error: true,
  "run.stopped": true,
  "run.done": true,
};

const WORKSPACE_EVENT_TYPE_MAP: Record<WorkspaceSseEventType, true> = {
  "patch.applied": true,
  "job.progress": true,
  "job.done": true,
  "job.failed": true,
  "dataset.profiled": true,
  "relation.updated": true,
  "semantic.updated": true,
  "semantic.warning": true,
  resync: true,
};

export const CHAT_EVENT_TYPES = Object.keys(CHAT_EVENT_TYPE_MAP) as readonly ChatSseEventType[];
export const WORKSPACE_EVENT_TYPES = Object.keys(
  WORKSPACE_EVENT_TYPE_MAP,
) as readonly WorkspaceSseEventType[];

const CHAT_EVENT_TYPE_SET: ReadonlySet<string> = new Set(CHAT_EVENT_TYPES);
const WORKSPACE_EVENT_TYPE_SET: ReadonlySet<string> = new Set(WORKSPACE_EVENT_TYPES);

/** Event yang tidak diteruskan ke `onEvent`. */
export interface SseDecodeFailure {
  reason: "unknown_event" | "invalid_json";
  message: SseMessage;
  error?: unknown;
}

export type SseDecodeResult<E extends { event: string; data: unknown }> =
  | { ok: true; event: SseEnvelope<E> }
  | ({ ok: false } & SseDecodeFailure);

/**
 * Decode `message.data` (JSON) menjadi event bertipe bila `message.event`
 * termasuk `knownTypes`. `id` envelope diisi hanya bila blok event membawa
 * `id:` sendiri.
 */
export function decodeSseMessage<E extends { event: string; data: unknown }>(
  message: SseMessage,
  knownTypes: ReadonlySet<string>,
): SseDecodeResult<E> {
  if (!knownTypes.has(message.event)) {
    return { ok: false, reason: "unknown_event", message };
  }
  let data: unknown;
  try {
    data = JSON.parse(message.data);
  } catch (error) {
    return { ok: false, reason: "invalid_json", message, error };
  }
  const envelope: { event: string; data: unknown; id?: string } = { event: message.event, data };
  if (message.hasId && message.lastEventId !== "") envelope.id = message.lastEventId;
  return { ok: true, event: envelope as SseEnvelope<E> };
}

/** Decode event stream chat (lihat {@link decodeSseMessage}). */
export function decodeChatMessage(message: SseMessage): SseDecodeResult<ChatSseEvent> {
  return decodeSseMessage<ChatSseEvent>(message, CHAT_EVENT_TYPE_SET);
}

/** Decode event stream workspace (lihat {@link decodeSseMessage}). */
export function decodeWorkspaceMessage(message: SseMessage): SseDecodeResult<WorkspaceSseEvent> {
  return decodeSseMessage<WorkspaceSseEvent>(message, WORKSPACE_EVENT_TYPE_SET);
}

// ---------------------------------------------------------------------------
// Workspace events (EventSource)
// ---------------------------------------------------------------------------

export type WorkspaceConnectionStatus = "connecting" | "open" | "reconnecting" | "closed";

export type ResyncEventData = Extract<WorkspaceSseEvent, { event: "resync" }>["data"];

export interface WorkspaceEventHandlers {
  /** Semua event workspace yang dikenal (termasuk `resync`). */
  onEvent?: (event: SseEnvelope<WorkspaceSseEvent>) => void;
  /**
   * Server tidak dapat me-replay semua event setelah `Last-Event-ID`
   * (mis. backend restart): refetch state via REST.
   */
  onResync?: (data: ResyncEventData) => void;
  /** Koneksi terbuka; `reconnected` bila sebelumnya pernah terbuka. */
  onOpen?: (info: { reconnected: boolean; lastEventId: string | null }) => void;
  /**
   * Koneksi pulih setelah terputus (reconnect otomatis browser atau manual).
   * Pemanggil sebaiknya menyelaraskan Dashboard terbuka lewat
   * `GET /dashboards/{id}/patches?since_version=` (Req 16.5), mis.
   * `dispatch({type: "resyncRequested"})` lalu `resyncDashboard(...)` dari
   * `dashboard-state.ts`, dan menerapkan patch berurutan.
   */
  onReconnect?: (info: { lastEventId: string | null }) => void;
  /** Error koneksi; `retryInMs` `null` bila browser reconnect sendiri. */
  onError?: (info: { event: Event; retryInMs: number | null }) => void;
  onStatusChange?: (status: WorkspaceConnectionStatus) => void;
  /** Event tak dikenal / JSON rusak (diabaikan). */
  onUnhandled?: (failure: SseDecodeFailure) => void;
}

/** Subset `EventSource` yang dipakai (memudahkan test). */
export interface EventSourceLike {
  readonly readyState: number;
  onopen: ((ev: Event) => unknown) | null;
  onerror: ((ev: Event) => unknown) | null;
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
  close(): void;
}

export interface WorkspaceEventsOptions {
  /** Kursor awal (`?last_event_id=`) untuk replay pada koneksi pertama. */
  lastEventId?: string | null;
  /** Backoff reconnect manual awal (default 1.000 ms, dikali 2 tiap gagal). */
  initialBackoffMs?: number;
  /** Batas backoff reconnect manual (default 30.000 ms). */
  maxBackoffMs?: number;
  /** Default `new EventSource(url)`. */
  eventSourceFactory?: (url: string) => EventSourceLike;
}

/**
 * Handle langganan: dapat dipanggil langsung (`unsubscribe()`) atau via
 * `.close()`.
 */
export interface WorkspaceEventsSubscription {
  (): void;
  close(): void;
  /** Id event terakhir yang diterima (kursor `Last-Event-ID`). */
  readonly lastEventId: string | null;
  readonly status: WorkspaceConnectionStatus;
}

const EVENT_SOURCE_CLOSED = 2;

/**
 * Berlangganan event Workspace.
 *
 * Reconnect: saat koneksi putus dengan `readyState` CONNECTING, browser
 * reconnect sendiri dan mengirim header `Last-Event-ID`. Bila `readyState`
 * CLOSED (HTTP error, content-type salah, server mati), koneksi baru dibuat
 * dengan backoff eksponensial memakai `?last_event_id=<id terakhir>`.
 * Setiap pemulihan memanggil `onReconnect`; event `resync` dari server
 * memanggil `onResync`. Tanpa `EventSource` (SSR) langganan menjadi no-op
 * berstatus `closed`.
 */
export function connectWorkspaceEvents(
  ws: string,
  handlers: WorkspaceEventHandlers = {},
  options: WorkspaceEventsOptions = {},
): WorkspaceEventsSubscription {
  const initialBackoff = Math.max(0, options.initialBackoffMs ?? 1_000);
  const maxBackoff = Math.max(initialBackoff, options.maxBackoffMs ?? 30_000);
  const factory: ((url: string) => EventSourceLike) | null =
    options.eventSourceFactory ??
    (typeof EventSource !== "undefined" ? (url: string) => new EventSource(url) : null);

  let lastEventId: string | null = options.lastEventId || null;
  let status: WorkspaceConnectionStatus = "connecting";
  let source: EventSourceLike | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let everOpened = false;
  let closed = false;

  const setStatus = (next: WorkspaceConnectionStatus) => {
    if (status === next) return;
    status = next;
    handlers.onStatusChange?.(next);
  };

  const onMessage = (owner: EventSourceLike, ev: MessageEvent) => {
    if (closed || source !== owner) return;
    if (ev.lastEventId) lastEventId = ev.lastEventId;
    const decoded = decodeWorkspaceMessage({
      event: ev.type,
      data: typeof ev.data === "string" ? ev.data : String(ev.data),
      lastEventId: ev.lastEventId ?? "",
      // `MessageEvent` tidak membedakan id milik blok ini dari id warisan.
      hasId: Boolean(ev.lastEventId) && ev.type !== "resync",
    });
    if (!decoded.ok) {
      handlers.onUnhandled?.(decoded);
      return;
    }
    const event = decoded.event;
    handlers.onEvent?.(event);
    if (event.event === "resync") handlers.onResync?.(event.data);
  };

  const scheduleReconnect = (): number => {
    setStatus("reconnecting");
    const delay = Math.min(maxBackoff, initialBackoff * 2 ** attempt);
    attempt += 1;
    timer = setTimeout(connect, delay);
    return delay;
  };

  function connect() {
    timer = null;
    if (closed || !factory) return;
    let es: EventSourceLike;
    try {
      es = factory(workspaceEventsUrl(ws, lastEventId));
    } catch {
      scheduleReconnect();
      return;
    }
    source = es;

    es.onopen = () => {
      if (closed || source !== es) return;
      const reconnected = everOpened;
      everOpened = true;
      attempt = 0;
      setStatus("open");
      handlers.onOpen?.({ reconnected, lastEventId });
      if (reconnected) handlers.onReconnect?.({ lastEventId });
    };

    es.onerror = (event: Event) => {
      if (closed || source !== es) return;
      if (es.readyState === EVENT_SOURCE_CLOSED) {
        es.close();
        source = null;
        const retryInMs = scheduleReconnect();
        handlers.onError?.({ event, retryInMs });
      } else {
        // CONNECTING: browser reconnect otomatis dengan header Last-Event-ID.
        setStatus("reconnecting");
        handlers.onError?.({ event, retryInMs: null });
      }
    };

    const listener = (ev: MessageEvent) => onMessage(es, ev);
    for (const type of WORKSPACE_EVENT_TYPES) es.addEventListener(type, listener);
    // Event tanpa `event:` → tipe "message" → dilaporkan sebagai unknown.
    es.addEventListener("message", listener);
  }

  const close = () => {
    if (closed) return;
    closed = true;
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (source) {
      source.onopen = null;
      source.onerror = null;
      source.close();
      source = null;
    }
    setStatus("closed");
  };

  if (factory) {
    connect();
  } else {
    closed = true;
    setStatus("closed");
  }

  const subscription = Object.assign(() => close(), { close });
  Object.defineProperties(subscription, {
    lastEventId: { get: () => lastEventId, enumerable: true },
    status: { get: () => status, enumerable: true },
  });
  return subscription as unknown as WorkspaceEventsSubscription;
}

// ---------------------------------------------------------------------------
// Chat stream (fetch + ReadableStream)
// ---------------------------------------------------------------------------

export interface ChatStreamHandlers {
  /** Setiap event chat yang dikenal, berurutan. */
  onEvent?: (event: SseEnvelope<ChatSseEvent>) => void;
  /** Event tak dikenal / JSON rusak (diabaikan). */
  onUnhandled?: (failure: SseDecodeFailure) => void;
}

export interface ChatStreamOptions {
  signal?: AbortSignal;
  /** Default `globalThis.fetch`. */
  fetch?: typeof fetch;
}

/**
 * `done` → `run.done`; `stopped` → `run.stopped`; `ended` → stream berakhir
 * tanpa keduanya (mis. koneksi ditutup server).
 */
export type ChatStreamOutcome = "done" | "stopped" | "ended";

export interface ChatStreamResult {
  runId: string | null;
  sessionId: string | null;
  outcome: ChatStreamOutcome;
}

function chatNetworkError(cause: unknown): ApiError {
  return new ApiError(0, "NETWORK_ERROR", "Tidak dapat terhubung ke server.", {
    cause: cause instanceof Error ? cause.message : String(cause),
  });
}

function chatAbortError(signal: AbortSignal | undefined, cause: unknown): unknown {
  if (isAbortError(cause)) return cause;
  if (signal && isAbortError(signal.reason)) return signal.reason;
  return new DOMException("Permintaan dibatalkan.", "AbortError");
}

/**
 * Kirim pesan chat dan baca stream SSE-nya. Resolve saat stream berakhir.
 *
 * - HTTP non-2xx → {@link ApiError} (envelope via `parseApiError`).
 * - Kegagalan jaringan (termasuk putus di tengah stream) → `ApiError`
 *   status 0 `NETWORK_ERROR`.
 * - Pembatalan `signal` → error `AbortError` (cek `isAbortError`). Untuk
 *   menghentikan run agent gunakan `stopChatRun`; stream lalu mengirim
 *   `run.stopped` dan resolve dengan `outcome: "stopped"`.
 * - Exception dari handler menghentikan pembacaan dan diteruskan apa adanya.
 */
export async function streamChat(
  ws: string,
  request: ChatRequest,
  handlers: ChatStreamHandlers = {},
  options: ChatStreamOptions = {},
): Promise<ChatStreamResult> {
  const { signal } = options;
  const fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);

  let res: Response;
  try {
    res = await fetchImpl(chatUrl(ws), {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
      body: JSON.stringify(request),
      cache: "no-store",
      signal,
    });
  } catch (err) {
    if (isAbortError(err) || signal?.aborted) throw chatAbortError(signal, err);
    throw chatNetworkError(err);
  }

  if (!res.ok) {
    let text = "";
    try {
      text = await res.text();
    } catch (err) {
      if (isAbortError(err) || signal?.aborted) throw chatAbortError(signal, err);
    }
    throw parseApiError(res.status, text, res.statusText);
  }

  const result: ChatStreamResult = { runId: null, sessionId: null, outcome: "ended" };
  const parser = createSseParser((message) => {
    const decoded = decodeChatMessage(message);
    if (!decoded.ok) {
      handlers.onUnhandled?.(decoded);
      return;
    }
    const event = decoded.event;
    if (event.event === "run.started") {
      const data = event.data as { run_id?: unknown; session_id?: unknown } | null;
      if (typeof data?.run_id === "string") result.runId = data.run_id;
      if (typeof data?.session_id === "string") result.sessionId = data.session_id;
    } else if (event.event === "run.done") {
      result.outcome = "done";
    } else if (event.event === "run.stopped") {
      result.outcome = "stopped";
    }
    handlers.onEvent?.(event);
  });

  const body = res.body;
  if (!body) {
    // Lingkungan tanpa streaming body: proses seluruh teks sekaligus.
    let text: string;
    try {
      text = await res.text();
    } catch (err) {
      if (isAbortError(err) || signal?.aborted) throw chatAbortError(signal, err);
      throw chatNetworkError(err);
    }
    parser.feed(text);
    parser.end();
    return result;
  }

  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let finished = false;
  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err) {
        finished = true; // stream sudah errored; cancel tidak diperlukan
        if (isAbortError(err) || signal?.aborted) throw chatAbortError(signal, err);
        throw chatNetworkError(err);
      }
      if (chunk.done) {
        finished = true;
        break;
      }
      parser.feed(decoder.decode(chunk.value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.end();
    return result;
  } finally {
    if (!finished) reader.cancel().catch(() => undefined);
  }
}

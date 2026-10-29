/**
 * `toolProvider` runtime OpenUI untuk canvas (D7).
 *
 * Semua panggilan `Query(...)` dalam satu tick dikumpulkan menjadi satu
 * `POST /dashboards/{id}/canvas/render` beserta Cross_Filter aktif (Global_Filter
 * dibaca backend dari Dashboard). Hasil per Query dikembalikan apa adanya
 * (`status`, `rows`/`kpi`, `error`) agar tile menampilkan status sendiri.
 *
 * Renderer membuat QueryManager baru setiap identitas `toolProvider` berubah,
 * jadi host cukup membuat provider baru saat filter/versi/data berubah untuk
 * memaksa refetch semua Query.
 */
import type { CanvasQueryRequest, CanvasQueryResult, CanvasRenderResponse, Predicate } from "@/lib/types";
import { TOOL_NAMES } from "./schema";

export type CanvasRenderFn = (
  dashboardId: string,
  body: { queries: CanvasQueryRequest[]; cross_filters: Predicate[] },
) => Promise<CanvasRenderResponse>;

export type ToolMap = Record<string, (args: Record<string, unknown>) => Promise<unknown>>;

interface Pending {
  req: CanvasQueryRequest;
  resolve: (value: CanvasQueryResult) => void;
  reject: (reason: unknown) => void;
}

export function errorResult(code: string, message: string): CanvasQueryResult {
  return { status: "error", filter_unaffected: false, error: { code, message, details: {} } };
}

export function createCanvasToolProvider(options: {
  dashboardId: string;
  crossFilters: Predicate[];
  render: CanvasRenderFn;
  /** Dipanggil setiap batch selesai (mis. untuk menghitung request di test). */
  onBatch?: (size: number) => void;
}): ToolMap {
  let queue: Pending[] = [];
  let scheduled = false;
  let seq = 0;

  const flush = async () => {
    scheduled = false;
    const batch = queue;
    queue = [];
    if (batch.length === 0) return;
    options.onBatch?.(batch.length);
    try {
      const res = await options.render(options.dashboardId, {
        queries: batch.map((p) => p.req),
        cross_filters: options.crossFilters,
      });
      for (const p of batch) {
        p.resolve(res.results[p.req.key] ?? errorResult("NOT_FOUND", "Hasil query tidak ada."));
      }
    } catch (err) {
      for (const p of batch) p.reject(err);
    }
  };

  const call = (tool: string) => (args: Record<string, unknown>) =>
    new Promise<CanvasQueryResult>((resolve, reject) => {
      seq += 1;
      queue.push({ req: { key: `k${seq}`, tool, args: args ?? {} }, resolve, reject });
      if (!scheduled) {
        scheduled = true;
        // setTimeout(0) (bukan microtask): QueryManager memanggil tool per Query
        // dalam satu effect; satu macrotask cukup untuk mengumpulkan semuanya.
        setTimeout(() => void flush(), 0);
      }
    });

  return Object.fromEntries(TOOL_NAMES.map((name) => [name, call(name)]));
}

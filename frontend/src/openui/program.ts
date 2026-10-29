/**
 * Utilitas program canvas OpenUI Lang di Frontend (cermin
 * `backend/studio/core/openui/program.py`).
 *
 * Statement disimpan backend per nama dengan props bernama (rujukan =
 * `{ $ref: nama }`). Edit manual (D8) menulis ulang statement secara
 * deterministik lewat `serializeStatement` lalu dikirim sebagai patch
 * `apply_canvas_patch` — jalur tulis yang sama dengan agent.
 */
import type { CanvasStatement } from "@/lib/types";
import { baseLibrary } from "./schema";

export const ROOT = "root";
export const LAYOUT_COMPONENTS = new Set(["VContainer", "HContainer", "Section", "Tabs"]);

export type Ref = { $ref: string };

export function isRef(value: unknown): value is Ref {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length === 1 &&
    typeof (value as Ref).$ref === "string"
  );
}

/** Teks program; `root` selalu di depan. */
export function programText(statements: Readonly<Record<string, CanvasStatement>>): string {
  const all = Object.values(statements);
  return [...all.filter((s) => s.name === ROOT), ...all.filter((s) => s.name !== ROOT)]
    .map((s) => s.source)
    .join("\n");
}

const schema = baseLibrary.toJSONSchema();

/** Urutan argumen posisi komponen (urutan properti skema). */
export function paramOrder(component: string): string[] {
  return Object.keys(schema.$defs?.[component]?.properties ?? {});
}

const KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Nilai JSON (props sidecar) → teks OpenUI Lang. */
export function toLang(value: unknown): string {
  if (isRef(value)) return value.$ref;
  if (value === null || value === undefined) return "null";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(toLang).join(", ")}]`;
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if (typeof obj.$component === "string") {
      return callText(obj.$component, (obj.props as Record<string, unknown>) ?? {});
    }
    const parts = Object.entries(obj)
      .filter(([, v]) => v !== undefined && v !== null)
      .map(([k, v]) => `${KEY.test(k) ? k : JSON.stringify(k)}: ${toLang(v)}`);
    return `{${parts.join(", ")}}`;
  }
  throw new TypeError(`Nilai tidak bisa diserialisasi: ${typeof value}`);
}

function callText(component: string, props: Readonly<Record<string, unknown>>): string {
  const args = paramOrder(component).map((k) => props[k]);
  while (args.length > 0 && (args[args.length - 1] === undefined || args[args.length - 1] === null)) args.pop();
  return `${component}(${args.map(toLang).join(", ")})`;
}

export function serializeStatement(
  name: string,
  component: string,
  props: Readonly<Record<string, unknown>>,
): string {
  return `${name} = ${callText(component, props)}`;
}

/** Nama statement anak (rujukan) sebuah container. */
export function childNames(stmt: CanvasStatement): string[] {
  const children = stmt.props.children;
  return Array.isArray(children) ? children.filter(isRef).map((c) => c.$ref) : [];
}

/** Container yang merujuk `name` sebagai anak langsung. */
export function parentOf(
  statements: Readonly<Record<string, CanvasStatement>>,
  name: string,
): CanvasStatement | null {
  for (const stmt of Object.values(statements)) {
    if (stmt.component && LAYOUT_COMPONENTS.has(stmt.component) && childNames(stmt).includes(name)) {
      return stmt;
    }
  }
  return null;
}

/** Statement Query yang menjadi `data` sebuah komponen. */
export function sourceQuery(
  statements: Readonly<Record<string, CanvasStatement>>,
  stmt: CanvasStatement | undefined,
): CanvasStatement | null {
  const data = stmt?.props.data;
  if (!isRef(data)) return null;
  const q = statements[data.$ref];
  return q && q.kind === "query" ? q : null;
}

/** Judul tile (prop `title`) atau nama statement. */
export function statementTitle(statements: Readonly<Record<string, CanvasStatement>>, name: string): string {
  const t = statements[name]?.props.title;
  return typeof t === "string" && t ? t : name;
}

export function queryArgs(stmt: CanvasStatement | null | undefined): Record<string, unknown> {
  const args = stmt?.query?.args;
  return typeof args === "object" && args !== null && !Array.isArray(args) ? (args as Record<string, unknown>) : {};
}

// ---------------------------------------------------------------------------
// Edit deterministik (D8): setiap fungsi mengembalikan teks patch atau null
// ---------------------------------------------------------------------------

function withChildren(parent: CanvasStatement, children: Ref[], weights?: number[]): string {
  const props: Record<string, unknown> = { ...parent.props, children };
  if (parent.component === "HContainer") props.weights = weights;
  if (parent.component === "Tabs") {
    const labels = Array.isArray(parent.props.labels) ? (parent.props.labels as string[]) : [];
    const before = childNames(parent);
    props.labels = children.map((c) => labels[before.indexOf(c.$ref)] ?? c.$ref);
  }
  return serializeStatement(parent.name, parent.component as string, props);
}

function weightsOf(parent: CanvasStatement): number[] {
  const n = childNames(parent).length;
  const w = parent.props.weights;
  return Array.isArray(w) && w.length === n && w.every((x) => typeof x === "number" && x > 0)
    ? (w as number[])
    : Array.from({ length: n }, () => 1);
}

/** Hapus tile dari induknya (statement yatim dibuang merge). */
export function removePatch(
  statements: Readonly<Record<string, CanvasStatement>>,
  name: string,
): string | null {
  const parent = parentOf(statements, name);
  if (!parent) return null;
  const names = childNames(parent);
  const idx = names.indexOf(name);
  const weights = weightsOf(parent).filter((_, i) => i !== idx);
  return withChildren(
    parent,
    names.filter((n) => n !== name).map(($ref) => ({ $ref })),
    parent.component === "HContainer" ? weights : undefined,
  );
}

/** Geser tile `delta` posisi di dalam induknya (kiri/atas = -1). */
export function movePatch(
  statements: Readonly<Record<string, CanvasStatement>>,
  name: string,
  delta: number,
): string | null {
  const parent = parentOf(statements, name);
  if (!parent) return null;
  const names = childNames(parent);
  const from = names.indexOf(name);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= names.length) return null;
  const weights = weightsOf(parent);
  const swap = <T,>(arr: T[]) => {
    const copy = [...arr];
    [copy[from], copy[to]] = [copy[to], copy[from]];
    return copy;
  };
  return withChildren(
    parent,
    swap(names).map(($ref) => ({ $ref })),
    parent.component === "HContainer" ? swap(weights) : undefined,
  );
}

/** Pindahkan tile ke container lain pada indeks tertentu. */
export function relocatePatch(
  statements: Readonly<Record<string, CanvasStatement>>,
  name: string,
  targetName: string,
  index: number,
): string | null {
  const parent = parentOf(statements, name);
  const target = statements[targetName];
  if (!parent || !target || !target.component || !LAYOUT_COMPONENTS.has(target.component)) return null;
  if (parent.name === targetName) {
    const names = childNames(parent);
    return movePatch(statements, name, Math.min(index, names.length - 1) - names.indexOf(name));
  }
  const fromNames = childNames(parent);
  const fromWeights = weightsOf(parent).filter((_, i) => fromNames[i] !== name);
  const toNames = childNames(target);
  const toWeights = weightsOf(target);
  const at = Math.max(0, Math.min(index, toNames.length));
  toNames.splice(at, 0, name);
  // Tile baru mendapat lebar rata-rata agar tidak tampil sempit.
  const avg = toWeights.length ? Math.round(toWeights.reduce((a, b) => a + b, 0) / toWeights.length) : 1;
  toWeights.splice(at, 0, Math.max(1, avg));
  return [
    withChildren(
      parent,
      fromNames.filter((n) => n !== name).map(($ref) => ({ $ref })),
      parent.component === "HContainer" ? fromWeights : undefined,
    ),
    withChildren(target, toNames.map(($ref) => ({ $ref })), target.component === "HContainer" ? toWeights : undefined),
  ].join("\n");
}

/** Ubah proporsi lebar anak HContainer (hasil drag batas tile). */
export function weightsPatch(stmt: CanvasStatement, weights: number[]): string | null {
  if (stmt.component !== "HContainer" || weights.length !== childNames(stmt).length) return null;
  const rounded = weights.map((w) => Math.max(1, Math.round(w)));
  return serializeStatement(stmt.name, "HContainer", { ...stmt.props, weights: rounded });
}

/** Lebar preset (⅓, ½, ⅔, penuh) untuk satu tile di HContainer berskala 12. */
export function widthPatch(
  statements: Readonly<Record<string, CanvasStatement>>,
  name: string,
  span: number,
): string | null {
  const parent = parentOf(statements, name);
  if (!parent || parent.component !== "HContainer") return null;
  const names = childNames(parent);
  const idx = names.indexOf(name);
  const total = 12;
  const rest = names.length - 1;
  const clamped = Math.max(1, Math.min(total - rest, span));
  const others = rest > 0 ? (total - clamped) / rest : 0;
  return weightsPatch(
    parent,
    names.map((_, i) => (i === idx ? clamped : others)),
  );
}

export const CHART_SWAPS: Record<string, string[]> = {
  LineChart: ["BarChart"],
  BarChart: ["LineChart", "PieChart"],
  PieChart: ["BarChart"],
};

/** Ganti tipe chart (Line ⇄ Bar ⇄ Pie) dengan memetakan prop kolom. */
export function changeChartTypePatch(stmt: CanvasStatement, to: string): string | null {
  const p = stmt.props;
  const y = Array.isArray(p.y) ? (p.y as string[]) : typeof p.y === "string" ? [p.y] : [];
  const options = (p.options as Record<string, unknown> | undefined) ?? {};
  const kept = { crossFilter: options.crossFilter, subtitle: options.subtitle };
  const keep = kept.crossFilter === undefined && kept.subtitle === undefined ? undefined : kept;
  if ((to === "LineChart" || to === "BarChart") && (stmt.component === "LineChart" || stmt.component === "BarChart")) {
    return serializeStatement(stmt.name, to, { title: p.title, data: p.data, x: p.x, y, options: keep });
  }
  if (to === "PieChart" && stmt.component === "BarChart" && y.length > 0) {
    return serializeStatement(stmt.name, to, { title: p.title, data: p.data, category: p.x, value: y[0], options: keep });
  }
  if (to === "BarChart" && stmt.component === "PieChart") {
    return serializeStatement(stmt.name, to, { title: p.title, data: p.data, x: p.category, y: [p.value], options: keep });
  }
  return null;
}

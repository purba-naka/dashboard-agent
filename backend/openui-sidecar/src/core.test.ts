import { describe, expect, test } from "bun:test";
import { analyse, apply, prompt, splitStatements, stripFences } from "./core";
import { loadLibrary } from "./library";

const lib = loadLibrary();

const BASE = [
  "root = VContainer([kpis, row])",
  "kpis = HContainer([kpi_rev])",
  'q_kpi = Query("render_kpi", {query: "q_01", value: "revenue"}, {})',
  'kpi_rev = Kpi("Pendapatan", q_kpi)',
  'q_trend = Query("render_query", {query: "q_02"}, {rows: []})',
  'slot_trend = LineChart("Tren", q_trend, "month", ["revenue"], {crossFilter: "month"})',
  "row = HContainer([slot_trend], [1])",
].join("\n");

describe("analyse", () => {
  test("memetakan argumen posisi ke nama prop dan mengenali Query", () => {
    const a = analyse(BASE, lib);
    expect(a.syntax_errors).toEqual([]);
    expect(a.errors).toEqual([]);
    const chart = a.statements.find((s) => s.id === "slot_trend")!;
    expect(chart.kind).toBe("component");
    expect(chart.component).toBe("LineChart");
    expect(chart.props).toEqual({
      title: "Tren",
      data: { $ref: "q_trend" },
      x: "month",
      y: ["revenue"],
      options: { crossFilter: "month" },
    });
    const q = a.statements.find((s) => s.id === "q_kpi")!;
    expect(q.kind).toBe("query");
    expect(q.query).toEqual({ tool: "render_kpi", args: { query: "q_01", value: "revenue" } });
  });

  test("melaporkan komponen tak dikenal dan argumen kurang", () => {
    const a = analyse('root = VContainer([x, y])\nx = Bogus(1)\ny = PieChart("Hanya judul")', lib);
    const codes = a.errors.map((e) => `${e.statementId}:${e.code}`);
    expect(codes).toContain("x:unknown-component");
    expect(codes.some((c) => c.startsWith("y:missing-required"))).toBe(true);
  });

  test("baris tanpa '=' menjadi syntax error", () => {
    expect(analyse("root = VContainer([])\nini bukan statement", lib).syntax_errors).toHaveLength(1);
  });
});

describe("apply", () => {
  test("patch pertama pada program kosong", () => {
    const r = apply("", BASE, lib);
    expect(r.added).toHaveLength(7);
    expect(r.removed).toEqual([]);
  });

  test("ganti, tambah, dan hapus by name + GC statement tak terjangkau", () => {
    const patch = [
      "row = HContainer([slot_trend, slot_mix], [2, 1])",
      'q_mix = Query("render_query", {query: "q_03"}, {rows: []})',
      'slot_mix = PieChart("Komposisi", q_mix, "region", "revenue")',
      "kpis = null",
      "root = VContainer([row])",
    ].join("\n");
    const r = apply(BASE, patch, lib);
    expect(r.added.sort()).toEqual(["q_mix", "slot_mix"]);
    expect(r.replaced.sort()).toEqual(["root", "row"]);
    // kpis dihapus eksplisit; kpi_rev dan q_kpi terbuang (tak terjangkau).
    expect(r.removed.sort()).toEqual(["kpi_rev", "kpis", "q_kpi"]);
    expect(r.errors).toEqual([]);
  });

  test("pagar ```openui-lang dibuang", () => {
    expect(stripFences("```openui-lang\nroot = VContainer([])\n```")).toBe("root = VContainer([])");
  });

  test("string berisi kurung dan baris baru tetap satu statement", () => {
    const src = 'a = Insight("trend", "Judul (Q3)", "Baris 1\\nBaris [2]", "q_1")\nroot = VContainer([a])';
    expect(splitStatements(src)).toHaveLength(2);
  });
});

test("prompt memuat signature dan tool", () => {
  const p = prompt(lib, { preamble: "Kamu menyusun canvas dashboard." });
  expect(p).toContain("LineChart(");
  expect(p).toContain("render_query");
  expect(p).toContain("Kamu menyusun canvas dashboard.");
});

import { describe, expect, it } from "vitest";
import { canvasLibrary } from "./library";
import { baseLibrary } from "./schema";
import { changeChartTypePatch, relocatePatch, serializeStatement, widthPatch } from "./program";
import type { CanvasStatement } from "@/lib/types";

describe("library React OpenUI", () => {
  it("skema identik dengan library dasar (parser browser = sidecar)", () => {
    expect(JSON.parse(JSON.stringify(canvasLibrary.toJSONSchema()))).toEqual(
      JSON.parse(JSON.stringify(baseLibrary.toJSONSchema())),
    );
    expect(canvasLibrary.prompt()).toEqual(baseLibrary.prompt());
  });
});

const s = (name: string, component: string, props: Record<string, unknown>): CanvasStatement => ({
  name,
  source: "",
  kind: "component",
  component,
  refs: [],
  props,
  query: null,
  insight: null,
});
const r = ($ref: string) => ({ $ref });

describe("edit deterministik", () => {
  const statements = {
    root: s("root", "VContainer", { children: [r("row"), r("sec")] }),
    row: s("row", "HContainer", { children: [r("a"), r("b")], weights: [6, 6] }),
    sec: s("sec", "Section", { title: "Detail", children: [r("c")] }),
    a: s("a", "Kpi", { title: "A", data: r("qa") }),
    b: s("b", "PieChart", { title: "B", data: r("qb"), category: "region", value: "revenue" }),
    c: s("c", "Table", { title: "C", data: r("qc") }),
  };

  it("serialize mengikuti urutan argumen posisi dan membuang argumen opsional di ujung", () => {
    expect(serializeStatement("x", "Section", { subtitle: undefined, title: "T", children: [r("a")] })).toBe(
      'x = Section("T", [a])',
    );
  });

  it("lebar preset, pindah container, dan ganti tipe chart", () => {
    expect(widthPatch(statements, "a", 4)).toBe("row = HContainer([a, b], [4, 8])");
    expect(relocatePatch(statements, "c", "row", 1)).toBe(
      'sec = Section("Detail", [])\nrow = HContainer([a, c, b], [6, 6, 6])',
    );
    expect(changeChartTypePatch(statements.b, "BarChart")).toBe('b = BarChart("B", qb, "region", ["revenue"])');
  });
});

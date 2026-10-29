import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { createParser } from "@openuidev/lang-core";
import { ROOT_COMPONENT, TOOLS, baseLibrary } from "./schema";

const SIDECAR = join(__dirname, "..", "..", "..", "backend", "openui-sidecar");
const readJson = (path: string) => JSON.parse(readFileSync(join(SIDECAR, path), "utf8"));

describe("library OpenUI vs sidecar (D6)", () => {
  it("artefak sidecar sama dengan skema saat ini (jalankan `bun run openui:spec` bila gagal)", () => {
    expect(readJson("library/schema.json")).toEqual(JSON.parse(JSON.stringify(baseLibrary.toJSONSchema())));
    expect(readJson("library/spec.json")).toEqual(JSON.parse(JSON.stringify(baseLibrary.toSpec())));
    expect(readJson("library/tools.json")).toEqual(TOOLS);
    expect(readJson("library/meta.json")).toEqual({ root: ROOT_COMPONENT });
  });

  it("versi lang-core frontend dan sidecar identik", () => {
    const front = JSON.parse(readFileSync(join(__dirname, "..", "..", "package.json"), "utf8"));
    const side = readJson("package.json");
    expect(side.dependencies["@openuidev/lang-core"]).toBe(front.dependencies["@openuidev/lang-core"]);
  });

  it("parser memetakan contoh program tanpa error", () => {
    const parser = createParser(baseLibrary.toJSONSchema(), ROOT_COMPONENT);
    const result = parser.parse(
      [
        "root = VContainer([kpis, sec])",
        "kpis = HContainer([kpi_rev, slc])",
        'q_kpi = Query("render_kpi", {query: "q_01", value: "revenue"}, {formatted: {}})',
        'kpi_rev = Kpi("Pendapatan", q_kpi)',
        'slc = Slicer("Wilayah", "region", "in", q_reg)',
        'q_reg = Query("render_query", {query: "q_03"}, {rows: []})',
        'sec = Section("Detail", [tabs])',
        'tabs = Tabs(["Grafik", "Tabel"], [bar, tbl])',
        'bar = BarChart("Per wilayah", q_reg, "region", ["revenue"], {crossFilter: "region"})',
        'tbl = Table("Rincian", q_reg, ["region", "revenue"])',
      ].join("\n"),
    );
    expect(result.meta.errors).toEqual([]);
    expect(result.meta.unresolved).toEqual([]);
    expect(result.root?.typeName).toBe("VContainer");
  });
});

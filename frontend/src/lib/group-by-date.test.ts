import { describe, expect, it } from "vitest";
import { dateGroup, groupByDate } from "./group-by-date";

const now = new Date(2026, 9, 28, 15, 0);
const at = (daysAgo: number) => new Date(2026, 9, 28 - daysAgo, 9, 0).toISOString();

describe("groupByDate", () => {
  it("mengelompokkan per hari", () => {
    expect(dateGroup(at(0), now)).toBe("Hari ini");
    expect(dateGroup(at(1), now)).toBe("Kemarin");
    expect(dateGroup(at(5), now)).toBe("7 hari terakhir");
    expect(dateGroup(at(30), now)).toBe("Lebih lama");
  });
  it("membuang kelompok kosong dan menjaga urutan", () => {
    const r = groupByDate([{ created_at: at(30) }, { created_at: at(0) }], now);
    expect(r.map((g) => g.group)).toEqual(["Hari ini", "Lebih lama"]);
  });
});

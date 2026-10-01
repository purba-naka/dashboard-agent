import fc from "fast-check";
import { describe, expect, it } from "vitest";

import {
  crossFilterPredicate,
  filtersDiffer,
  filtersEqual,
  isCrossFilterActive,
  normalizeFilters,
  predicateKey,
  toggleCrossFilter,
  type CrossFilterElement,
} from "./filters";
import type { FilterSet, Predicate, Scalar } from "./types";

// ---------------------------------------------------------------------------
// Generator (domain kecil agar sering terjadi tabrakan table/column/value)
// ---------------------------------------------------------------------------

const tableArb = fc.constantFrom("sales", "customers");
const columnArb = fc.constantFrom("region", "segment", "order_date");
const scalarArb: fc.Arbitrary<Scalar> = fc.oneof(
  fc.constantFrom("North", "South", "East", "1"),
  fc.integer({ min: -3, max: 3 }),
  fc.boolean(),
  fc.constant(null),
);
const dateArb = fc.option(
  fc.constantFrom("2024-01-01", "2024-02-15", "2024-03-31T10:00:00Z"),
  { nil: null },
);

const predicateArb: fc.Arbitrary<Predicate> = fc.oneof(
  fc.record({
    kind: fc.constant("in" as const),
    table: tableArb,
    column: columnArb,
    values: fc.array(scalarArb, { maxLength: 3 }),
  }),
  fc.record({
    kind: fc.constant("date_range" as const),
    table: tableArb,
    column: columnArb,
    start: dateArb,
    end: dateArb,
  }),
);

const elementArb: fc.Arbitrary<CrossFilterElement> = fc.record({
  table: tableArb,
  column: columnArb,
  value: scalarArb,
});

/** FilterSet sembarang (belum tentu kanonik; boleh duplikat/no-op/urutan acak). */
const filterSetArb: fc.Arbitrary<FilterSet> = fc.array(predicateArb, { maxLength: 6 });

/** Pasangan (fs, e) — sebagian besar kasus `e` sudah aktif di `fs`. */
const fsAndElementArb = fc.oneof(
  fc.tuple(filterSetArb, elementArb),
  fc
    .tuple(filterSetArb, elementArb, fc.nat())
    .map(([fs, e, i]): [FilterSet, CrossFilterElement] => {
      const copy = [...fs];
      copy.splice(i % (copy.length + 1), 0, crossFilterPredicate(e));
      return [copy, e];
    }),
);

const keys = (fs: readonly Predicate[]) => new Set(normalizeFilters(fs).map(predicateKey));

// ---------------------------------------------------------------------------
// Property 33
// ---------------------------------------------------------------------------

// Feature: dashboard-studio-agent, Property 33: Toggle Cross_Filter
describe("Property 33: Toggle Cross_Filter", () => {
  /** **Validates: Requirements 24.1, 24.4** */
  it("toggle dua kali mengembalikan fs (identik untuk fs kanonik, sama sebagai himpunan untuk fs sembarang)", () => {
    fc.assert(
      fc.property(fsAndElementArb, ([fs, e]) => {
        const twice = toggleCrossFilter(toggleCrossFilter(fs, e), e);
        expect(filtersEqual(twice, fs)).toBe(true);

        const canonical = normalizeFilters(fs);
        expect(toggleCrossFilter(toggleCrossFilter(canonical, e), e)).toEqual(canonical);
      }),
      { numRuns: 100 },
    );
  });

  /** **Validates: Requirements 24.1, 24.4** */
  it("toggle elemen yang belum aktif menambah tepat satu predikat baru untuk kolom dan nilai e", () => {
    fc.assert(
      fc.property(filterSetArb, elementArb, (fs, e) => {
        fc.pre(!isCrossFilterActive(fs, e));
        const before = keys(fs);
        const result = toggleCrossFilter(fs, e);
        const after = keys(result);

        // Semua predikat lama tetap ada.
        for (const k of before) expect(after.has(k)).toBe(true);
        // Tepat satu predikat baru.
        const added = normalizeFilters(result).filter((p) => !before.has(predicateKey(p)));
        expect(added).toHaveLength(1);
        expect(after.size).toBe(before.size + 1);
        expect(added[0]).toEqual({
          kind: "in",
          table: e.table,
          column: e.column,
          values: [e.value],
        });
        expect(isCrossFilterActive(result, e)).toBe(true);
      }),
      { numRuns: 100 },
    );
  });
});

// ---------------------------------------------------------------------------
// Contoh
// ---------------------------------------------------------------------------

describe("toggleCrossFilter (contoh)", () => {
  const e: CrossFilterElement = { table: "sales", column: "region", value: "North" };

  it("klik pertama mengaktifkan, klik kedua menonaktifkan", () => {
    const on = toggleCrossFilter([], e);
    expect(on).toEqual([{ kind: "in", table: "sales", column: "region", values: ["North"] }]);
    expect(toggleCrossFilter(on, e)).toEqual([]);
  });

  it("elemen berbeda pada kolom yang sama menjadi predikat terpisah", () => {
    const fs = toggleCrossFilter(toggleCrossFilter([], e), { ...e, value: "South" });
    expect(fs).toHaveLength(2);
    expect(isCrossFilterActive(fs, e)).toBe(true);
  });
});

describe("filtersDiffer (contoh)", () => {
  const region = (values: Scalar[]): Predicate => ({
    kind: "in",
    table: "sales",
    column: "region",
    values,
  });
  const date = (start: string | null, end: string | null): Predicate => ({
    kind: "date_range",
    table: "sales",
    column: "order_date",
    start,
    end,
  });

  it("tidak berbeda bila hanya urutan predikat/nilai atau duplikat yang berbeda", () => {
    expect(
      filtersDiffer(
        [region(["North", "South"]), date("2024-01-01", null)],
        [date("2024-01-01", null), region(["South", "North", "North"]), region(["North", "South"])],
      ),
    ).toBe(false);
  });

  it("tidak berbeda untuk null/undefined/[] dan date_range tanpa ujung (no-op)", () => {
    expect(filtersDiffer(null, [])).toBe(false);
    expect(filtersDiffer(undefined, [date(null, null)])).toBe(false);
  });

  it("waktu setelah tanggal diabaikan pada date_range", () => {
    expect(filtersDiffer([date("2024-01-01T10:00:00Z", null)], [date("2024-01-01", null)])).toBe(
      false,
    );
  });

  it("berbeda bila nilai, ujung tanggal, atau jumlah predikat berbeda", () => {
    expect(filtersDiffer([region(["North"])], [region(["South"])])).toBe(true);
    expect(filtersDiffer([date("2024-01-01", null)], [date(null, "2024-01-01")])).toBe(true);
    expect(filtersDiffer([], [region(["North"])])).toBe(true);
    expect(filtersDiffer([region([1])], [region(["1"])])).toBe(true);
    expect(filtersDiffer([region([])], [])).toBe(true);
  });
});

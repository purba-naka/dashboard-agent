import { describe, expect, it } from "vitest";
import type { Dataset } from "@/lib/types";
import { suggestQuestions } from "./suggestions";

const ds = (schema: Dataset["schema"]) => [{ schema }] as unknown as Dataset[];

describe("suggestQuestions", () => {
  it("menyusun tren, peringkat, dan perbandingan dari skema", () => {
    const q = suggestQuestions(
      ds([
        { name: "periode", type: "date" },
        { name: "kode_provinsi", type: "integer" },
        { name: "tahun", type: "integer" },
        { name: "nama_provinsi", type: "string" },
        { name: "kelompok", type: "string" },
        { name: "ntp", type: "float" },
      ]),
    );
    expect(q).toEqual([
      "Bagaimana tren ntp per periode?",
      "nama provinsi mana yang ntp-nya tertinggi dan terendah?",
      "Bandingkan ntp antar kelompok.",
      "Buatkan dashboard ringkasan dari data ini.",
    ]);
  });

  it("kosong tanpa dataset", () => {
    expect(suggestQuestions([])).toEqual([]);
  });
});

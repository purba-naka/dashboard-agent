const FORMATTERS = new Map<number, Intl.NumberFormat>();

/** Angka gaya id-ID (ribuan ".", desimal ","). Satu-satunya formatter angka untuk chart. */
export function formatNumber(value: unknown, maxDecimals = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return value == null ? "" : String(value);
  let f = FORMATTERS.get(maxDecimals);
  if (!f) {
    f = new Intl.NumberFormat("id-ID", { maximumFractionDigits: maxDecimals });
    FORMATTERS.set(maxDecimals, f);
  }
  return f.format(value);
}

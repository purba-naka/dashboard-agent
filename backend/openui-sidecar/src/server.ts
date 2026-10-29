/**
 * Sidecar OpenUI: JSON-RPC sederhana lewat stdin/stdout, satu JSON per baris.
 *
 * Permintaan: `{"id": 1, "method": "apply", "params": {...}}`
 * Jawaban:    `{"id": 1, "result": {...}}` atau `{"id": 1, "error": "pesan"}`
 *
 * Metode: `ping`, `analyse {program}`, `apply {program, patch}`,
 * `prompt {preamble?, additionalRules?, toolExamples?}`.
 */
import { createInterface } from "node:readline";
import { analyse, apply, prompt } from "./core";
import { loadLibrary } from "./library";

process.env.OPENUI_TELEMETRY_DISABLED = "1";

const lib = loadLibrary();

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function handle(method: string, params: any): unknown {
  switch (method) {
    case "ping":
      return { ok: true, root: lib.root };
    case "analyse":
      return analyse(String(params.program ?? ""), lib);
    case "apply":
      return apply(String(params.program ?? ""), String(params.patch ?? ""), lib);
    case "prompt":
      return { prompt: prompt(lib, params ?? {}) };
    default:
      throw new Error(`Metode tidak dikenal: ${method}`);
  }
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let id: unknown = null;
  try {
    const msg = JSON.parse(line);
    id = msg.id ?? null;
    process.stdout.write(`${JSON.stringify({ id, result: handle(msg.method, msg.params) })}\n`);
  } catch (e) {
    process.stdout.write(`${JSON.stringify({ id, error: e instanceof Error ? e.message : String(e) })}\n`);
  }
});

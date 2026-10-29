import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Library } from "./core";

/** Muat artefak hasil `bun run openui:spec` (frontend). */
export function loadLibrary(dir = join(dirname(fileURLToPath(import.meta.url)), "..", "library")): Library {
  const read = (name: string) => JSON.parse(readFileSync(join(dir, name), "utf8"));
  return { schema: read("schema.json"), spec: read("spec.json"), tools: read("tools.json"), root: read("meta.json").root };
}

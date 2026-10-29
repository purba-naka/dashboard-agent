/**
 * Generate artefak library OpenUI untuk sidecar backend (D6).
 *
 * Jalankan dari `frontend/`: `bun run openui:spec`. Hasilnya dikomit di
 * `backend/openui-sidecar/library/` agar backend dan frontend memakai satu
 * sumber library.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ROOT_COMPONENT, TOOLS, baseLibrary } from "../src/openui/schema";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "..", "backend", "openui-sidecar", "library");
mkdirSync(outDir, { recursive: true });

const write = (name: string, value: unknown) =>
  writeFileSync(join(outDir, name), `${JSON.stringify(value, null, 2)}\n`, "utf8");

write("schema.json", baseLibrary.toJSONSchema());
write("spec.json", baseLibrary.toSpec());
write("tools.json", TOOLS);
write("meta.json", { root: ROOT_COMPONENT });
console.log(`OpenUI library ditulis ke ${outDir}`);

/**
 * Inti sidecar OpenUI (D6): parse, merge, dan analisis statement memakai
 * `@openuidev/lang-core` asli, sehingga backend memvalidasi persis seperti
 * Renderer browser mem-parse.
 *
 * Fungsi di sini murni (tanpa I/O) agar mudah diuji dengan `bun test`.
 */
import {
  createParser,
  generateSystemPrompt,
  mergeStatements,
  parseExpression,
  split,
  tokenize,
  walkAST,
  type ASTNode,
  type LibraryJSONSchema,
  type LibrarySpec,
  type ToolSpec,
} from "@openuidev/lang-core";

export interface Library {
  schema: LibraryJSONSchema;
  spec: LibrarySpec;
  tools: ToolSpec[];
  root: string;
}

export type StatementKind = "component" | "query" | "mutation" | "state" | "value";

export interface StatementInfo {
  id: string;
  raw: string;
  kind: StatementKind;
  /** Nama komponen untuk `component`; "Query"/"Mutation" untuk keduanya. */
  component: string | null;
  /** Props bernama (posisi -> nama sesuai urutan skema). Ref = `{"$ref": name}`. */
  props: Record<string, unknown>;
  /** Nama statement yang dirujuk. */
  refs: string[];
  /** Untuk `query`: nama tool dan argumen literal. */
  query: { tool: string | null; args: unknown } | null;
}

export interface SyntaxError {
  raw: string;
  message: string;
}

export interface ProgramAnalysis {
  program: string;
  statements: StatementInfo[];
  syntax_errors: SyntaxError[];
  /** Error validasi parser resmi (komponen tak dikenal, argumen kurang, ...). */
  errors: { code: string; component: string; path: string; message: string; statementId?: string; severity?: string }[];
  unresolved: string[];
  orphaned: string[];
  has_root: boolean;
}

export interface ApplyResult extends ProgramAnalysis {
  /** Nama statement di patch (urutan patch). */
  patch_ids: string[];
  added: string[];
  replaced: string[];
  /** Dihapus eksplisit (`nama = null`) atau terbuang karena tak terjangkau dari root. */
  removed: string[];
}

/** Salinan `splitStatementSource` lang-core (internal, tidak diekspor). */
export function splitStatements(input: string): string[] {
  const stmts: string[] = [];
  let depth = 0;
  let inStr: string | false = false;
  let esc = false;
  let start = 0;
  for (let i = 0; i < input.length; i++) {
    const c = input[i];
    if (esc) {
      esc = false;
      continue;
    }
    if (c === "\\" && inStr) {
      esc = true;
      continue;
    }
    if (inStr) {
      if (c === inStr) inStr = false;
      continue;
    }
    if (c === '"' || c === "'") {
      inStr = c;
      continue;
    }
    if (c === "(" || c === "[" || c === "{") depth++;
    else if (c === ")" || c === "]" || c === "}") depth = Math.max(0, depth - 1);
    else if (c === "\n" && depth <= 0) {
      const stmt = input.slice(start, i).trim();
      if (stmt) stmts.push(stmt);
      start = i + 1;
    }
  }
  const tail = input.slice(start).trim();
  if (tail) stmts.push(tail);
  return stmts;
}

/** Buang pagar ```openui-lang bila model menyertakannya. */
export function stripFences(text: string): string {
  const m = text.match(/```(?:openui-lang|openui)?\s*\n([\s\S]*?)```/);
  return (m ? m[1] : text).trim();
}

function astToJson(node: ASTNode, lib: Library): unknown {
  switch (node.k) {
    case "Str":
    case "Num":
    case "Bool":
      return node.v;
    case "Null":
      return null;
    case "Arr":
      return node.els.map((e) => astToJson(e, lib));
    case "Obj":
      return Object.fromEntries(node.entries.map(([k, v]) => [k, astToJson(v, lib)]));
    case "Ref":
    case "RuntimeRef":
      return { $ref: node.n };
    case "StateRef":
      return { $state: node.n };
    case "Comp":
      return { $component: node.name, props: mapProps(node, lib) };
    default:
      return { $expr: node.k };
  }
}

function mapProps(node: Extract<ASTNode, { k: "Comp" }>, lib: Library): Record<string, unknown> {
  const def = lib.schema.$defs?.[node.name];
  const keys = Object.keys(def?.properties ?? {});
  const props: Record<string, unknown> = {};
  node.args.forEach((arg, i) => {
    props[keys[i] ?? `$${i}`] = astToJson(arg, lib);
  });
  for (const [k, v] of Object.entries(node.mappedProps ?? {})) props[k] = astToJson(v, lib);
  return props;
}

function analyseStatement(raw: string, lib: Library): StatementInfo | SyntaxError {
  let stmt;
  try {
    stmt = split(tokenize(raw))[0];
  } catch (e) {
    return { raw, message: `Tokenizer gagal: ${e instanceof Error ? e.message : String(e)}` };
  }
  if (!stmt) return { raw, message: "Baris bukan statement `nama = Ekspresi`." };
  let ast: ASTNode;
  try {
    ast = parseExpression(stmt.tokens);
  } catch (e) {
    return { raw, message: `Ekspresi tidak valid: ${e instanceof Error ? e.message : String(e)}` };
  }
  const refs = new Set<string>();
  walkAST(ast, (n) => {
    if (n.k === "Ref" || n.k === "RuntimeRef") refs.add(n.n);
  });
  const base = { id: stmt.id, raw, refs: [...refs], props: {}, query: null };
  if (stmt.id.startsWith("$")) return { ...base, kind: "state", component: null };
  if (ast.k === "Comp" && (ast.name === "Query" || ast.name === "Mutation")) {
    const [tool, args] = ast.args;
    return {
      ...base,
      kind: ast.name === "Query" ? "query" : "mutation",
      component: ast.name,
      query: {
        tool: tool && tool.k === "Str" ? tool.v : null,
        args: args ? astToJson(args, lib) : null,
      },
    };
  }
  if (ast.k === "Comp") return { ...base, kind: "component", component: ast.name, props: mapProps(ast, lib) };
  return { ...base, kind: "value", component: null };
}

export function analyse(program: string, lib: Library): ProgramAnalysis {
  const statements: StatementInfo[] = [];
  const syntax_errors: SyntaxError[] = [];
  for (const raw of splitStatements(program)) {
    const info = analyseStatement(raw, lib);
    if ("id" in info) statements.push(info);
    else syntax_errors.push(info);
  }
  const result = createParser(lib.schema, lib.root).parse(program);
  return {
    program,
    statements,
    syntax_errors,
    errors: result.meta.errors.map((e) => ({ ...e })),
    unresolved: result.meta.unresolved,
    orphaned: result.meta.orphaned,
    has_root: statements.some((s) => s.id === "root"),
  };
}

/** Terapkan patch `editMode` ke program (semantik `mergeStatements`). */
export function apply(program: string, patch: string, lib: Library): ApplyResult {
  const clean = stripFences(patch);
  const patchAnalysis = analyse(clean, lib);
  const before = new Map(analyse(program, lib).statements.map((s) => [s.id, s.raw]));
  const merged = program.trim() ? mergeStatements(program, clean) : patchAnalysis.statements.map((s) => s.raw).join("\n");
  const after = analyse(merged, lib);
  const afterIds = new Set(after.statements.map((s) => s.id));
  const added: string[] = [];
  const replaced: string[] = [];
  for (const s of after.statements) {
    const prev = before.get(s.id);
    if (prev === undefined) added.push(s.id);
    else if (prev !== s.raw) replaced.push(s.id);
  }
  const removed = [...before.keys()].filter((id) => !afterIds.has(id));
  return {
    ...after,
    // Error sintaks patch dilaporkan apa adanya: merge membuang baris itu diam-diam.
    syntax_errors: patchAnalysis.syntax_errors,
    patch_ids: patchAnalysis.statements.map((s) => s.id),
    added,
    replaced,
    removed,
  };
}

export interface PromptParams {
  preamble?: string;
  additionalRules?: string[];
  toolExamples?: string[];
}

export function prompt(lib: Library, params: PromptParams = {}): string {
  return generateSystemPrompt({
    library: lib.spec,
    promptOptions: {
      tools: lib.tools,
      toolCalls: true,
      // Filter milik host (D7) dan angka hanya dari query (D2): tanpa $binding,
      // form, maupun agregasi @Sum/@Count di klien.
      bindings: false,
      builtinFunctions: false,
      editMode: true,
      preamble: params.preamble,
      additionalRules: params.additionalRules,
      toolExamples: params.toolExamples,
    },
  });
}

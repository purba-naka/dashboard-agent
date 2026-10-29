/**
 * Skema library komponen OpenUI untuk canvas dashboard (D4, D9).
 *
 * File ini framework-agnostic (hanya `zod/v4` + `@openuidev/lang-core`) agar
 * bisa dipakai tiga pihak dengan definisi yang sama:
 * - `library.tsx` memasang renderer React di atas skema ini;
 * - `scripts/openui-spec.ts` menulis JSON Schema + spec prompt untuk sidecar
 *   backend (`backend/openui-sidecar/library/`);
 * - test kesetaraan parser.
 *
 * Aturan: komponen data menerima hasil statement `Query(...)` (tidak ada angka
 * inline, D2). Urutan key `z.object` = urutan argumen posisi di OpenUI Lang,
 * jadi JANGAN mengubah urutan field tanpa migrasi program tersimpan.
 */
import { z } from "zod/v4";
import { createLibrary, defineComponent, type ToolSpec } from "@openuidev/lang-core";

/** Nama komponen root program (dipakai parser & prompt). */
export const ROOT_COMPONENT = "VContainer";

/** Hasil statement Query; divalidasi backend, bukan oleh skema prop. */
const queryResult = (what: string) =>
  z.any().describe(`Reference to a Query statement (${what}). Never inline data.`);

const column = (what: string) => z.string().describe(`Column name from the query result (${what})`);

const chartOptions = z
  .object({
    crossFilter: z.string().optional().describe("Dimension column toggled as Cross_Filter on click"),
    subtitle: z.string().optional(),
  })
  .describe("Optional chart options");

// ---------------------------------------------------------------------------
// Komponen data (tile)
// ---------------------------------------------------------------------------

export const LineChart = defineComponent({
  name: "LineChart",
  description: "Trend over an ordered dimension (usually time). One line per y column.",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    x: column("x axis"),
    y: z.array(z.string()).describe("Measure columns, one line each"),
    options: chartOptions
      .extend({ area: z.boolean().optional(), smooth: z.boolean().optional() })
      .optional(),
  }),
  component: null,
});

export const BarChart = defineComponent({
  name: "BarChart",
  description:
    "Compare a measure across categories. stack=true stacks series; horizontal=true for long labels; lines turns listed y columns into lines (combo).",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    x: column("category axis"),
    y: z.array(z.string()).describe("Measure columns"),
    options: chartOptions
      .extend({
        stack: z.boolean().optional(),
        horizontal: z.boolean().optional(),
        lines: z.array(z.string()).optional(),
      })
      .optional(),
  }),
  component: null,
});

export const PieChart = defineComponent({
  name: "PieChart",
  description: "Part-of-whole for at most 8 categories.",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    category: column("slice label"),
    value: column("slice value"),
    options: chartOptions.extend({ donut: z.boolean().optional() }).optional(),
  }),
  component: null,
});

export const ScatterChart = defineComponent({
  name: "ScatterChart",
  description: "Relationship between two measures; optional series column for colour.",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    x: column("x measure"),
    y: column("y measure"),
    options: chartOptions.extend({ series: z.string().optional() }).optional(),
  }),
  component: null,
});

export const Heatmap = defineComponent({
  name: "Heatmap",
  description: "Measure intensity across two dimensions.",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    x: column("x dimension"),
    y: column("y dimension"),
    value: column("cell value"),
    options: chartOptions.optional(),
  }),
  component: null,
});

export const Waterfall = defineComponent({
  name: "Waterfall",
  description: "Bridge from a start value through positive/negative contributions (in row order).",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    category: column("step label"),
    value: column("signed step value"),
    options: chartOptions.optional(),
  }),
  component: null,
});

export const Kpi = defineComponent({
  name: "Kpi",
  description: "Headline number with optional comparison; data MUST be a Query(\"render_kpi\", ...).",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_kpi", ...)'),
  }),
  component: null,
});

export const Table = defineComponent({
  name: "Table",
  description: "Detail rows; columns limits and orders the shown columns.",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    columns: z.array(z.string()).optional(),
  }),
  component: null,
});

export const Matrix = defineComponent({
  name: "Matrix",
  description: "Pivot: rows x columns with one aggregated value per cell.",
  props: z.object({
    title: z.string(),
    data: queryResult('Query("render_query", ...)'),
    rows: column("row dimension"),
    columns: column("column dimension"),
    value: column("cell value"),
  }),
  component: null,
});

export const Slicer = defineComponent({
  name: "Slicer",
  description:
    "Canvas filter tile writing the dashboard Global_Filter. mode \"in\" lists distinct values from data; \"date_range\" picks a range.",
  props: z.object({
    title: z.string(),
    column: z.string().describe("Column to filter"),
    mode: z.enum(["in", "date_range"]),
    data: queryResult('Query("render_query", ...) providing the column values').optional(),
  }),
  component: null,
});

export const Insight = defineComponent({
  name: "Insight",
  description:
    "Narrative finding. Every number in text must come from the query result; backend verifies it.",
  props: z.object({
    type: z.enum(["trend", "anomaly", "comparison", "top_bottom_contributors", "cross_dataset_correlation"]),
    title: z.string(),
    text: z.string(),
    query: z.string().describe("query_id the insight is based on"),
  }),
  component: null,
});

export const Unsupported = defineComponent({
  name: "Unsupported",
  description: "System placeholder for migrated items that could not be converted. Do not write.",
  props: z.object({ title: z.string(), reason: z.string() }),
  component: null,
});

const TILES = [
  LineChart,
  BarChart,
  PieChart,
  ScatterChart,
  Heatmap,
  Waterfall,
  Kpi,
  Table,
  Matrix,
  Slicer,
  Insight,
  Unsupported,
] as const;

const tileRefs = TILES.map((c) => c.ref);

// ---------------------------------------------------------------------------
// Layout (D8): container bersarang gaya Tableau
// ---------------------------------------------------------------------------

/* eslint-disable @typescript-eslint/no-explicit-any -- rekursi container */
export const HContainer: any = defineComponent({
  name: "HContainer",
  description:
    "Horizontal row of tiles/containers. weights = relative widths (same length as children), default equal.",
  props: z.object({
    children: z.array(z.union([...tileRefs, z.lazy(() => VContainer.ref), z.lazy(() => Section.ref)] as any)),
    weights: z.array(z.number()).optional(),
  }),
  component: null,
});

export const Tabs: any = defineComponent({
  name: "Tabs",
  description: "Tabbed views; labels[i] is the tab title of children[i].",
  props: z.object({
    labels: z.array(z.string()),
    children: z.array(z.union([...tileRefs, HContainer.ref, z.lazy(() => VContainer.ref)] as any)),
  }),
  component: null,
});

export const Section: any = defineComponent({
  name: "Section",
  description: "Titled group of tiles/containers stacked vertically.",
  props: z.object({
    title: z.string(),
    children: z.array(z.union([...tileRefs, HContainer.ref, Tabs.ref, z.lazy(() => VContainer.ref)] as any)),
    subtitle: z.string().optional(),
  }),
  component: null,
});

export const VContainer: any = defineComponent({
  name: "VContainer",
  description: "Vertical stack of tiles/containers. Use as root.",
  props: z.object({
    children: z.array(z.union([...tileRefs, HContainer.ref, Tabs.ref, Section.ref] as any)),
  }),
  component: null,
});
/* eslint-enable @typescript-eslint/no-explicit-any */

/** Semua definisi komponen (urutan = urutan di prompt). */
export const COMPONENTS = [VContainer, HContainer, Section, Tabs, ...TILES] as const;

export const COMPONENT_GROUPS = [
  { name: "Layout", components: ["VContainer", "HContainer", "Section", "Tabs"] },
  {
    name: "Data",
    components: ["Kpi", "LineChart", "BarChart", "PieChart", "ScatterChart", "Heatmap", "Waterfall", "Table", "Matrix"],
  },
  { name: "Filter & text", components: ["Slicer", "Insight"] },
];

/** Library tanpa renderer (prompt, JSON Schema, parser). */
export const baseLibrary = createLibrary({
  components: [...COMPONENTS],
  componentGroups: COMPONENT_GROUPS,
  root: ROOT_COMPONENT,
});

/** Tool yang boleh dipakai `Query(...)` (D7); filter disisipkan host. */
export const TOOLS: ToolSpec[] = [
  {
    name: "render_query",
    description:
      "Run a saved query (query_id from run_query) with the active dashboard filters. Returns {columns, rows}.",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string", description: "query_id" } },
      required: ["query"],
    },
    outputSchema: {
      type: "object",
      properties: {
        columns: { type: "array", items: { type: "string" } },
        rows: { type: "array", items: { type: "object" } },
      },
    },
  },
  {
    name: "render_kpi",
    description: "Compute a KPI value (and optional comparison) from a saved query. Use only as data of Kpi.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "query_id" },
        value: { type: "string", description: "value column" },
        compare: { type: "string", description: "comparison column (optional)" },
        compare_label: { type: "string" },
        format: { type: "string", enum: ["number", "currency", "percent"] },
        currency: { type: "string" },
        decimals: { type: "number" },
        compact: { type: "boolean" },
        good: { type: "string", enum: ["up", "down", "neutral"] },
        metric: { type: "string", description: "semantic metric name (optional)" },
      },
      required: ["query", "value"],
    },
    outputSchema: { type: "object", properties: { formatted: { type: "object" } } },
  },
];

export const TOOL_NAMES = TOOLS.map((t) => t.name);

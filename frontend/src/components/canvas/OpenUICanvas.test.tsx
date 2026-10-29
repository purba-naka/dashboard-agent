// Canvas OpenUI (docs/plan-openui-canvas.md irisan 3 & 5): render program,
// batch Query, cross-filter, dan edit manual sebagai patch.
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  CanvasRenderRequest,
  CanvasRenderResponse,
  CanvasStatement,
  Command,
  DashboardSnapshot,
  Dataset,
  PatchEvent,
} from "@/lib/types";
import { OpenUICanvas } from "./OpenUICanvas";
import type { CanvasClient } from "./client";

const echartsState = vi.hoisted(() => ({
  props: [] as { option: Record<string, unknown>; onEvents?: Record<string, (p: unknown) => void> }[],
}));
vi.mock("echarts-for-react", () => ({
  default: (props: { option: Record<string, unknown>; onEvents?: Record<string, (p: unknown) => void> }) => {
    echartsState.props.push(props);
    return <div data-testid="echarts" data-option={JSON.stringify(props.option)} />;
  },
}));

afterEach(() => {
  cleanup();
  echartsState.props = [];
});

const stmt = (name: string, source: string, over: Partial<CanvasStatement>): CanvasStatement => ({
  name,
  source,
  kind: "component",
  component: null,
  refs: [],
  props: {},
  query: null,
  insight: null,
  ...over,
});

const ref = ($ref: string) => ({ $ref });

const STATEMENTS: Record<string, CanvasStatement> = Object.fromEntries(
  [
    stmt("root", "root = VContainer([row])", { component: "VContainer", props: { children: [ref("row")] } }),
    stmt("row", "row = HContainer([kpi, trend, tbl], [3, 6, 3])", {
      component: "HContainer",
      props: { children: [ref("kpi"), ref("trend"), ref("tbl")], weights: [3, 6, 3] },
    }),
    stmt("q_kpi", 'q_kpi = Query("render_kpi", {query: "q1", value: "revenue"}, {formatted: {}})', {
      kind: "query",
      component: "Query",
      query: { tool: "render_kpi", args: { query: "q1", value: "revenue" } },
    }),
    stmt("kpi", 'kpi = Kpi("Pendapatan", q_kpi)', { component: "Kpi", props: { title: "Pendapatan", data: ref("q_kpi") } }),
    stmt("q_m", 'q_m = Query("render_query", {query: "q2"}, {rows: []})', {
      kind: "query",
      component: "Query",
      query: { tool: "render_query", args: { query: "q2" } },
    }),
    stmt("trend", 'trend = BarChart("Per wilayah", q_m, "region", ["revenue"], {crossFilter: "region"})', {
      component: "BarChart",
      props: { title: "Per wilayah", data: ref("q_m"), x: "region", y: ["revenue"], options: { crossFilter: "region" } },
    }),
    stmt("tbl", 'tbl = Table("Rincian", q_m)', { component: "Table", props: { title: "Rincian", data: ref("q_m") } }),
  ].map((s) => [s.name, s]),
);

const snapshot: DashboardSnapshot = {
  id: "d1",
  title: "Penjualan",
  version: 4,
  content: { title: "Penjualan", items: {}, layout: {}, global_filters: [], statements: STATEMENTS },
  can_undo: false,
  can_redo: false,
  item_status: {},
};

const datasets = [
  { id: "ds1", table_name: "sales", schema: [{ name: "region", type: "string" }] },
] as unknown as Dataset[];

function makeClient() {
  const bodies: CanvasRenderRequest[] = [];
  const commands: Command[] = [];
  const client: CanvasClient = {
    render: vi.fn(),
    command: vi.fn(async (_d: string, _v: number, c: Command) => {
      commands.push(c);
      return {} as PatchEvent;
    }),
    renderCanvas: vi.fn(async (_d: string, body: CanvasRenderRequest): Promise<CanvasRenderResponse> => {
      bodies.push(body);
      const results: CanvasRenderResponse["results"] = {};
      for (const q of body.queries) {
        results[q.key] =
          q.tool === "render_kpi"
            ? {
                status: "ok",
                filter_unaffected: false,
                kpi: {
                  value: 120,
                  comparison: null,
                  delta: null,
                  delta_pct: null,
                  sentiment: "neutral",
                  formatted: { value: "120", comparison: null, delta: null, delta_pct: null },
                },
              }
            : {
                status: "ok",
                filter_unaffected: false,
                columns: ["region", "revenue"],
                rows: [
                  { region: "Jawa", revenue: 120 },
                  { region: "Bali", revenue: 98 },
                ],
              };
      }
      return { version: 4, results };
    }),
  };
  return { client, bodies, commands };
}

describe("OpenUICanvas", () => {
  it("merender tile dari program dan membatch semua Query dalam satu request", async () => {
    const { client, bodies } = makeClient();
    render(<OpenUICanvas snapshot={snapshot} crossFilters={[]} datasets={datasets} client={client} />);
    expect(await screen.findByRole("group", { name: "Pendapatan: 120" })).toBeTruthy();
    expect(await screen.findByText("Jawa")).toBeTruthy();
    expect(bodies).toHaveLength(1);
    expect(bodies[0].queries.map((q) => q.tool).sort()).toEqual(["render_kpi", "render_query"]);
    const option = JSON.parse(screen.getByTestId("echarts").getAttribute("data-option")!);
    expect(option.dataset.source).toEqual([
      ["Jawa", 120],
      ["Bali", 98],
    ]);
  });

  it("klik elemen chart men-toggle Cross_Filter pada kolom crossFilter", async () => {
    const { client } = makeClient();
    const onChange = vi.fn();
    render(
      <OpenUICanvas snapshot={snapshot} crossFilters={[]} datasets={datasets} client={client} onCrossFiltersChange={onChange} />,
    );
    await screen.findByTestId("echarts");
    echartsState.props.at(-1)!.onEvents!.click({ name: "Bali" });
    expect(onChange).toHaveBeenCalledWith([{ kind: "in", table: "sales", column: "region", values: ["Bali"] }]);
  });

  it("aksi tile menghasilkan patch OpenUI Lang deterministik", async () => {
    const user = userEvent.setup();
    const { client, commands } = makeClient();
    render(<OpenUICanvas snapshot={snapshot} crossFilters={[]} datasets={datasets} client={client} />);
    const tile = (await screen.findByText("Rincian")).closest("[data-tile]") as HTMLElement;
    await user.click(within(tile).getByRole("button", { name: "Aksi tile" }));
    await user.click(await screen.findByRole("menuitem", { name: /Pindah ke kiri/ }));
    await waitFor(() => expect(commands).toHaveLength(1));
    expect(commands[0]).toEqual({ type: "apply_canvas_patch", patch: "row = HContainer([kpi, tbl, trend], [3, 3, 6])" });

    const trend = screen.getByText("Per wilayah").closest("[data-tile]") as HTMLElement;
    await user.click(within(trend).getByRole("button", { name: "Aksi tile" }));
    await user.click(await screen.findByRole("menuitem", { name: /Ubah ke Line/ }));
    await waitFor(() => expect(commands).toHaveLength(2));
    expect(commands[1]).toEqual({
      type: "apply_canvas_patch",
      patch: 'trend = LineChart("Per wilayah", q_m, "region", ["revenue"], {crossFilter: "region"})',
    });

    await user.click(within(trend).getByRole("button", { name: "Aksi tile" }));
    await user.click(await screen.findByRole("menuitem", { name: /Hapus/ }));
    await user.click(await screen.findByRole("button", { name: "Hapus" }));
    await waitFor(() => expect(commands).toHaveLength(3));
    expect(commands[2]).toEqual({ type: "apply_canvas_patch", patch: "row = HContainer([kpi, tbl], [3, 3])" });
  });
});

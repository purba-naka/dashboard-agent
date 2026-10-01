import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DashboardSnapshot, RenderResponse } from "@/lib/types";
import { Canvas } from "./Canvas";
import type { CanvasClient } from "./client";

const echartsRenders = vi.hoisted(() => ({ count: 0 }));

vi.mock("react-grid-layout", () => ({
  useContainerWidth: () => ({ containerRef: { current: null }, width: 800 }),
  GridLayout: (props: { children?: React.ReactNode }) => <div>{props.children}</div>,
}));
vi.mock("echarts-for-react", () => ({
  default: () => {
    echartsRenders.count++;
    return <div data-testid="echarts" />;
  },
}));

afterEach(cleanup);

const N = 8;
const ids = Array.from({ length: N }, (_, i) => `ch_${i}`);
const snapshot: DashboardSnapshot = {
  id: "db",
  title: "D",
  version: 1,
  content: {
    title: "D",
    items: Object.fromEntries(
      ids.map((id) => [
        id,
        {
          id,
          kind: "chart",
          title: id,
          spec: { spec_version: 1, query_id: "q", chart_type: "bar", option: {}, cross_filter_column: null },
        },
      ]),
    ),
    layout: Object.fromEntries(ids.map((id, i) => [id, { x: 0, y: i * 4, w: 6, h: 4 }])),
    global_filters: [],
  },
  can_undo: false,
  can_redo: false,
  item_status: {},
};
const response: RenderResponse = {
  version: 1,
  items: Object.fromEntries(ids.map((id) => [id, { option: { series: [] }, status: "ok", filter_unaffected: false }])),
};
const client: CanvasClient = { render: vi.fn(async () => response), command: vi.fn() };

describe("Canvas perf", () => {
  it("re-render parent tanpa perubahan data tidak merender ulang chart", async () => {
    const props = { snapshot, crossFilters: [], client, datasetVersions: {}, datasets: [] };
    const { rerender } = render(<Canvas {...props} />);
    await screen.findAllByTestId("echarts");

    echartsRenders.count = 0;
    // Simulasi parent re-render (mis. tiap text.delta chat): callback baru, data sama.
    for (let i = 0; i < 10; i++) {
      // Sama seperti WorkspaceStudio: objek datasetVersions/datasets baru tiap render.
      act(() =>
        rerender(
          <Canvas {...props} datasetVersions={{}} datasets={[]} onCrossFiltersChange={() => {}} />,
        ),
      );
    }
    console.log(`[perf] chart renders after 10 parent rerenders: ${echartsRenders.count}`);
    expect(echartsRenders.count).toBe(0);
  });
});

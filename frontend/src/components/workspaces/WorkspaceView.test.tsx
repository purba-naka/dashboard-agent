import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Dataset } from "@/lib/types";
import { WorkspaceView } from "./WorkspaceView";
import { createMemoryClient, makeWorkspace } from "./memoryClient.test-util";

afterEach(cleanup);

const dataset: Dataset = {
  id: "ds_1",
  workspace_id: "ws_a",
  owner_id: "local",
  upload_id: "up_1",
  table_name: "orders",
  source_name: "orders.csv",
  sheet_name: null,
  schema: [
    { name: "order_id", type: "integer" },
    { name: "amount", type: "float" },
  ],
  column_mapping: [],
  row_count: 1200,
  data_version: 1,
  data_updated_at: "2024-01-31T10:00:00Z",
  privacy_no_samples: false,
  created_at: "2024-01-31T10:00:00Z",
};

describe("WorkspaceView", () => {
  it("menampilkan daftar Dataset, Dashboard, dan sesi chat Workspace (Req 1.2)", async () => {
    const { client } = createMemoryClient([makeWorkspace("ws_a", "Penjualan")], {
      ws_a: {
        datasets: [dataset],
        dashboards: [
          {
            id: "db_1",
            workspace_id: "ws_a",
            title: "Ringkasan",
            version: 3,
            created_at: "2024-01-31T10:00:00Z",
            updated_at: "2024-02-01T10:00:00Z",
          },
        ],
        chat_sessions: [
          {
            id: "s_1",
            workspace_id: "ws_a",
            title: "Analisis awal",
            created_at: "2024-01-31T10:00:00Z",
            last_agent_version: 3,
          },
        ],
      },
    });
    render(<WorkspaceView workspaceId="ws_a" client={client} />);

    expect(await screen.findByRole("heading", { level: 1, name: "Penjualan" })).toBeTruthy();

    const datasets = screen.getByRole("region", { name: /Dataset/ });
    expect(within(datasets).getByText("orders")).toBeTruthy();
    expect(within(datasets).getByText(/orders\.csv/).textContent).toMatch(/2 kolom/);

    const dashboards = screen.getByRole("region", { name: /Dashboard/ });
    expect(within(dashboards).getByText("Ringkasan")).toBeTruthy();
    expect(within(dashboards).getByText(/v3/)).toBeTruthy();

    const sessions = screen.getByRole("region", { name: /Sesi chat/ });
    expect(within(sessions).getByText("Analisis awal")).toBeTruthy();

    // Slot untuk komponen task berikutnya tersedia.
    for (const slot of ["datasets", "relations", "filters", "canvas", "chat", "export"]) {
      expect(document.querySelector(`[data-slot="${slot}"]`)).not.toBeNull();
    }
  });

  it("memanggil onSelectSession saat sesi chat lama diklik", async () => {
    const { client } = createMemoryClient([makeWorkspace("ws_a", "Penjualan")], {
      ws_a: {
        chat_sessions: [
          {
            id: "s_1",
            workspace_id: "ws_a",
            title: "Analisis awal",
            created_at: "2024-01-31T10:00:00Z",
            last_agent_version: 3,
          },
        ],
      },
    });
    const onSelectSession = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceView workspaceId="ws_a" client={client} onSelectSession={onSelectSession} />,
    );

    await user.click(await screen.findByText("Analisis awal"));
    expect(onSelectSession).toHaveBeenCalledWith("s_1");
  });

  it("menampilkan state kosong untuk Workspace baru", async () => {
    const { client } = createMemoryClient([makeWorkspace("ws_a", "Kosong")]);
    render(<WorkspaceView workspaceId="ws_a" client={client} />);

    expect(await screen.findByText(/Belum ada Dataset/)).toBeTruthy();
    expect(screen.getByText("Belum ada Dashboard.")).toBeTruthy();
    expect(screen.getByText("Belum ada sesi chat.")).toBeTruthy();
  });

  it("menampilkan pesan tidak ditemukan untuk Workspace yang tidak ada", async () => {
    const { client } = createMemoryClient();
    render(<WorkspaceView workspaceId="ws_x" client={client} />);

    expect((await screen.findByRole("alert")).textContent).toMatch(/tidak ditemukan/);
    expect(screen.getByRole("link", { name: /Semua Workspace/ }).getAttribute("href")).toBe("/");
  });
});

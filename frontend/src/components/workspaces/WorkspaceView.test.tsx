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

  it("panel data dan asisten bisa ditutup/dibuka; lebar asisten diatur keyboard dalam batas", async () => {
    window.localStorage.clear();
    const { client } = createMemoryClient([makeWorkspace("ws_a", "Penjualan")]);
    const user = userEvent.setup();
    render(<WorkspaceView workspaceId="ws_a" client={client} />);
    await screen.findByRole("heading", { level: 1, name: "Penjualan" });

    const sidebar = document.getElementById("workspace-sidebar")!;
    const chat = document.getElementById("workspace-chat")!;
    expect(sidebar.hidden).toBe(false);
    expect(chat.hidden).toBe(false);

    await user.click(screen.getByRole("button", { name: "Sembunyikan panel data" }));
    expect(sidebar.hidden).toBe(true);
    await user.click(screen.getByRole("button", { name: "Tutup asisten" }));
    expect(chat.hidden).toBe(true);
    expect(window.localStorage.getItem("studio.chat")).toBe("0");
    await user.click(screen.getByRole("button", { name: "Tampilkan asisten" }));
    expect(chat.hidden).toBe(false);

    const handle = screen.getByRole("separator", { name: "Ubah lebar panel asisten" });
    handle.focus();
    for (let i = 0; i < 20; i++) await user.keyboard("{ArrowLeft}");
    expect(handle.getAttribute("aria-valuenow")).toBe("560");
    for (let i = 0; i < 40; i++) await user.keyboard("{ArrowRight}");
    expect(handle.getAttribute("aria-valuenow")).toBe("320");

    // Sidebar di kiri: panah kanan melebarkan; lebar tersimpan untuk reload.
    await user.click(screen.getByRole("button", { name: "Tampilkan panel data" }));
    const side = screen.getByRole("separator", { name: "Ubah lebar panel data" });
    side.focus();
    await user.keyboard("{ArrowRight}");
    expect(side.getAttribute("aria-valuenow")).toBe("276");
    expect(window.localStorage.getItem("studio.sidebarWidth")).toBe("276");
  }, 15_000); // banyak interaksi keyboard; default 5 s terlalu ketat saat suite penuh paralel

  it("menampilkan pesan tidak ditemukan untuk Workspace yang tidak ada", async () => {
    const { client } = createMemoryClient();
    render(<WorkspaceView workspaceId="ws_x" client={client} />);

    expect((await screen.findByRole("alert")).textContent).toMatch(/tidak ditemukan/);
    expect(screen.getByRole("link", { name: /Semua Workspace/ }).getAttribute("href")).toBe("/");
  });
});

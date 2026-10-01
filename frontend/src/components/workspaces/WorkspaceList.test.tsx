import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { WorkspaceList } from "./WorkspaceList";
import { validateWorkspaceName } from "./client";
import { createMemoryClient, makeWorkspace } from "./memoryClient.test-util";

afterEach(cleanup);

describe("validateWorkspaceName", () => {
  it("menolak nama kosong/whitespace dan nama > 200 karakter", () => {
    expect(validateWorkspaceName("")).toMatch(/kosong/);
    expect(validateWorkspaceName("   ")).toMatch(/kosong/);
    expect(validateWorkspaceName("a".repeat(201))).toMatch(/200/);
    expect(validateWorkspaceName("  Penjualan  ")).toBeNull();
    expect(validateWorkspaceName("a".repeat(200))).toBeNull();
  });
});

describe("WorkspaceList", () => {
  it("menampilkan state kosong lalu membuat Workspace baru (Req 1.1)", async () => {
    const user = userEvent.setup();
    const { client, store } = createMemoryClient();
    render(<WorkspaceList client={client} />);

    expect(await screen.findByText(/Belum ada Workspace/)).toBeTruthy();

    await user.type(screen.getByLabelText("Nama Workspace baru"), "  Penjualan 2024  ");
    await user.click(screen.getByRole("button", { name: "Buat Workspace" }));

    const list = await screen.findByRole("list", { name: "Daftar Workspace" });
    expect(within(list).getByRole("link", { name: "Penjualan 2024" }).getAttribute("href")).toBe(
      "/w/ws_1",
    );
    expect([...store.values()].map((w) => w.name)).toEqual(["Penjualan 2024"]);
    expect((screen.getByLabelText("Nama Workspace baru") as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("status").textContent).toMatch(/dibuat/);
  });

  it("tidak memanggil backend untuk nama kosong dan menampilkan error", async () => {
    const user = userEvent.setup();
    const { client, store } = createMemoryClient();
    render(<WorkspaceList client={client} />);
    await screen.findByText(/Belum ada Workspace/);

    await user.type(screen.getByLabelText("Nama Workspace baru"), "   ");
    await user.click(screen.getByRole("button", { name: "Buat Workspace" }));

    expect(screen.getByRole("alert").textContent).toMatch(/tidak boleh kosong/);
    expect(store.size).toBe(0);
  });

  it("mengganti nama Workspace dan menampilkan nama baru (Req 1.3)", async () => {
    const user = userEvent.setup();
    const { client, store } = createMemoryClient([makeWorkspace("ws_a", "Lama")]);
    render(<WorkspaceList client={client} />);

    await user.click(await screen.findByRole("button", { name: "Ganti nama Lama" }));
    const input = screen.getByLabelText("Nama baru untuk “Lama”");
    await user.clear(input);
    await user.type(input, "Baru");
    await user.click(screen.getByRole("button", { name: "Simpan" }));

    expect(await screen.findByRole("link", { name: "Baru" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Lama" })).toBeNull();
    expect(store.get("ws_a")?.name).toBe("Baru");
  });

  it("menghapus hanya setelah nama konfirmasi sama persis (Req 1.4)", async () => {
    const user = userEvent.setup();
    const { client, store, calls } = createMemoryClient([
      makeWorkspace("ws_a", "Hapus Saya"),
      makeWorkspace("ws_b", "Tetap"),
    ]);
    render(<WorkspaceList client={client} />);

    await user.click(await screen.findByRole("button", { name: "Hapus Hapus Saya" }));
    const dialog = screen.getByRole("alertdialog", { name: /Hapus Workspace/ });
    const input = within(dialog).getByLabelText("Nama Workspace untuk konfirmasi");
    const confirm = within(dialog).getByRole("button", { name: "Hapus permanen" });
    expect(document.activeElement).toBe(input);

    for (const wrong of ["hapus saya", "Hapus Saya ", "Tetap"]) {
      await user.clear(input);
      await user.type(input, wrong);
      expect((confirm as HTMLButtonElement).disabled).toBe(true);
    }
    expect(calls.remove).toEqual([]);

    await user.clear(input);
    await user.type(input, "Hapus Saya");
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
    await user.click(confirm);

    expect(await screen.findByText("Workspace “Hapus Saya” dihapus.")).toBeTruthy();
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.queryByRole("link", { name: "Hapus Saya" })).toBeNull();
    expect(screen.getByRole("link", { name: "Tetap" })).toBeTruthy();
    expect(calls.remove).toEqual([["ws_a", "Hapus Saya"]]);
    expect([...store.keys()]).toEqual(["ws_b"]);
  });

  it("membatalkan dialog hapus dengan Escape tanpa menghapus", async () => {
    const user = userEvent.setup();
    const { client, store } = createMemoryClient([makeWorkspace("ws_a", "A")]);
    render(<WorkspaceList client={client} />);

    await user.click(await screen.findByRole("button", { name: "Hapus A" }));
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(store.has("ws_a")).toBe(true);
  });

  it("menampilkan error muat dan dapat mencoba lagi", async () => {
    const user = userEvent.setup();
    const { client } = createMemoryClient([makeWorkspace("ws_a", "A")]);
    let fail = true;
    const flaky = {
      ...client,
      list: async () => {
        if (fail) throw new ApiError(0, "NETWORK_ERROR", "Tidak dapat terhubung ke server.");
        return client.list();
      },
    };
    render(<WorkspaceList client={flaky} />);

    expect((await screen.findByRole("alert")).textContent).toMatch(/Tidak dapat terhubung/);
    fail = false;
    await user.click(screen.getByRole("button", { name: "Coba lagi" }));
    expect(await screen.findByRole("link", { name: "A" })).toBeTruthy();
  });
});

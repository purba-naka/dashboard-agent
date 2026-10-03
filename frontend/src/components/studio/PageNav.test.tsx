import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DashboardSummary } from "@/lib/types";
import { PageNav } from "./PageNav";

afterEach(cleanup);

const pages = [
  { id: "d1", title: "Ringkasan" },
  { id: "d2", title: "Wilayah" },
] as unknown as DashboardSummary[];

describe("PageNav", () => {
  it("Hapus halaman lewat dialog konfirmasi, Batal tidak menghapus", async () => {
    const onDelete = vi.fn();
    render(
      <PageNav pages={pages} activeId="d1" onSelect={vi.fn()} onCreate={vi.fn()} onRename={vi.fn()} onDelete={onDelete} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Hapus" }));
    await userEvent.click(await screen.findByRole("button", { name: "Batal" }));
    expect(onDelete).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Hapus" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog.textContent).toMatch(/Ringkasan/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Hapus" }));
    expect(onDelete).toHaveBeenCalledWith("d1");
  });
});

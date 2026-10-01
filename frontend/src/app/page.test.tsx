import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "./page";

beforeEach(() => {
  // Backend tidak berjalan di test: `GET /api/workspaces` mengembalikan daftar kosong.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("HomePage", () => {
  it("merender judul aplikasi dan bagian daftar Workspace di jsdom", async () => {
    render(<HomePage />);
    expect(screen.getByRole("heading", { level: 1, name: "Dashboard Studio" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "Workspace" })).toBeTruthy();
    expect(await screen.findByText(/Belum ada Workspace/)).toBeTruthy();
  });
});

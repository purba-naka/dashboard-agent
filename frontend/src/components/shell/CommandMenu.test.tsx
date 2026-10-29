import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { CommandMenu } from "./CommandMenu";

const setTheme = vi.fn();
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light", setTheme }) }));

beforeAll(() => {
  // cmdk memakai API yang tidak ada di jsdom.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView ??= () => {};
});
afterEach(cleanup);

const pages = [
  { id: "d1", title: "Ringkasan" },
  { id: "d2", title: "Penjualan" },
];

function Harness(props: { onSelectPage?: (id: string) => void; onAskAssistant?: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      onKeyDown={(e) => {
        if (e.ctrlKey && e.key === "k") setOpen(true);
      }}
    >
      <button onClick={() => setOpen(true)}>buka</button>
      <CommandMenu
        open={open}
        onOpenChange={setOpen}
        pages={pages}
        onToggleSidebar={() => {}}
        onToggleChat={() => {}}
        sidebarOpen
        chatOpen
        {...props}
      />
    </div>
  );
}

describe("CommandMenu", () => {
  it("Ctrl+K membuka palette", async () => {
    const user = userEvent.setup();
    render(<Harness onSelectPage={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(screen.getByText("buka"));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    screen.getByText("buka").focus();
    await user.keyboard("{Control>}k{/Control}");
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("mengetik memfilter daftar", async () => {
    const user = userEvent.setup();
    render(<Harness onSelectPage={() => {}} />);
    await user.click(screen.getByText("buka"));
    await user.type(screen.getByRole("combobox"), "Penj");
    expect(screen.getByText("Penjualan")).toBeTruthy();
    expect(screen.queryByText("Ringkasan")).toBeNull();
  });

  it("memilih halaman memanggil callback dan menutup", async () => {
    const user = userEvent.setup();
    const onSelectPage = vi.fn();
    render(<Harness onSelectPage={onSelectPage} />);
    await user.click(screen.getByText("buka"));
    await user.click(screen.getByText("Penjualan"));
    expect(onSelectPage).toHaveBeenCalledWith("d2");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("\"Tanya: x\" memanggil onAskAssistant", async () => {
    const user = userEvent.setup();
    const onAskAssistant = vi.fn();
    render(<Harness onAskAssistant={onAskAssistant} />);
    await user.click(screen.getByText("buka"));
    await user.type(screen.getByRole("combobox"), "total omzet");
    await user.click(screen.getByText("Tanya: total omzet"));
    expect(onAskAssistant).toHaveBeenCalledWith("total omzet");
  });
});

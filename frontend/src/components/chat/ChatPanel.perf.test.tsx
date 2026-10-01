import { Profiler, act } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatPanel } from "./ChatPanel";
import type { ChatClient, HistoryEntry } from "./client";

afterEach(cleanup);

// Riwayat realistis: 40 jawaban agent berisi Markdown (list + tabel + kode).
const LONG_MD = Array.from({ length: 10 }, (_, i) =>
  `**Poin ${i}** naik \`12%\`\n\n- Jawa\n- Bali\n\n| region | revenue |\n|---|---|\n| Jawa | ${i}00 |\n| Bali | ${i}5 |`,
).join("\n\n");
const history: HistoryEntry[] = Array.from({ length: 40 }, (_, i) =>
  i % 2 ? { kind: "agent", agent: "root", text: LONG_MD } : { kind: "user", text: `pertanyaan ${i}` },
);

const client: ChatClient = {
  send: vi.fn(),
  stop: vi.fn(),
  history: vi.fn(async () => history),
  confirmRelation: vi.fn(),
  rejectRelation: vi.fn(),
};

describe("ChatPanel perf", () => {
  it("mengetik di input tidak merender ulang riwayat chat", async () => {
    let commitMs = 0;
    render(
      <Profiler id="chat" onRender={(_id, _phase, actual) => (commitMs += actual)}>
        <ChatPanel workspaceId="ws" sessionId="s" client={client} />
      </Profiler>,
    );
    await screen.findAllByText("Poin 0");

    commitMs = 0;
    const input = screen.getByLabelText("Pesan");
    const t0 = performance.now();
    for (let i = 0; i < 20; i++) {
      act(() => {
        fireEvent.change(input, { target: { value: "x".repeat(i + 1) } });
      });
    }
    const wall = performance.now() - t0;
    console.log(`[perf] 20 keystroke: wall=${wall.toFixed(0)}ms react=${commitMs.toFixed(0)}ms`);
    expect(commitMs / 20).toBeLessThan(5);
  });
});

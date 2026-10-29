"use client";

import { Command } from "cmdk";
import { useTheme } from "next-themes";
import { useState, type ReactNode } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

export interface CommandMenuPage {
  id: string;
  title: string;
}

export interface CommandMenuProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pages: readonly CommandMenuPage[];
  activePageId?: string | null;
  /** Tanpa ini grup "Halaman" disembunyikan. */
  onSelectPage?: (id: string) => void;
  onToggleSidebar: () => void;
  onToggleChat: () => void;
  sidebarOpen: boolean;
  chatOpen: boolean;
  /** Tanpa ini perintah "Tanya" disembunyikan. */
  onAskAssistant?: (text: string) => void;
}

const ITEM =
  "flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none select-none data-[selected=true]:bg-muted data-[selected=true]:text-foreground";
const GROUP = "**:[[cmdk-group-heading]]:section-label **:[[cmdk-group-heading]]:px-2 **:[[cmdk-group-heading]]:py-1.5";

/** Command palette: pindah halaman, ganti tema, buka/tutup panel, dan bertanya ke Asisten. */
export function CommandMenu({
  open,
  onOpenChange,
  pages,
  activePageId,
  onSelectPage,
  onToggleSidebar,
  onToggleChat,
  sidebarOpen,
  chatOpen,
  onAskAssistant,
}: CommandMenuProps) {
  const [search, setSearch] = useState("");
  const { resolvedTheme, setTheme } = useTheme();
  const question = search.trim();

  const run = (action: () => void) => () => {
    onOpenChange(false);
    action();
  };
  const handleOpenChange = (next: boolean) => {
    if (!next) setSearch("");
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        showCloseButton={false}
        className="top-[20%] max-w-[calc(100%-2rem)] translate-y-0 gap-0 overflow-hidden rounded-lg border bg-surface-3 p-0 shadow-(--shadow-lg) sm:max-w-xl"
      >
        <DialogTitle className="sr-only">Perintah</DialogTitle>
        <DialogDescription className="sr-only">
          Ketik untuk mencari halaman atau perintah, atau tulis pertanyaan untuk Asisten.
        </DialogDescription>
        <Command label="Perintah" loop>
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Cari atau jalankan perintah…"
            aria-label="Cari atau jalankan perintah"
            className="h-11 w-full border-b bg-transparent px-3 text-sm outline-none placeholder:text-muted-foreground"
          />
          <Command.List className="max-h-80 overflow-y-auto overscroll-contain p-1.5">
            <Command.Empty className="px-3 py-6 text-center text-[0.8125rem] text-muted-foreground">
              Tidak ada hasil.
            </Command.Empty>

            {onAskAssistant && question && (
              <Command.Group heading="Asisten" className={GROUP}>
                <Command.Item
                  forceMount
                  value={`tanya-asisten ${question}`}
                  className={ITEM}
                  onSelect={run(() => onAskAssistant(question))}
                >
                  <span className="truncate">Tanya: {question}</span>
                </Command.Item>
              </Command.Group>
            )}

            {onSelectPage && pages.length > 0 && (
              <Command.Group heading="Halaman" className={GROUP}>
                {pages.map((p) => (
                  <Command.Item
                    key={p.id}
                    value={`halaman ${p.title} ${p.id}`}
                    className={ITEM}
                    onSelect={run(() => onSelectPage(p.id))}
                  >
                    <span className="truncate">{p.title}</span>
                    {p.id === activePageId && <Hint>Aktif</Hint>}
                  </Command.Item>
                ))}
              </Command.Group>
            )}

            <Command.Group heading="Tampilan" className={GROUP}>
              <Command.Item
                value="ganti tema terang gelap"
                className={ITEM}
                onSelect={run(() => setTheme(resolvedTheme === "dark" ? "light" : "dark"))}
              >
                {resolvedTheme === "dark" ? "Ganti ke tema terang" : "Ganti ke tema gelap"}
              </Command.Item>
              <Command.Item value="panel asisten chat" className={ITEM} onSelect={run(onToggleChat)}>
                {chatOpen ? "Tutup panel Asisten" : "Buka panel Asisten"}
              </Command.Item>
              <Command.Item value="panel data sidebar" className={ITEM} onSelect={run(onToggleSidebar)}>
                {sidebarOpen ? "Tutup panel data" : "Buka panel data"}
              </Command.Item>
            </Command.Group>
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

function Hint({ children }: { children: ReactNode }) {
  return <span className="ml-auto text-xs text-muted-foreground">{children}</span>;
}

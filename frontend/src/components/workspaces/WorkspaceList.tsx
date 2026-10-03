"use client";

import { useCallback, useEffect, useId, useState } from "react";
import { isAbortError } from "@/lib/api";
import type { WorkspaceSummary } from "@/lib/types";
import { CreateWorkspaceForm } from "./CreateWorkspaceForm";
import { DeleteWorkspaceDialog } from "./DeleteWorkspaceDialog";
import { WorkspaceRow } from "./WorkspaceRow";
import { defaultWorkspaceClient, errorMessage, type WorkspaceClient } from "./client";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const TILE_GRID = "grid list-none grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-3";

export interface WorkspaceListProps {
  client?: WorkspaceClient;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; workspaces: WorkspaceSummary[] };

export type WorkspaceSort = "activity" | "name" | "datasets";

const SORT_LABELS: Record<WorkspaceSort, string> = {
  activity: "Aktivitas terakhir",
  name: "Nama",
  datasets: "Jumlah dataset",
};

const collator = new Intl.Collator("id-ID", { sensitivity: "base", numeric: true });

// ponytail: filter & urut di browser, cukup sampai ratusan Workspace.
// Pindah ke query param `GET /workspaces` bila daftar jadi ribuan.
export function filterAndSort(
  list: WorkspaceSummary[],
  query: string,
  sort: WorkspaceSort,
): WorkspaceSummary[] {
  const q = query.trim().toLocaleLowerCase("id-ID");
  const filtered = q ? list.filter((w) => w.name.toLocaleLowerCase("id-ID").includes(q)) : list;
  const byName = (a: WorkspaceSummary, b: WorkspaceSummary) => collator.compare(a.name, b.name);
  const compare: Record<WorkspaceSort, (a: WorkspaceSummary, b: WorkspaceSummary) => number> = {
    activity: (a, b) => b.last_activity_at.localeCompare(a.last_activity_at) || byName(a, b),
    name: byName,
    datasets: (a, b) => b.dataset_count - a.dataset_count || byName(a, b),
  };
  return [...filtered].sort(compare[sort]);
}

/** Daftar Workspace: cari, urutkan, buat, ganti nama, dan hapus (Req 1.1, 1.3, 1.4). */
export function WorkspaceList({ client = defaultWorkspaceClient }: WorkspaceListProps) {
  const searchId = useId();
  const sortId = useId();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [deleting, setDeleting] = useState<WorkspaceSummary | null>(null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<WorkspaceSort>("activity");
  const [status, setStatus] = useState("");

  useEffect(() => {
    const ctrl = new AbortController();
    client
      .list({ signal: ctrl.signal })
      .then((workspaces) => setState({ kind: "ready", workspaces }))
      .catch((err: unknown) => {
        if (!isAbortError(err)) setState({ kind: "error", message: errorMessage(err) });
      });
    return () => ctrl.abort();
  }, [client, reloadKey]);

  const retry = useCallback(() => {
    setState({ kind: "loading" });
    setReloadKey((k) => k + 1);
  }, []);

  const update = (fn: (list: WorkspaceSummary[]) => WorkspaceSummary[]) =>
    setState((s) => (s.kind === "ready" ? { kind: "ready", workspaces: fn(s.workspaces) } : s));

  async function handleCreate(name: string) {
    const created = await client.create(name);
    const summary: WorkspaceSummary = {
      ...created,
      dataset_count: 0,
      dashboard_count: 0,
      last_activity_at: created.updated_at,
    };
    update((list) => [summary, ...list.filter((w) => w.id !== created.id)]);
    setCreating(false);
    setStatus(`Workspace “${created.name}” dibuat.`);
  }

  async function handleRename(id: string, name: string) {
    const renamed = await client.rename(id, name);
    update((list) =>
      list.map((w) =>
        w.id === renamed.id ? { ...w, ...renamed, last_activity_at: renamed.updated_at } : w,
      ),
    );
    setStatus(`Nama Workspace diganti menjadi “${renamed.name}”.`);
  }

  async function handleDelete(target: WorkspaceSummary, confirmName: string) {
    await client.remove(target.id, confirmName);
    update((list) => list.filter((w) => w.id !== target.id));
    setDeleting(null);
    setStatus(`Workspace “${target.name}” dihapus.`);
  }

  const all = state.kind === "ready" ? state.workspaces : [];
  const isEmpty = state.kind === "ready" && all.length === 0;
  const visible = filterAndSort(all, query, sort);
  const showForm = creating || isEmpty;

  return (
    <section aria-labelledby="workspace-list-heading" className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h2 id="workspace-list-heading" className="flex items-center gap-2">
          Workspace
          {all.length > 0 && (
            <Badge variant="secondary" className="tabular-nums">
              {all.length}
            </Badge>
          )}
        </h2>
        {!showForm && <Button onClick={() => setCreating(true)}>Workspace baru</Button>}
      </div>

      {showForm && (
        <div className="rounded-lg border bg-card p-4">
          <CreateWorkspaceForm
            onCreate={handleCreate}
            onCancel={isEmpty ? undefined : () => setCreating(false)}
          />
        </div>
      )}

      <p role="status" aria-live="polite" className="sr-only">
        {status}
      </p>

      {all.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <Label htmlFor={searchId} className="sr-only">
            Cari Workspace
          </Label>
          <Input
            id={searchId}
            type="search"
            className="min-w-64 flex-1"
            placeholder="Cari nama Workspace"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoComplete="off"
          />
          <div className="flex items-center gap-2">
            <Label htmlFor={sortId} className="text-muted-foreground">
              Urutkan
            </Label>
            <Select value={sort} onValueChange={(v) => setSort(v as WorkspaceSort)}>
              <SelectTrigger id={sortId} className="min-w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {Object.entries(SORT_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {state.kind === "loading" && (
        <div aria-busy="true" className={TILE_GRID}>
          <span className="sr-only">Memuat Workspace</span>
          <Skeleton className="h-25" />
          <Skeleton className="h-25" />
          <Skeleton className="h-25" />
        </div>
      )}

      {state.kind === "error" && (
        <Alert variant="destructive">
          <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
            <span>Gagal memuat Workspace: {state.message}</span>
            <Button variant="outline" size="sm" onClick={retry}>
              Coba lagi
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {isEmpty && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>Belum ada Workspace.</EmptyTitle>
            <EmptyDescription>
              Beri nama Workspace pertama, lalu unggah CSV atau XLSX di dalamnya.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}

      {all.length > 0 && visible.length === 0 && (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>Tidak ada Workspace yang cocok dengan “{query.trim()}”.</EmptyTitle>
          </EmptyHeader>
          <EmptyContent>
            <Button variant="outline" size="sm" onClick={() => setQuery("")}>
              Hapus pencarian
            </Button>
          </EmptyContent>
        </Empty>
      )}

      {visible.length > 0 && (
        <ul className={TILE_GRID} aria-label="Daftar Workspace">
          {visible.map((ws) => (
            <WorkspaceRow
              key={ws.id}
              workspace={ws}
              onRename={(name) => handleRename(ws.id, name)}
              onRequestDelete={() => setDeleting(ws)}
            />
          ))}
        </ul>
      )}

      {deleting && (
        <DeleteWorkspaceDialog
          workspace={deleting}
          onConfirm={(confirmName) => handleDelete(deleting, confirmName)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </section>
  );
}

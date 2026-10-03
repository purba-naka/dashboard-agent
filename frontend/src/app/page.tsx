import { WorkspaceList } from "@/components/workspaces/WorkspaceList";

// Route `/`: daftar Workspace. Buat, ganti nama, hapus (Req 1.1, 1.3, 1.4).
export default function HomePage() {
  return (
    <main className="mx-auto flex w-full max-w-[1120px] flex-col gap-8 px-4 pt-8 pb-12 md:px-6 md:pt-12 md:pb-16">
      <header className="flex flex-col gap-1.5">
        <h1>Dashboard Studio</h1>
        <p className="max-w-[60ch] text-muted-foreground">
          Unggah CSV atau XLSX ke Workspace, lalu minta agent menyusun dashboard dari data Anda.
        </p>
      </header>
      <WorkspaceList />
    </main>
  );
}

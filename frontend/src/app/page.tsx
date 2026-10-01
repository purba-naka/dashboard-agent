import { WorkspaceList } from "@/components/workspaces/WorkspaceList";
import styles from "@/components/workspaces/workspaces.module.css";

// Route `/`: daftar Workspace — buat, ganti nama, hapus (Req 1.1, 1.3, 1.4).
export default function HomePage() {
  return (
    <main className={styles.page}>
      <header className={styles.pageHeader}>
        <h1>Dashboard Studio</h1>
        <p className={styles.lede}>
          Unggah CSV atau XLSX ke Workspace, lalu minta agent menyusun dashboard dari data Anda.
        </p>
      </header>
      <WorkspaceList />
    </main>
  );
}

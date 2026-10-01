import { WorkspaceStudio } from "@/components/studio";

// Route `/w/[workspaceId]`: perangkaian akhir halaman Workspace (task 23.1) —
// Chat_Panel, Canvas_Editor, panel dataset/relasi, filter, dan ekspor dengan
// satu state store Dashboard yang diisi dari SSE workspace dan chat (Req 1.2).
export default async function WorkspacePage(props: PageProps<"/w/[workspaceId]">) {
  const { workspaceId } = await props.params;
  return <WorkspaceStudio workspaceId={workspaceId} />;
}

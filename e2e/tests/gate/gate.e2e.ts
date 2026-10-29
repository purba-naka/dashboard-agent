/**
 * Gate deterministik (plan F1). Tanpa penilaian LLM kecuali G3 (struktur saja).
 * Persiapan dashboard G4-G6 lewat REST tanpa LLM (seedDashboard).
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, test } from '@e2e-dev/web';
import { expect, type Screen } from 'e2e';
import * as api from '../../lib/api.ts';
import { readStream } from '../../lib/harness.ts';
import { E2E_ROOT, SEEDS_DIR } from '../../lib/paths.ts';
import { layoutProblems } from '../../lib/scorers.ts';
import { prepareWorkspace, seedDashboard } from '../../lib/workspace.ts';

const CHAT_INPUT = 'Tanyakan sesuatu tentang data Anda';
const uniq = (p: string) => `${p}-${Date.now().toString(36)}`;

/** `waitForDownload().path` relatif ke folder attempt; cari file terbaru di output run. */
function downloadedFile(rel: string): string {
  const root = join(process.env.E2E_RUN_DIR ?? join(E2E_ROOT, '.run'), 'e2e');
  const hits = (readdirSync(root, { recursive: true }) as string[])
    .map((p) => join(root, p))
    .filter((p) => p.replace(/\\/g, '/').endsWith(rel.replace(/\\/g, '/')));
  return hits.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)[0] ?? rel;
}

async function openStudio(browser: { goto(u: string, o?: object): Promise<unknown> }, screen: Screen, ws: string) {
  await browser.goto(`/w/${ws}`, { waitUntil: 'networkidle' });
  await expect(screen.getByPlaceholder(CHAT_INPUT)).toBeVisible({ timeout: 60_000 });
}

describe('gate', { timeout: 5 * 60_000, retries: 1, tags: ['gate'] }, () => {
  test('G1 buat workspace dan upload CSV + XLSX dari UI', { tags: ['G1'] }, async ({ app, screen, browser }) => {
    const name = uniq('gate-g1');
    await app.open('/');
    // Form tampil langsung bila belum ada Workspace; selain itu buka lewat tombol.
    const openForm = screen.getByRole('button', 'Workspace baru');
    if (await openForm.count()) await openForm.tap();
    await screen.getByLabel('Nama Workspace baru').fill(name);
    await screen.getByRole('button', 'Buat Workspace').tap();
    // Pembuatan menambah tile di daftar (tidak navigasi); buka lewat link-nya.
    await screen.getByRole('link', new RegExp(name)).first().tap();
    await browser.waitForURL(/\/w\/[^/]+/, { timeout: 30_000 });
    const ws = new URL(await browser.evaluate(() => location.href)).pathname.split('/')[2]!;
    await expect(screen.getByPlaceholder(CHAT_INPUT)).toBeVisible({ timeout: 60_000 });

    for (const file of ['toko.csv', 'produk.xlsx']) {
      await screen.getByLabel('File dataset').setInputFiles(join(SEEDS_DIR, file));
      await screen.getByRole('button', 'Unggah').tap();
      if (file.endsWith('.xlsx')) {
        // XLSX: backend mengembalikan daftar sheet, lalu pengguna memilih dan mengonversi.
        await expect(screen.getByRole('group', 'Pemilih sheet')).toBeVisible({ timeout: 30_000 });
        await screen.getByRole('button', 'Konversi sheet terpilih').tap();
      }
      await expect.poll(async () => (await api.listDatasets(ws)).map((d) => d.table_name), { timeout: 90_000 }).toContain(file.split('.')[0]!);
      // Input file disabled selama `uploading`; tunggu siap sebelum file berikutnya.
      await expect(screen.getByRole('button', 'Mengunggah')).toBeHidden({ timeout: 90_000 });
    }
    // Tabel sudah dicek lewat REST di atas; di UI cukup pastikan item daftar dataset tampil.
    await expect(screen.getByRole('button', /^produk\.xlsx.+Produk.+baris/).first()).toBeVisible({ timeout: 30_000 });
  });

  test('G2 konfirmasi relasi dari UI', { tags: ['G2'] }, async ({ screen, browser }) => {
    const ws = await prepareWorkspace(uniq('gate-g2'), 'seed', 'candidate');
    await openStudio(browser, screen, ws.id);
    expect(ws.relations.some((r) => r.status === 'candidate')).toBe(true);
    await screen.getByRole('tab', /^Relasi/).tap();
    await screen.getByRole('button', 'Konfirmasi', { exact: true }).first().tap();
    await expect.poll(async () => (await api.listRelations(ws.id)).filter((r) => r.status === 'confirmed').length, { timeout: 20_000 }).toBeGreaterThan(0);
  });

  test('G3 minta dashboard lalu setujui semua (struktur)', { tags: ['G3'], timeout: 15 * 60_000 }, async ({ screen, browser }) => {
    const ws = await prepareWorkspace(uniq('gate-g3'), 'seed', 'confirmed');
    await openStudio(browser, screen, ws.id);
    await screen.getByPlaceholder(CHAT_INPUT).fill('Buatkan dashboard penjualan sederhana');
    const firstTurn = browser.waitForResponse(/\/api\/workspaces\/[^/]+\/chat$/, { timeout: 10 * 60_000 });
    await screen.getByRole('button', 'Kirim').tap();
    await readStream(firstTurn); // giliran selesai (stream ditutup)
    // Agent harus mengusulkan dulu: kartu blueprint ("Setujui semua") atau kartu propose_changes
    // ("Setujui"). Jenis kartu adalah kualitas agent (dinilai scorecard), bukan struktur.
    // Satu balasan lanjutan diizinkan (sama dengan harness, plan R11).
    const approveAll = screen.getByRole('button', /^Setujui( semua)?$/);
    if ((await approveAll.count()) === 0) {
      const followUp = browser.waitForResponse(/\/api\/workspaces\/[^/]+\/chat$/, { timeout: 10 * 60_000 });
      await screen.getByPlaceholder(CHAT_INPUT).fill('Tolong usulkan rancangan dashboardnya dulu.');
      await screen.getByRole('button', 'Kirim').tap();
      await readStream(followUp);
    }
    await expect(approveAll.last()).toBeVisible({ timeout: 15_000 });
    const build = browser.waitForResponse(/\/api\/workspaces\/[^/]+\/chat$/, { timeout: 10 * 60_000 });
    await approveAll.last().tap();
    await readStream(build);
    const dash = await api.activeDashboard(ws.id);
    expect(Object.keys(dash?.content.items ?? {}).length).toBeGreaterThan(0);
    expect(layoutProblems(dash!)).toEqual([]);
    await expect(screen.getByRole('alert')).toBeHidden();
  });

  test('G4 cross-filter dari klik chart lalu hapus', { tags: ['G4'] }, async ({ screen, browser }) => {
    const ws = await prepareWorkspace(uniq('gate-g4'), 'seed', 'confirmed');
    await seedDashboard(ws.id);
    await openStudio(browser, screen, ws.id);
    const card = screen.getByRole('button', 'Pindah item Pendapatan per Wilayah');
    await expect(card).toBeVisible({ timeout: 30_000 });
    // Bar ECharts digambar di <canvas> tanpa node aksesibel. Sapu horizontal dekat dasar plot
    // (batang pertama tertinggi, urut DESC) sampai satu batang terkena dan Cross_Filter muncul.
    const canvas = browser.locator('canvas').first();
    await expect(canvas).toBeVisible({ timeout: 30_000 });
    const box = (await canvas.boundingBox())!;
    const chip = screen.getByRole('button', /^Hapus Cross_Filter/);
    for (let f = 0.12; f <= 0.45 && (await chip.count()) === 0; f += 0.03) {
      await screen.tapAt({ x: box.x + box.width * f, y: box.y + box.height * 0.85 });
      await new Promise((r) => setTimeout(r, 400));
    }
    await expect(chip.first()).toBeVisible({ timeout: 15_000 });
    await screen.getByRole('button', /^Hapus Cross_Filter/).first().tap();
    await expect(screen.getByRole('button', /^Hapus Cross_Filter/)).toBeHidden({ timeout: 15_000 });
  });

  test('G5 ekspor PNG dan PDF', { tags: ['G5'] }, async ({ screen, browser }) => {
    const ws = await prepareWorkspace(uniq('gate-g5'), 'seed', 'confirmed');
    await seedDashboard(ws.id);
    await openStudio(browser, screen, ws.id);
    await expect(screen.getByRole('button', 'Pindah item Pendapatan per Wilayah')).toBeVisible({ timeout: 30_000 });
    for (const label of ['Ekspor PNG', 'Ekspor PDF']) {
      // Split button Ekspor: kedua format ada di menu "Opsi ekspor".
      await screen.getByRole('button', 'Opsi ekspor').tap();
      const dl = await browser.waitForDownload(() => screen.getByRole('menuitem', label).tap(), { timeout: 60_000 });
      expect(dl.suggestedFilename).toMatch(label.endsWith('PNG') ? /\.png$/i : /\.pdf$/i);
      expect(readFileSync(downloadedFile(dl.path)).length).toBeGreaterThan(1000);
    }
  });

  test('G6 hapus manual lalu undo/redo', { tags: ['G6'] }, async ({ screen, browser }) => {
    const ws = await prepareWorkspace(uniq('gate-g6'), 'seed', 'confirmed');
    const { dashboardId } = await seedDashboard(ws.id);
    await openStudio(browser, screen, ws.id);
    await screen.getByRole('button', 'Aksi untuk Pesanan per Status').tap();
    await screen.getByRole('menuitem', 'Hapus').tap();
    await screen.getByRole('alertdialog').getByRole('button', 'Hapus').tap();
    await expect(screen.getByRole('button', 'Pindah item Pesanan per Status')).toBeHidden({ timeout: 15_000 });
    let snap = await api.getDashboard(dashboardId);
    expect(Object.keys(snap.content.items)).toHaveLength(1);

    await api.req('POST', `/dashboards/${dashboardId}/undo`, { base_version: snap.version });
    snap = await api.getDashboard(dashboardId);
    expect(Object.keys(snap.content.items)).toHaveLength(2);
    await expect(screen.getByRole('button', 'Pindah item Pesanan per Status')).toBeVisible({ timeout: 15_000 });

    await api.req('POST', `/dashboards/${dashboardId}/redo`, { base_version: snap.version });
    expect(Object.keys((await api.getDashboard(dashboardId)).content.items)).toHaveLength(1);
  });
});

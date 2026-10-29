import { test } from '@e2e-dev/web';
import { expect } from 'e2e';

// Plan C3: buktikan proxy meneruskan gambar + tool calls ke driver dan judge.
test('smoke: driver dan judge model menjawab', { tags: ['smoke'] }, async ({ app, agent, screen }) => {
  await app.open('/');
  await expect(screen.getByRole('heading', 'Dashboard Studio')).toBeVisible();
  await agent.assert('halaman menampilkan judul Dashboard Studio dan daftar atau ajakan membuat Workspace', { vision: true });
  await agent.act('buka form pembuatan Workspace baru');
  await expect(screen.getByRole('button', 'Buat Workspace')).toBeVisible();
});

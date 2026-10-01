"""Performance test data besar (task 23.3, marker ``slow`` — tidak berjalan default).

Skenario (Req 5.1, 5.2, 22.4):

1. Bangkitkan CSV sintetis 10 juta baris (> 100 MB) langsung ke ``tmp_path``.
2. Unggah + konversi ke Parquet lewat Ingestion_Service (streaming per batch)
   sambil mengambil sampel RSS proses; puncak pemakaian memori relatif baseline
   harus < 2× ukuran file (Req 5.2).
3. Render ulang chart (SQL agregasi + Global_Filter, tanpa LLM) atas Dataset
   10 juta baris harus selesai ≤ 5 detik (Req 22.4).

Jalankan eksplisit: ``pytest -m slow -q``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

from studio.app import create_app
from studio.config import Settings
from tests.integration.helpers import Studio, bar_spec, dataset_rows, running, wait_until

pytestmark = pytest.mark.slow

#: Jumlah baris CSV sintetis (Req 22.4: "hingga 10 juta baris").
N_ROWS = 10_000_000
#: Baris per blok tulis (juga dipakai membangkitkan id unik).
BLOCK = 100_000

REGIONS = ("R0", "R1", "R2", "R3", "R4")
CHANNELS = ("Web", "Shop", "App")


def _row(global_index: int) -> str:
    return (
        f"{global_index:08d},{REGIONS[global_index % 5]},{CHANNELS[global_index % 3]},"
        f"{global_index * 7919 % 1_000_000}\n"
    )


def _write_csv(path: Path) -> dict[str, int]:
    """Tulis CSV; kembalikan ekspektasi jumlah ``amount`` untuk channel Web."""
    web_sum = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("id,region,channel,amount\n")
        for block in range(N_ROWS // BLOCK):
            lines = [_row(block * BLOCK + i) for i in range(BLOCK)]
            fh.writelines(lines)
            web_sum += sum(
                amount
                for g, amount in (
                    (block * BLOCK + i, (block * BLOCK + i) * 7919 % 1_000_000)
                    for i in range(BLOCK)
                )
                if g % 3 == 0
            )
    return {"web_sum": web_sum}


class RssSampler:
    """Sampler RSS proses test di thread terpisah selama ingestion berjalan."""

    def __init__(self) -> None:
        self._proc = psutil.Process()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.baseline = 0
        self.peak = 0

    def _run(self) -> None:
        while not self._stop.is_set():
            rss = self._proc.memory_info().rss
            if rss > self.peak:
                self.peak = rss
            self._stop.wait(0.02)

    def __enter__(self) -> RssSampler:
        self.baseline = self._proc.memory_info().rss
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    @property
    def peak_delta(self) -> int:
        return max(0, self.peak - self.baseline)


def _chunks(path: Path, chunk_bytes: int = 1 << 20) -> Iterator[bytes]:
    """Berkas dibaca per 1 MiB (simulasi body upload streaming, tanpa buffering penuh)."""
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_bytes):
            yield chunk


async def test_ingestion_dan_render_data_besar(tmp_path: Path) -> None:
    csv_path = tmp_path / "big.csv"
    expected = _write_csv(csv_path)
    size = csv_path.stat().st_size
    assert size > 100 * 1024 * 1024, f"CSV {size / 1e6:.0f} MB belum > 100 MB"

    settings = Settings(
        data_dir=tmp_path / "data",
        query_runner_mode="inline",
        sse_heartbeat_seconds=0.2,
    )
    app = create_app(settings)

    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace()

        # --- Upload + konversi: RSS puncak < 2× ukuran file (Req 5.2) --------
        # Jendela pengukuran = upload streaming + konversi batch ke Parquet
        # (job berstatus ``done`` tepat setelah Dataset terdaftar, sebelum hook
        # profiling dijalankan), sesuai lingkup Req 5.2 pada Ingestion_Service.
        ingestion = app.state.ingestion
        with RssSampler() as sampler:
            upload = await ingestion.save_upload(ws, "big.csv", _chunks(csv_path))
            job_id = await ingestion.start_csv_job(ws, upload)

            async def probe() -> dict[str, object]:
                return ingestion.get_job(job_id)

            status = await wait_until(probe, lambda s: s["status"] in ("done", "failed"))
        assert status["status"] == "done", status
        peak_mb = sampler.peak_delta / 1e6
        print(f"file={size / 1e6:.0f} MB, peak RSS delta konversi={peak_mb:.0f} MB")
        assert sampler.peak_delta < 2 * size, (
            f"puncak RSS {peak_mb:.0f} MB >= 2x ukuran file {2 * size / 1e6:.0f} MB"
        )

        # Tunggu hook profiling + deteksi relasi selesai di dalam task job.
        await ingestion.wait_job(job_id, timeout=600)
        dataset_id = status["dataset_id"]

        detail = (await client.get(f"/api/workspaces/{ws}/datasets/{dataset_id}")).json()
        assert detail["dataset"]["row_count"] == N_ROWS
        assert detail["dataset"]["table_name"] == "big"

        # --- Dashboard + chart agregasi per region ----------------------------
        sql = "SELECT region, SUM(amount) AS revenue FROM big GROUP BY region ORDER BY revenue DESC"
        result = await studio.execute(ws, sql)
        dash = await studio.create_dashboard(ws)
        chart_id, version = await studio.add_chart(
            dash["id"], 0, "Revenue per Region", bar_spec(result.query_id, "region", "revenue")
        )

        # --- Global_Filter → render ulang ≤ 5 dtk (Req 22.4, tanpa LLM) --------
        web_filter = {"kind": "in", "table": "big", "column": "channel", "values": ["Web"]}
        version = (
            await studio.apply_ok(
                dash["id"], {"type": "set_global_filters", "filters": [web_filter]}, version
            )
        )["version"]
        assert version == 2

        started = time.perf_counter()
        rendered = await studio.render(dash["id"])
        elapsed = time.perf_counter() - started
        print(f"render 10 juta baris: {elapsed:.2f} s")
        assert elapsed <= 5.0, f"render {elapsed:.2f} s > 5 s"

        rows = dataset_rows(rendered["items"][chart_id]["option"])
        assert {r["region"] for r in rows} == set(REGIONS)
        assert sum(r["revenue"] for r in rows) == expected["web_sum"]

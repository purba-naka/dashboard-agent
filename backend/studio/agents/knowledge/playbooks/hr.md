---
topic: playbooks/hr
summary: Playbook dashboard HR untuk memantau headcount, rekrutmen, turnover, absensi, masa kerja, dan biaya gaji per departemen.
---

# Playbook HR

## Audiens umum

- Direksi / CHRO: ukuran organisasi, retensi, dan biaya tenaga kerja.
- HR business partner / manajer departemen: turnover, absensi, dan rekrutmen per unit.
- Grain lazim: bulan (snapshot akhir bulan); default tahun berjalan atau 12 bulan terakhir.

## Pertanyaan bisnis khas

1. Berapa jumlah karyawan aktif sekarang dan bagaimana trennya?
2. Berapa karyawan baru dan yang keluar tiap bulan?
3. Departemen mana dengan turnover tertinggi?
4. Apakah tingkat absensi meningkat, dan di unit mana?
5. Berapa lama waktu rekrut rata-rata per posisi?
6. Bagaimana komposisi karyawan per departemen, level, atau masa kerja?
7. Berapa biaya gaji dan bagaimana pertumbuhannya dibanding headcount?

## KPI inti (lihat kpi_catalog)

`headcount`, `new_hires`, `terminations`, `turnover_rate`, `absenteeism_rate`.
Pendukung: `avg_tenure_years`, `avg_time_to_hire_days`, `payroll_cost`,
`training_hours_per_employee`.

Arah: `turnover_rate`/`absenteeism_rate`/`avg_time_to_hire_days` `down`; `headcount`,
`new_hires`, `payroll_cost` `neutral`.

## Struktur dashboard yang disarankan

| Slot | Section | Visual | Isi |
|---|---|---|---|
| `kpi_headcount` | `kpi_row` | kpi | Headcount vs bulan lalu (`neutral`) |
| `kpi_hires` | `kpi_row` | kpi | Karyawan baru bulan ini vs bulan lalu |
| `kpi_turnover` | `kpi_row` | kpi | Turnover rate vs bulan lalu (`down`) |
| `kpi_absence` | `kpi_row` | kpi | Absensi vs bulan lalu (`down`) |
| `trend_hires_exits` | `trend` | bar + line | Hire dan exit per bulan (bar), headcount (line, `yAxisIndex: 1`) |
| `breakdown_dept_turnover` | `breakdown` | bar horizontal | Turnover per departemen, cross filter `department` |
| `composition_level` | `composition` | bar stack | Headcount per departemen ditumpuk per level/jabatan |
| `distribution_tenure` | `distribution` | bar | Histogram masa kerja (bin dibuat di SQL) |
| `distribution_absence` | `distribution` | heatmap | Absensi per departemen × bulan |

Total 9 item.

## Slicer umum

- Global_Filter: rentang tanggal, departemen/divisi, lokasi.
- Cross_Filter: `department`, `job_level`, `location`, `employment_type`.

## Jebakan umum

- Headcount adalah snapshot; jangan `SUM` lintas bulan. Ambil nilai akhir periode.
- Turnover rate memakai penyebut rata-rata headcount periode, bukan headcount akhir saja;
  nyatakan definisinya.
- Data sensitif (gaji, nama, NIK): tampilkan agregat, hindari detail per individu, dan
  sembunyikan grup sangat kecil yang bisa mengidentifikasi orang.
- Karyawan kontrak vs tetap tercampur tanpa penjelasan; tambahkan slicer `employment_type`.
- Pie untuk banyak departemen; pakai bar horizontal.

# RADsoftBOT (ExperimentBot)

Bot Discord untuk merekap **Opening** dan **Closing** harian dari channel dan thread yang terdaftar, dilengkapi web panel admin dan database.

---

## Fitur

- **Rekap otomatis** setiap hari pada jam `recap_hour:recap_minute` WIB (default 23:59). File Excel per channel/thread dikirim ke channel/thread asalnya, dan file gabungan dikirim ke `destination_channel`.
- **Web panel dengan login admin** di http://127.0.0.1:8080. Admin dibuat lewat CLI (tidak ada halaman register). Dilindungi CSRF dan rate limit login.
- **Simpan ke database**: setiap rekap (dari scheduler maupun panel) disimpan per source per tanggal.
- **Rewrite + riwayat revisi**: rekap ulang tanggal yang sama menimpa datanya; versi lama disimpan sebagai revisi dan bisa dibandingkan dengan data sekarang di panel.
- **Export Excel dari database** dengan kolom yang sama seperti Excel yang dikirim ke Discord.
- **Pemetaan tim**: setiap channel/thread bisa dipetakan ke tim Produk, Implementor, atau Development, lalu difilter di halaman riwayat.
- **Backup & restore** database lewat skrip.

---

## Setup lokal (Windows)

Semua perintah dijalankan dari **folder proyek** di PowerShell.

**1. Buat dan aktifkan virtual environment**

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

> Kalau muncul error *running scripts is disabled*, jalankan sekali:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**2. Install dependency**

```powershell
pip install -r requirements.txt
```

**3. Salin file konfigurasi**

```powershell
Copy-Item .env.example .env
Copy-Item appsettings.example.json appsettings.json
Copy-Item user.example.json user.json
```

**4. Isi `.env`**

- `DISCORD_TOKEN`: token bot dari Discord Developer Portal.
- `SECRET_KEY`: kunci rahasia session panel. Buat dengan:

  ```powershell
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

Variabel lain boleh dibiarkan default (lihat [Variabel `.env`](#variabel-env)).

**5. Isi `appsettings.json` dan `user.json`**

```json
{
    "channel_ids": [123456789012345678],
    "thread_ids": [111122223333444455],
    "destination_channel": 100200300400500600,
    "recap_hour": 23,
    "recap_minute": 59
}
```

| Key | Keterangan |
|---|---|
| `channel_ids` | ID channel yang direkap |
| `thread_ids` | ID thread yang direkap |
| `destination_channel` | ID channel tujuan file rekap gabungan |
| `recap_hour`, `recap_minute` | Jam rekap otomatis (WIB, format 24 jam) |

`user.json` memetakan Discord user ID ke nama karyawan. Hanya user yang terdaftar di sini yang direkap.

```json
{
    "123456789012345678": "NAMA KARYAWAN"
}
```

> `bot_token` di `appsettings.json` masih dibaca sebagai cadangan kalau `DISCORD_TOKEN` kosong, tapi sebaiknya token hanya disimpan di `.env`.

**6. Buat database**

```powershell
alembic upgrade head
```

File `radsoftbot.db` dibuat di folder proyek (lokasinya mengikuti `DATABASE_URL`). Jalankan perintah ini lagi setiap kali ada migrasi baru.

**7. Buat admin pertama**

```powershell
flask --app web.server create-admin
```

Masukkan username (3–64 karakter: huruf, angka, `.`, `_`, `-`) dan password (minimal 8 karakter, diketik dua kali).

**8. Jalankan bot + panel**

```powershell
python bot.py
```

Buka http://127.0.0.1:8080 lalu login.

### Catatan Linux

Langkahnya sama, dengan perbedaan:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp appsettings.example.json appsettings.json
cp user.example.json user.json
```

Path di contoh perintah memakai `/` (mis. `python scripts/backup_db.py`).

---

## Variabel `.env`

| Variabel | Default | Keterangan |
|---|---|---|
| `DISCORD_TOKEN` | *(kosong)* | **Wajib.** Token bot Discord. Kalau kosong, dicoba `bot_token` di `appsettings.json`. |
| `SECRET_KEY` | *(kosong)* | **Wajib.** Kunci rahasia session dan CSRF panel. Bot tidak mau jalan tanpa ini. |
| `WEB_HOST` | `127.0.0.1` | Alamat panel. Default hanya bisa diakses dari komputer ini. |
| `WEB_PORT` | `8080` | Port panel. |
| `SESSION_COOKIE_SECURE` | `false` | `true` hanya kalau panel diakses lewat HTTPS. |
| `LOGIN_RATE_LIMIT` | `5 per minute` | Batas percobaan login **gagal** per IP (format Flask-Limiter, mis. `10 per hour`). Login yang berhasil tidak dihitung. |
| `SESSION_LIFETIME_MINUTES` | *(kosong)* | Kosong = session berakhir saat browser ditutup. Diisi angka N = session habis setelah N menit tanpa aktivitas. |
| `DATABASE_URL` | `sqlite:///radsoftbot.db` | Lokasi database. Path SQLite relatif dihitung dari folder proyek. |

---

## Cara kerja rekap & penyimpanan

- **Unit rekap = (source, tanggal WIB).** Source adalah satu channel atau thread. Setiap tanggal disimpan sebagai satu rekap berisi 4 flag per user: `opening`, `opening_late`, `closing`, `closing_late`.
- **Pemicu:** scheduler (`triggered_by = scheduler`) atau panel / `POST /recap` (`triggered_by = panel:<username>`).
- **Rekap ulang (rewrite):** menjalankan rekap lagi untuk tanggal yang sama **menimpa** data tanggal itu, tidak menduplikasi. Kalau isinya berbeda, versi lama disimpan dulu sebagai revisi.
- **Status per tanggal** (ditampilkan di response `POST /recap` dan log panel):

  | Status | Arti |
  |---|---|
  | `created` | Tanggal ini baru pertama kali disimpan. |
  | `updated` | Ada flag yang berubah. Versi lama disimpan sebagai revisi, `revision_count` naik. |
  | `unchanged` | Flag sama persis. Hanya waktu dan pemicu terakhir yang diperbarui (perubahan nama saja juga masuk sini). |
  | `skipped` | Tanggal setelah hari ini (WIB). Tidak disimpan, tapi Excel tetap dikirim. |
  | `error` | Gagal menyimpan ke database. Detail ada di log; Excel tetap dikirim ke Discord. |

- **Tanggal tanpa pesan** tetap disimpan sebagai rekap dengan 0 entri. Jadi kalau pesan Opening/Closing dihapus lalu tanggal itu direkap ulang, datanya ikut kosong (dan versi lamanya tersimpan di revisi).
- **Rekap rentang tanggal** dari panel disimpan per tanggal; kegagalan satu tanggal tidak membatalkan tanggal lain.
- Rekap "hari ini" yang dijalankan siang hari akan ditimpa oleh rekap terjadwal malamnya.
- Kalau bot mati saat jadwal rekap, rekap hari itu **tidak** dijalankan susulan otomatis. Jalankan ulang dari panel.

### Halaman riwayat (panel → 02 RIWAYAT REKAP)

- Filter tanggal, rentang (maks. 366 hari), source, dan tim. Tanpa tanggal = 30 hari terakhir.
- Kolom: tanggal, source, tim, jumlah entri, jumlah revisi, terakhir oleh, diperbarui. Semua waktu dalam **WIB**.
- **DETAIL** menampilkan data sekarang dan setiap revisi (versi lama vs sekarang).
- **EXPORT EXCEL** mengunduh `.xlsx` dari database: sheet `Recap` (kolom sama dengan Excel Discord) dan sheet `Info` (filter, waktu export, metadata tiap rekap).

---

## Pemetaan tim (CLI)

Tim yang tersedia: **Produk**, **Implementor**, **Development** (huruf besar/kecil bebas).

```powershell
# lihat semua channel/thread (dari config dan database) beserta timnya
flask --app web.server list-sources

# petakan source ke tim (pakai DISCORD_ID dari list-sources)
flask --app web.server set-team 123456789012345678 produk

# hapus pemetaan
flask --app web.server clear-team 123456789012345678
```

- `set-team` hanya menerima ID yang ada di `channel_ids` / `thread_ids`.
- Source yang belum pernah direkap tetap bisa dipetakan. Namanya tampil sebagai `(belum direkap)` sampai rekap berikutnya mengisi nama aslinya.
- Setelah dipetakan, filter tim di halaman riwayat langsung bisa dipakai.

---

## Backup & restore

> Penjadwalan backup otomatis **belum ada**. Jalankan skrip backup secara manual (atau lewat Task Scheduler / cron kalau perlu).

**Backup** (aman walaupun bot sedang berjalan):

```powershell
python scripts\backup_db.py                  # backup + hapus backup lebih dari 14 hari
python scripts\backup_db.py --keep-days 30   # simpan 30 hari
python scripts\backup_db.py --dry-run        # lihat rencana tanpa mengubah apa pun
```

- Hasil: `backups\radsoftbot_YYYY-MM-DD_HHMM.db` (waktu WIB), satu file tanpa `-wal`/`-shm`.
- Setiap backup dicek dengan `PRAGMA integrity_check`. Kalau gagal, file dihapus dan skrip keluar dengan kode 2.
- Retensi hanya menghapus file di `backups\` yang namanya cocok pola backup. File lain tidak disentuh.
- Hanya untuk SQLite. Kalau `DATABASE_URL` bukan SQLite, skrip menolak dengan pesan yang jelas.

**Restore:**

```powershell
# 1. hentikan bot (Ctrl+C di terminal python bot.py)
# 2. restore
python scripts\restore_db.py backups\radsoftbot_2026-11-15_2130.db
# 3. kalau backup dari versi skema lebih lama
alembic upgrade head
# 4. jalankan bot lagi
python bot.py
```

Skrip restore:
1. memvalidasi file backup,
2. menolak kalau bot tampaknya masih berjalan,
3. meminta Anda mengetik `RESTORE` (bisa dilewati dengan `--yes`),
4. menyimpan salinan DB saat ini ke `backups\pre-restore_radsoftbot_….db` (tidak ikut terhapus retensi),
5. menghapus `-wal`/`-shm` lama lalu mengganti file DB.

---

## Menjalankan test

```powershell
pip install -r requirements-dev.txt
pytest
```

Test memakai database sementara dan data fiktif; tidak menyentuh `.env`, `appsettings.json`, `user.json`, atau `radsoftbot.db`.

Request contoh untuk VSCode REST Client ada di `bot.http` (urut dari login sampai logout).

---

## Struktur folder

```
ExperimentBot/
├── bot.py                      # entry point: bot Discord + web panel
├── config.py                   # baca .env, appsettings.json, user.json
├── alembic.ini
├── requirements.txt
├── requirements-dev.txt        # + pytest
├── pytest.ini
├── bot.http                    # request contoh (VSCode REST Client)
├── .env.example
├── appsettings.example.json
├── user.example.json
├── db/
│   ├── base.py                 # engine, session, pragma SQLite
│   ├── models.py               # tabel
│   └── url.py                  # resolusi DATABASE_URL
├── migrations/                 # Alembic
│   └── versions/
├── services/
│   ├── recap.py                # kumpulkan pesan, tulis Excel, kirim ke Discord
│   ├── recap_store.py          # simpan / rewrite + revisi
│   ├── recap_query.py          # riwayat, detail, export
│   ├── scheduler.py            # rekap terjadwal
│   └── source_admin.py         # pemetaan tim
├── web/
│   ├── server.py               # app Flask + route rekap
│   ├── auth.py                 # login, CSRF, rate limit, create-admin
│   ├── history.py              # API riwayat + export
│   ├── cli.py                  # list-sources, set-team, clear-team
│   ├── templates/login.html
│   └── static/                 # index.html, script.js, style.css, logo.jpg
├── scripts/
│   ├── backup_db.py
│   └── restore_db.py
├── tests/
└── docs/
    └── PROGRESS-database-login.md
```

---

## Catatan keamanan

- **Repo ini public.** Jangan pernah commit `.env`, `appsettings.json`, `user.json`, file database (`*.db`, `*.db-wal`, `*.db-shm`), atau folder `backups/`. Semuanya sudah ada di `.gitignore`. Cek dengan `git status` sebelum commit.
- Data di test, dokumentasi, dan contoh harus **fiktif**, bukan ID atau nama asli.
- Panel default hanya mendengarkan di `127.0.0.1` (localhost). Mengubah `WEB_HOST` ke `0.0.0.0` membuka panel ke jaringan, padahal saat ini panel memakai HTTP biasa tanpa enkripsi.
- Kalau nanti panel diakses lewat HTTPS, set `SESSION_COOKIE_SECURE=true`.
- Kalau token bot pernah tersebar, reset di Discord Developer Portal dan perbarui `DISCORD_TOKEN`.

---

## Troubleshooting

| Gejala | Penyebab & solusi |
|---|---|
| `DISCORD_TOKEN belum diisi. Isi di .env (lihat .env.example).` | `DISCORD_TOKEN` kosong. Isi di `.env`. |
| `SECRET_KEY belum diisi. Isi di .env (lihat .env.example).` | Buat dengan `python -c "import secrets; print(secrets.token_hex(32))"` lalu isi di `.env`. |
| `no such table: ...` saat `create-admin`, atau status `error` dengan `OperationalError` di hasil rekap | Database belum dimigrasi. Jalankan `alembic upgrade head`. |
| Panel tidak bisa dibuka / error port sudah dipakai saat `python bot.py` | Port 8080 dipakai aplikasi lain. Ganti `WEB_PORT` di `.env` (mis. `8081`) lalu buka `http://127.0.0.1:8081`. |
| Halaman login menampilkan *Terlalu banyak percobaan login* | Lebih dari 5 login gagal dalam 1 menit (`LOGIN_RATE_LIMIT`). Tunggu sebentar. Penghitung disimpan di memori, jadi restart bot juga mereset. |
| Lupa password admin | Buat admin baru dengan `flask --app web.server create-admin` (username berbeda). |
| `[config] appsettings.json tidak ditemukan` | Salin dari `appsettings.example.json` lalu isi. |

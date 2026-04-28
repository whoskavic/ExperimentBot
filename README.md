# ExperimentBot

Discord bot untuk rekap otomatis opening & closing harian dari channel dan thread yang terdaftar.

---

## Setup

**1. Buat virtual environment**
```bash
python -m venv venv
```

**2. Activate virtual environment**
```bash
# Windows
venv\Scripts\activate

# Linux / Mac
source venv/bin/activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Jalankan bot**
```bash
python bot.py
```

---

## Konfigurasi

### `appsettings.json`

```json
{
    "bot_token": "TOKEN_BOT_DISCORD",
    "channel_ids": [123456789012345678],
    "thread_ids": [111122223333444455],
    "destination_channel": 100200300400500600,
    "recap_hour": 23,
    "recap_minute": 59
}
```

| Key | Keterangan |
|---|---|
| `bot_token` | Token bot dari Discord Developer Portal |
| `channel_ids` | List ID channel yang akan direkap |
| `thread_ids` | List ID thread yang akan direkap |
| `destination_channel` | ID channel tujuan pengiriman hasil rekap otomatis |
| `recap_hour` | Jam pengiriman rekap otomatis (format 24 jam) |
| `recap_minute` | Menit pengiriman rekap otomatis |

### `user.json`

Mapping Discord ID ke nama karyawan. Digunakan agar hasil rekap menampilkan nama, bukan username Discord.

```json
{
    "299502622593777665": "FIKRI AVISHENA",
    "123456789012345678": "NAMA KARYAWAN"
}
```

---

## Fitur

- Rekap otomatis terjadwal setiap hari sesuai `recap_hour` dan `recap_minute`
- UI panel di `http://localhost:8080` untuk trigger rekap manual dengan pilihan tanggal
- Output file `.xlsx` per channel/thread + gabungan `all_channel`
- File lokal otomatis dihapus setelah dikirim ke Discord

---

## Struktur Folder

```
ExperimentBot/
├── bot.py
├── config.py
├── requirements.txt
├── appsettings.json
├── user.json
├── services/
│   ├── recap.py
│   └── scheduler.py
└── web/
    ├── server.py
    └── static/
        └── index.html
```
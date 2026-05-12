# FIRMS-Hotspot

Sistem otomatis pemantauan titik api (hotspot) dari data NASA FIRMS.  
Pipeline: **Gmail → n8n → GitHub Actions (overlay spasial) → n8n → WhatsApp (Fonnte/WAHA)**

## Struktur repo

```
FIRMS-Hotspot/
├── .github/
│   └── workflows/
│       └── overlay.yml        # GitHub Actions: jalankan overlay saat data baru masuk
├── data/
│   ├── hotspot.csv            # CSV terbaru dari FIRMS (di-push otomatis oleh n8n)
│   └── wilayah.geojson        # Polygon batas wilayah pemantauan
├── scripts/
│   └── overlay.py             # Script Python: spatial join titik × polygon
├── results/
│   └── result.json            # Output analisis (dikirim ke n8n webhook)
└── README.md
```

## Cara kerja

1. NASA FIRMS mengirim email dengan lampiran CSV hotspot
2. n8n menangkap email via Gmail Trigger, mengekstrak CSV, push ke `data/hotspot.csv`
3. n8n memicu GitHub Actions via `repository_dispatch`
4. GitHub Actions menjalankan `overlay.py`:
   - Load CSV titik hotspot
   - Load GeoJSON polygon wilayah
   - Spatial join: titik mana yang ada di wilayah mana
   - Simpan ringkasan ke `results/result.json`
5. GitHub Actions mengirim `result.json` ke n8n Webhook
6. n8n memformat pesan dan mengirim alert WhatsApp via Fonnte/WAHA

## Setup

### 1. GitHub Secrets yang dibutuhkan

Masuk ke **Settings → Secrets and variables → Actions**, tambahkan:

| Secret | Keterangan |
|--------|------------|
| `N8N_WEBHOOK_URL` | URL webhook n8n yang menerima hasil analisis |

### 2. Sesuaikan polygon wilayah

Edit `data/wilayah.geojson` sesuai wilayah pemantauanmu.  
Pastikan kolom nama wilayah bernama `NAMOBJ` (atau ubah `COL_NAMA_WILAYAH` di `overlay.py`).

### 3. Format kolom CSV FIRMS

Script mengharapkan kolom: `latitude`, `longitude`, `frp`, `acq_date`, `acq_time`, `confidence`.  
Ini adalah format standar FIRMS VIIRS/MODIS. Tidak perlu diubah.

## Output `result.json`

```json
{
  "generated_at": "2026-05-12T03:12:35Z",
  "total_titik": 42,
  "wilayah_terdampak": 3,
  "frp_tertinggi_global": 87.5,
  "ringkasan_per_wilayah": [
    {
      "wilayah": "Kab. Sigi",
      "jumlah_titik": 18,
      "frp_max": 87.5,
      "frp_rata": 34.2,
      "titik_terpanas": {
        "latitude": -1.234,
        "longitude": 119.876,
        "frp": 87.5,
        "tanggal": "2026-05-12",
        "jam": "0312",
        "confidence": "high"
      }
    }
  ]
}
```

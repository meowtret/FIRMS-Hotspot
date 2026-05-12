#!/usr/bin/env python3
"""
overlay.py — Analisis spasial hotspot FIRMS vs polygon wilayah
Dijalankan oleh GitHub Actions setiap kali ada data CSV baru dari n8n.

Input:
  - data/hotspot.csv     : titik hotspot dari FIRMS NASA
  - data/wilayah.geojson : polygon batas wilayah (kecamatan/kabupaten/dll)

Output:
  - results/result.json  : ringkasan hotspot per wilayah (dikirim ke n8n webhook)
"""

import os
import sys
import json
import datetime
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# ─── Konfigurasi path ──────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH   = os.path.join(BASE_DIR, "data", "hotspot.csv")
POLY_PATH  = os.path.join(BASE_DIR, "data", "wilayah.geojson")
OUT_PATH   = os.path.join(BASE_DIR, "results", "result.json")

# ─── Nama kolom FIRMS (VIIRS/MODIS) ───────────────────────────────────────────
# FIRMS CSV biasanya punya kolom: latitude, longitude, brightness/frp, acq_date, acq_time, confidence
COL_LAT    = "latitude"
COL_LON    = "longitude"
COL_FRP    = "frp"           # Fire Radiative Power (MW) — indikator intensitas
COL_DATE   = "acq_date"
COL_TIME   = "acq_time"
COL_CONF   = "confidence"    # low / nominal / high (VIIRS) atau angka 0-100 (MODIS)

# Nama kolom di GeoJSON yang berisi nama wilayah
COL_NAMA_WILAYAH = "NAMOBJ"  # sesuaikan dengan GeoJSON-mu (bisa: NAME, KECAMATAN, dll.)

def load_hotspot(path):
    """Baca CSV FIRMS, buat GeoDataFrame."""
    print(f"[1/4] Membaca CSV hotspot: {path}")
    df = pd.read_csv(path)
    print(f"      Ditemukan {len(df)} titik hotspot")

    # Buat kolom geometry dari lat/lon
    geometry = [Point(lon, lat) for lon, lat in zip(df[COL_LON], df[COL_LAT])]
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    return gdf

def load_polygon(path):
    """Baca GeoJSON polygon wilayah."""
    print(f"[2/4] Membaca polygon wilayah: {path}")
    poly = gpd.read_file(path)
    if poly.crs is None or poly.crs.to_epsg() != 4326:
        poly = poly.to_crs("EPSG:4326")
    print(f"      Ditemukan {len(poly)} polygon wilayah")
    return poly

def spatial_join(hotspot_gdf, poly_gdf):
    """Overlay: titik mana yang jatuh di dalam polygon mana."""
    print("[3/4] Menjalankan spatial join...")
    joined = gpd.sjoin(hotspot_gdf, poly_gdf[[COL_NAMA_WILAYAH, "geometry"]],
                       how="left", predicate="within")
    # Titik yang tidak masuk polygon manapun → wilayah = "Di luar area"
    joined[COL_NAMA_WILAYAH] = joined[COL_NAMA_WILAYAH].fillna("Di luar area pemantauan")
    print(f"      Spatial join selesai. {joined[COL_NAMA_WILAYAH].notna().sum()} titik diproses.")
    return joined

def build_result(joined):
    """Buat ringkasan per wilayah untuk dikirim ke n8n."""
    print("[4/4] Menyusun ringkasan hasil...")

    # ── Ringkasan per wilayah ────────────────────────────────
    summary = []
    for wilayah, group in joined.groupby(COL_NAMA_WILAYAH):
        frp_vals = group[COL_FRP].dropna() if COL_FRP in group.columns else pd.Series([])
        top_row   = group.loc[frp_vals.idxmax()] if len(frp_vals) > 0 else group.iloc[0]

        entry = {
            "wilayah"      : wilayah,
            "jumlah_titik" : int(len(group)),
            "frp_max"      : round(float(frp_vals.max()), 2) if len(frp_vals) > 0 else None,
            "frp_rata"     : round(float(frp_vals.mean()), 2) if len(frp_vals) > 0 else None,
            "titik_terpanas": {
                "latitude" : float(top_row[COL_LAT]),
                "longitude": float(top_row[COL_LON]),
                "frp"      : round(float(top_row[COL_FRP]), 2) if COL_FRP in top_row and pd.notna(top_row[COL_FRP]) else None,
                "tanggal"  : str(top_row[COL_DATE]) if COL_DATE in top_row else None,
                "jam"      : str(top_row[COL_TIME]) if COL_TIME in top_row else None,
                "confidence": str(top_row[COL_CONF]) if COL_CONF in top_row else None,
            }
        }
        summary.append(entry)

    # Urutkan dari yang paling banyak titik
    summary.sort(key=lambda x: x["jumlah_titik"], reverse=True)

    # ── Statistik global ─────────────────────────────────────
    total    = int(len(joined))
    frp_all  = joined[COL_FRP].dropna() if COL_FRP in joined.columns else pd.Series([])
    wilayah_terdampak = joined[joined[COL_NAMA_WILAYAH] != "Di luar area pemantauan"][COL_NAMA_WILAYAH].nunique()

    result = {
        "generated_at"       : datetime.datetime.utcnow().isoformat() + "Z",
        "total_titik"        : total,
        "wilayah_terdampak"  : int(wilayah_terdampak),
        "frp_tertinggi_global": round(float(frp_all.max()), 2) if len(frp_all) > 0 else None,
        "ringkasan_per_wilayah": summary,
    }
    return result

def main():
    # Validasi file input
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: File CSV tidak ditemukan: {CSV_PATH}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(POLY_PATH):
        print(f"ERROR: File GeoJSON tidak ditemukan: {POLY_PATH}", file=sys.stderr)
        sys.exit(1)

    hotspot = load_hotspot(CSV_PATH)
    polygon = load_polygon(POLY_PATH)
    joined  = spatial_join(hotspot, polygon)
    result  = build_result(joined)

    # Simpan ke results/result.json
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nHasil disimpan ke: {OUT_PATH}")
    print(f"Total titik    : {result['total_titik']}")
    print(f"Wilayah terdampak: {result['wilayah_terdampak']}")
    print(f"FRP tertinggi  : {result['frp_tertinggi_global']} MW")

    # Print JSON ke stdout juga (untuk debugging di Actions log)
    print("\n── Preview result.json ──")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:800] + "...")

if __name__ == "__main__":
    main()

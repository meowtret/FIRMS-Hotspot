import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely import wkb
from shapely.geometry import Point, shape


print("=== RUN HOTSPOT OVERLAY ===")


ROOT = Path(__file__).resolve().parents[1]
HOTSPOT_CSV = ROOT / os.getenv("HOTSPOT_CSV", "data/hotspot.csv")
WILAYAH_GEOJSON = ROOT / os.getenv("WILAYAH_GEOJSON", "data/wilayah.geojson")
RESULT_JSON = ROOT / os.getenv("RESULT_JSON", "results/result.json")
RESULT_CSV = ROOT / os.getenv("RESULT_CSV", "results/result.csv")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY", "")
SUPABASE_TABLE = os.getenv("SUPABASE_TABLE") or "kawasan_kph_simple"
SUPABASE_GEOM_COLUMN = os.getenv("SUPABASE_GEOM_COLUMN") or "geom"
SUPABASE_SELECT = os.getenv("SUPABASE_SELECT") or "id,pbph,kph,provinsi,fungsi,geom"

WITA = timezone(timedelta(hours=8))
TANGGAL_PENGAMATAN = datetime.now(WITA).strftime("%Y-%m-%d")

KABUPATEN_URLS = [
    "https://raw.githubusercontent.com/mahendrayudha/indonesia-geojson/main/Gorontalo/Kabupaten-Kota%20(Provinsi%20Gorontalo)/Kabupaten-Kota%20(Provinsi%20Gorontalo).geojson",
    "https://raw.githubusercontent.com/mahendrayudha/indonesia-geojson/main/Sulawesi%20Tengah/Kabupaten-Kota%20(Provinsi%20Sulawesi%20Tengah)/Kabupaten-Kota%20(Provinsi%20Sulawesi%20Tengah).geojson",
    "https://raw.githubusercontent.com/mahendrayudha/indonesia-geojson/main/Sulawesi%20Utara/Kabupaten-Kota%20(Provinsi%20Sulawesi%20Utara)/Kabupaten-Kota%20(Provinsi%20Sulawesi%20Utara).geojson",
]


def clean_sjoin(df):
    return df.drop(columns=[c for c in df.columns if "index_" in c], errors="ignore")


def map_conf(value):
    if isinstance(value, str):
        return {"h": "Tinggi", "n": "Sedang", "l": "Rendah"}.get(value.lower(), value)

    if pd.isna(value):
        return None

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value

    if numeric >= 80:
        return "Tinggi"
    if numeric >= 70:
        return "Sedang"
    return "Rendah"


def parse_geometry(raw_geometry):
    if not raw_geometry:
        return None

    if isinstance(raw_geometry, dict):
        return shape(raw_geometry)

    if isinstance(raw_geometry, str):
        stripped = raw_geometry.strip()

        if stripped.startswith("{"):
            return shape(json.loads(stripped))

        if stripped.startswith("\\x"):
            stripped = stripped[2:]

        return wkb.loads(stripped, hex=True)

    return None


def download_kawasan_supabase(chunk_size=1000):
    if not SUPABASE_URL or not SUPABASE_API_KEY:
        print("[INFO] Supabase secrets belum tersedia, pakai data/wilayah.geojson.")
        return None

    headers = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {SUPABASE_API_KEY}",
        "Accept": "application/json",
    }

    features = []
    offset = 0

    print(f"[INFO] Mengambil kawasan dari Supabase: {SUPABASE_TABLE}")

    while True:
        params = {
            "select": SUPABASE_SELECT,
            "offset": offset,
            "limit": chunk_size,
        }

        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}",
            headers=headers,
            params=params,
            timeout=60,
        )

        if response.status_code != 200:
            raise RuntimeError(f"Supabase error {response.status_code}: {response.text}")

        rows = response.json()
        if not rows:
            break

        for row in rows:
            row = dict(row)
            raw_geometry = row.pop(SUPABASE_GEOM_COLUMN, None)

            try:
                geometry = parse_geometry(raw_geometry)
            except Exception as exc:
                print(f"[WARN] Skip baris, gagal parse geometry: {exc}")
                continue

            if geometry is not None:
                features.append({"geometry": geometry, **row})

        print(f"[INFO] Diunduh {len(features)} fitur kawasan...")

        if len(rows) < chunk_size:
            break

        offset += chunk_size

    if not features:
        raise RuntimeError("Tidak ada polygon kawasan yang berhasil diunduh dari Supabase.")

    kawasan = gpd.GeoDataFrame(features, geometry="geometry", crs="EPSG:4326")
    kawasan = kawasan.to_crs("EPSG:4326")

    WILAYAH_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    kawasan.to_file(WILAYAH_GEOJSON, driver="GeoJSON")
    print(f"[INFO] Cache wilayah ditulis ke {WILAYAH_GEOJSON}")

    return kawasan


def load_kawasan():
    kawasan = download_kawasan_supabase()
    if kawasan is not None:
        return kawasan

    if not WILAYAH_GEOJSON.exists():
        raise FileNotFoundError(f"File polygon tidak ditemukan: {WILAYAH_GEOJSON}")

    kawasan = gpd.read_file(WILAYAH_GEOJSON)
    if kawasan.empty:
        raise RuntimeError(
            "Polygon wilayah kosong. Isi data/wilayah.geojson atau set Supabase secrets."
        )

    if kawasan.crs is None:
        kawasan = kawasan.set_crs("EPSG:4326")
    else:
        kawasan = kawasan.to_crs("EPSG:4326")

    return kawasan


def load_kabupaten():
    gdf_list = []

    for url in KABUPATEN_URLS:
        try:
            print(f"[INFO] Download batas kabupaten: {url}")
            gdf_list.append(gpd.read_file(url))
        except Exception as exc:
            print(f"[WARN] Gagal download kabupaten: {exc}")

    if not gdf_list:
        raise RuntimeError("Tidak ada data kabupaten yang berhasil diunduh.")

    kabupaten = gpd.GeoDataFrame(pd.concat(gdf_list, ignore_index=True), crs=gdf_list[0].crs)

    if kabupaten.crs is None:
        kabupaten = kabupaten.set_crs("EPSG:4326")
    else:
        kabupaten = kabupaten.to_crs("EPSG:4326")

    return kabupaten


def load_hotspot_csv():
    if not HOTSPOT_CSV.exists():
        raise FileNotFoundError(f"File hotspot tidak ditemukan: {HOTSPOT_CSV}")

    df = pd.read_csv(HOTSPOT_CSV)
    if df.empty:
        return df

    required_columns = {"latitude", "longitude"}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        raise ValueError(f"Kolom wajib tidak ada di hotspot.csv: {sorted(missing_columns)}")

    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"]).copy()

    if "confidence" in df.columns:
        df["Confidence"] = df["confidence"].apply(map_conf)

    if "acq_date" in df.columns:
        df["Tanggal"] = pd.to_datetime(df["acq_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    else:
        df["Tanggal"] = TANGGAL_PENGAMATAN

    if "Sumber_Satelit" not in df.columns:
        df["Sumber_Satelit"] = df.get("satellite", "FIRMS")

    return df


def add_admin_columns(joined):
    if "NAME_1" in joined.columns:
        joined["Provinsi_Final"] = joined["NAME_1"]
    elif "Propinsi" in joined.columns:
        joined["Provinsi_Final"] = joined["Propinsi"]
    elif "provinsi_right" in joined.columns:
        joined["Provinsi_Final"] = joined["provinsi_right"]
    else:
        joined["Provinsi_Final"] = None

    if "NAME_2" in joined.columns:
        joined["Kabupaten_Final"] = joined["NAME_2"]
    elif "Kabupaten" in joined.columns:
        joined["Kabupaten_Final"] = joined["Kabupaten"]
    elif "kabupaten" in joined.columns:
        joined["Kabupaten_Final"] = joined["kabupaten"]
    else:
        joined["Kabupaten_Final"] = None

    return joined


def build_payload(records):
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tanggal_pengamatan": TANGGAL_PENGAMATAN,
        "total_hotspot": len(records),
        "hotspots": records,
    }


def write_results(payload, df_output):
    RESULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    df_output.to_csv(RESULT_CSV, index=False)
    print(f"[INFO] Result JSON: {RESULT_JSON}")
    print(f"[INFO] Result CSV : {RESULT_CSV}")


def main():
    kawasan = load_kawasan()
    print(f"[INFO] Total polygon kawasan: {len(kawasan)}")

    hotspot = load_hotspot_csv()
    print(f"[INFO] Total hotspot awal: {len(hotspot)}")

    if hotspot.empty:
        payload = build_payload([])
        write_results(payload, pd.DataFrame())
        return

    hotspot_geometry = [Point(xy) for xy in zip(hotspot["longitude"], hotspot["latitude"])]
    hotspot_gdf = gpd.GeoDataFrame(hotspot, geometry=hotspot_geometry, crs="EPSG:4326")

    print("[INFO] Spatial join hotspot dengan kawasan...")
    join_kawasan = gpd.sjoin(hotspot_gdf, kawasan, how="inner", predicate="within")
    join_kawasan = clean_sjoin(join_kawasan)
    print(f"[INFO] Hotspot dalam kawasan: {len(join_kawasan)}")

    if join_kawasan.empty:
        payload = build_payload([])
        write_results(payload, pd.DataFrame())
        return

    kabupaten = load_kabupaten()

    print("[INFO] Spatial join hotspot dengan kabupaten...")
    join_kabupaten = gpd.sjoin(join_kawasan, kabupaten, how="left", predicate="intersects")
    join_kabupaten = clean_sjoin(join_kabupaten)
    join_kabupaten = add_admin_columns(join_kabupaten)

    sort_columns = [c for c in ["Provinsi_Final", "Kabupaten_Final", "Tanggal"] if c in join_kabupaten.columns]
    if sort_columns:
        join_kabupaten = join_kabupaten.sort_values(by=sort_columns, na_position="last")

    output_columns = [
        "latitude", "longitude", "Confidence", "Sumber_Satelit", "Tanggal",
        "Provinsi_Final", "Kabupaten_Final", "pbph", "kph", "provinsi", "fungsi",
    ]
    output_columns = [c for c in output_columns if c in join_kabupaten.columns]

    df_output = join_kabupaten[output_columns].copy()
    df_output = df_output.astype(object).where(pd.notnull(df_output), None)
    records = df_output.to_dict(orient="records")

    payload = build_payload(records)
    write_results(payload, df_output)


if __name__ == "__main__":
    main()

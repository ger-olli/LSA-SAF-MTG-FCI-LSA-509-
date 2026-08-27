
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import h5py
import numpy as np
import requests
from bs4 import BeautifulSoup
from pyproj import CRS, Transformer
from shapely.geometry import Point, Polygon

POLYGON = Polygon([
    (21.30252, 44.83812),
    (21.21291, 44.79014),
    (20.99648, 44.89789),
    (21.10188, 44.96886),
])

USERNAME = os.environ.get("LSASAF_USERNAME")
PASSWORD = os.environ.get("LSASAF_PASSWORD")
if not USERNAME or not PASSWORD:
    print("ERROR: LSASAF_USERNAME/LSASAF_PASSWORD not set", file=sys.stderr)
    sys.exit(2)

BASE = "https://datalsasaf.lsasvcs.ipma.pt/PRODUCTS/MTG/MTFRPPixel/"
STATE_PATH = Path("data/mtg_seen.json")
STATUS_PATH = Path("data/mtg_status.json")
EVENTS_PATH = Path("data/mtg_events.jsonl")
DOWNLOAD_DIR = Path("data/mtg_downloads")
CURSOR_PATH = Path("data/mtg_cursor.json")

session = requests.Session()
session.auth = (USERNAME, PASSWORD)
session.headers.update({"User-Agent": "mtg-frp-watch/1.0"})

def load_seen():
    if STATE_PATH.exists():
        try:
            return set(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    STATE_PATH.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")

def list_links(url):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return [a.get("href") for a in soup.find_all("a") if a.get("href")]

def latest_daily_directory():
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    # Reale LSA-SAF-Struktur:
    # /PRODUCTS/MTG/MTFRPPixel/NATIVE/YYYY/MM/DD/
    dates = [
        now,
        now - timedelta(days=1),
        now - timedelta(days=2),
    ]

    tried = []

    for d in dates:
        url = urljoin(
            BASE,
            f"NATIVE/{d.year:04d}/{d.month:02d}/{d.day:02d}/"
        )
        tried.append(url)

        try:
            links = list_links(url)
        except Exception:
            continue

        product_files = [
            x for x in links
            if re.search(r'\.(h5|hdf5|nc)(?:\.bz2)?$', x, re.I)
        ]

        if product_files:
            return url, links

    raise RuntimeError(
        "No MTFRPPixel product files found in the real LSA-SAF path "
        "NATIVE/YYYY/MM/DD for today or the previous 2 days. Tried: "
        + " | ".join(tried)
    )

def product_files(base_url, links):
    files = []
    for href in links:
        if re.search(r'\.(h5|hdf5|nc)(?:\.bz2)?$', href, re.I):
            files.append((href.split("/")[-1], urljoin(base_url, href)))
    files.sort(key=lambda x: x[0])
    return files

def load_cursor():
    if CURSOR_PATH.exists():
        try:
            return json.loads(CURSOR_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_processed_file": None}

def save_cursor(filename):
    CURSOR_PATH.write_text(
        json.dumps({"last_processed_file": filename}, indent=2),
        encoding="utf-8"
    )

def download(url, filename):
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = DOWNLOAD_DIR / filename
    r = session.get(url, timeout=180)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path, len(r.content)

def walk_datasets(h5):
    out = {}
    def rec(name, obj):
        if isinstance(obj, h5py.Dataset):
            out[name] = obj
    h5.visititems(rec)
    return out

def find_dataset(dsets, patterns):
    for pat in patterns:
        rx = re.compile(pat, re.I)
        for name, ds in dsets.items():
            if rx.search(name):
                return name, ds
    return None, None

def scale_values(ds):
    arr = ds[...]
    attrs = ds.attrs
    fill = attrs.get("_FillValue", attrs.get("missing_value", None))
    scale = attrs.get("scale_factor", 1.0)
    offset = attrs.get("add_offset", 0.0)
    arr = np.array(arr)
    if fill is not None:
        arr = arr.astype(float)
        arr[arr == np.array(fill).reshape(-1)[0]] = np.nan
    arr = arr.astype(float) * float(np.array(scale).reshape(-1)[0]) + float(np.array(offset).reshape(-1)[0])
    return arr

def geolocate_from_projection(h5, dsets, shape):
    # Fallback for geostationary grid products with x/y + projection metadata.
    xname, xds = find_dataset(dsets, [r'(^|/)x$'])
    yname, yds = find_dataset(dsets, [r'(^|/)y$'])
    pname, pds = find_dataset(dsets, [r'geostationary', r'projection'])
    if xds is None or yds is None or pds is None:
        return None, None
    attrs = pds.attrs
    h = float(np.array(attrs["perspective_point_height"]).reshape(-1)[0])
    lon0 = float(np.array(attrs.get("longitude_of_projection_origin", 0.0)).reshape(-1)[0])
    a = float(np.array(attrs["semi_major_axis"]).reshape(-1)[0])
    b = float(np.array(attrs["semi_minor_axis"]).reshape(-1)[0])
    sweep = attrs.get("sweep_angle_axis", b"y")
    if isinstance(sweep, bytes):
        sweep = sweep.decode()
    crs = CRS.from_proj4(f"+proj=geos +h={h} +lon_0={lon0} +sweep={sweep} +a={a} +b={b}")
    tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    x = np.asarray(xds[...], dtype=float)
    y = np.asarray(yds[...], dtype=float)

    # Some LSA SAF grids store scan angle radians; convert to projection metres.
    if np.nanmax(np.abs(x)) < 1 and np.nanmax(np.abs(y)) < 1:
        x = x * h
        y = y * h
    X, Y = np.meshgrid(x, y)
    lon, lat = tf.transform(X, Y)
    if lon.shape != shape:
        return None, None
    return lon, lat

def extract_hotspots(file_path):
    # NOTE: MTFRPPIXEL format may evolve. The extractor deliberately uses
    # semantic dataset discovery and fails loudly instead of inventing values.
    with h5py.File(file_path, "r") as h5:
        dsets = walk_datasets(h5)

        lat_name, lat_ds = find_dataset(dsets, [r'latitude$', r'(^|/)lat$'])
        lon_name, lon_ds = find_dataset(dsets, [r'longitude$', r'(^|/)lon$'])
        frp_name, frp_ds = find_dataset(dsets, [r'(^|/)frp$', r'fire.*radiative.*power'])
        conf_name, conf_ds = find_dataset(dsets, [r'confidence', r'conf'])
        unc_name, unc_ds = find_dataset(dsets, [r'frp.*uncert', r'uncert.*frp'])

        if frp_ds is None:
            raise RuntimeError(
                "Could not locate FRP dataset. Available datasets: " + ", ".join(sorted(dsets.keys())[:200])
            )

        frp = scale_values(frp_ds)

        if lat_ds is not None and lon_ds is not None:
            lat = scale_values(lat_ds)
            lon = scale_values(lon_ds)
        else:
            lon, lat = geolocate_from_projection(h5, dsets, frp.shape)
            if lon is None:
                raise RuntimeError(
                    "Could not geolocate MTFRPPIXEL grid: no latitude/longitude datasets and projection fallback failed."
                )

        conf = scale_values(conf_ds) if conf_ds is not None else None
        unc = scale_values(unc_ds) if unc_ds is not None else None

        # Accept only finite, positive FRP values. No interpolation.
        mask = np.isfinite(frp) & (frp > 0) & np.isfinite(lat) & np.isfinite(lon)
        idxs = np.argwhere(mask)
        results = []

        for idx in idxs:
            idx = tuple(idx)
            la = float(lat[idx])
            lo = float(lon[idx])
            if not (POLYGON.contains(Point(lo, la)) or POLYGON.touches(Point(lo, la))):
                continue

            item = {
                "latitude": la,
                "longitude": lo,
                "frp_mw": float(frp[idx]),
                "confidence": None if conf is None else float(conf[idx]),
                "frp_uncertainty_mw": None if unc is None else float(unc[idx]),
                "source": "LSA_SAF_MTG_MTFRPPIXEL",
            }
            results.append(item)

        return results, {
            "frp_dataset": frp_name,
            "latitude_dataset": lat_name,
            "longitude_dataset": lon_name,
            "confidence_dataset": conf_name,
            "frp_uncertainty_dataset": unc_name,
        }

def main():
    checked = datetime.now(timezone.utc).isoformat()
    seen = load_seen()
    cursor = load_cursor()

    status = {
        "checked_at_utc": checked,
        "polygon": list(POLYGON.exterior.coords),
        "source": "LSA SAF MTG/FCI MTFRPPIXEL",
        "product": "LSA-509",
        "processed_files": [],
        "new_hotspots": [],
        "errors": [],
    }

    try:
        # Collect files from today + previous 2 days.
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        all_files = []

        for d in [now, now - timedelta(days=1), now - timedelta(days=2)]:
            day_url = urljoin(
                BASE,
                f"NATIVE/{d.year:04d}/{d.month:02d}/{d.day:02d}/"
            )
            try:
                links = list_links(day_url)
            except Exception:
                continue
            all_files.extend(product_files(day_url, links))

        # Deduplicate by filename, then chronological filename order.
        dedup = {}
        for filename, url in all_files:
            dedup[filename] = url
        ordered = sorted(dedup.items())

        last = cursor.get("last_processed_file")
        if last:
            pending = [(fn, url) for fn, url in ordered if fn > last]
        else:
            # First backlog-aware run: process only latest file to avoid flooding
            # with historic detections. Subsequent runs process every new file.
            pending = ordered[-1:] if ordered else []

        status["files_available"] = len(ordered)
        status["last_processed_file_before_run"] = last
        status["pending_file_count"] = len(pending)

        all_new = []
        last_success = last

        for filename, file_url in pending:
            file_result = {
                "product_file": filename,
                "product_url": file_url,
                "new_hotspot_count": 0,
            }
            try:
                path, size = download(file_url, filename)
                hotspots, mapping = extract_hotspots(path)
                file_result["download_bytes"] = size
                file_result["inside_polygon"] = len(hotspots)
                file_result["dataset_mapping"] = mapping

                file_new = []
                for h in hotspots:
                    key = f"{filename}|{h['latitude']:.6f}|{h['longitude']:.6f}|{h['frp_mw']:.6f}"
                    h["_key"] = key
                    h["product_file"] = filename
                    if key not in seen:
                        seen.add(key)
                        file_new.append(h)
                        all_new.append(h)

                file_result["new_hotspot_count"] = len(file_new)
                last_success = filename
                save_cursor(filename)

                # Remove downloaded file after successful extraction.
                try:
                    path.unlink()
                except OSError:
                    pass

            except Exception as e:
                file_result["error"] = str(e)
                status["errors"].append({
                    "product_file": filename,
                    "error": str(e),
                })
                # Stop here so a failed file is retried next run and later
                # timestamps are not marked as completed out of order.
                status["processed_files"].append(file_result)
                break

            status["processed_files"].append(file_result)

        status["last_processed_file_after_run"] = last_success
        status["new_hotspots"] = all_new
        status["new_hotspot_count"] = len(all_new)

        if all_new:
            with EVENTS_PATH.open("a", encoding="utf-8") as f:
                for h in all_new:
                    f.write(json.dumps({
                        "detected_at_utc": checked,
                        **h
                    }) + "\n")

    except Exception as e:
        status["errors"].append({"general": str(e)})
        status["new_hotspot_count"] = 0

    STATUS_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")
    save_seen(seen)
    print(json.dumps(status, indent=2))

if __name__ == "__main__":
    main()

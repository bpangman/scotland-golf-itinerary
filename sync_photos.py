#!/usr/bin/env python3
"""Sync photos from iCloud Shared Album to the repo."""
import requests, json, os, sys, subprocess
from datetime import datetime

ALBUM_TOKEN = "B1e5n8hH4rqfx9S"
REPO_DIR = "/Users/jarvis/scotland-golf-itinerary"
STREAM_DIR = os.path.join(REPO_DIR, "images/stream")
MANIFEST = os.path.join(REPO_DIR, "photos.json")

os.makedirs(STREAM_DIR, exist_ok=True)

# Step 1: Determine the correct server partition
resp = requests.post(
    f"https://p46-sharedstreams.icloud.com/{ALBUM_TOKEN}/sharedstreams/webstream",
    json={"streamCtag": None}
)
data = resp.json()
host = data.get("X-Apple-MMe-Host")
if host:
    base = f"https://{host}/{ALBUM_TOKEN}/sharedstreams"
    resp = requests.post(f"{base}/webstream", json={"streamCtag": None})
    data = resp.json()
else:
    base = f"https://p46-sharedstreams.icloud.com/{ALBUM_TOKEN}/sharedstreams"

photos = data.get("photos", [])
if not photos:
    print("No photos in album")
    sys.exit(0)

# Step 2: Load existing manifest
existing = {}
if os.path.exists(MANIFEST):
    with open(MANIFEST) as f:
        manifest = json.load(f)
        existing = {p["guid"]: p for p in manifest.get("photos", [])}

# Step 3: Find new photos and get their asset URLs
new_guids = [p["photoGuid"] for p in photos if p["photoGuid"] not in existing]
# Also re-check all photos in case we need to re-download
all_guids = [p["photoGuid"] for p in photos]

if not new_guids and existing:
    print(f"No new photos (have {len(existing)} already)")
    sys.exit(0)

# Get asset URLs for all photos (we need fresh URLs as they expire)
# Process in batches of 25
new_photos_to_download = [p for p in photos if p["photoGuid"] not in existing]

if not new_photos_to_download:
    print("All photos already synced")
    sys.exit(0)

# Get the best (largest) derivative checksum for each new photo
checksums_to_guids = {}
photo_meta = {}
for p in new_photos_to_download:
    guid = p["photoGuid"]
    derivs = p.get("derivatives", {})
    # Pick the largest derivative
    best_key = max(derivs.keys(), key=lambda k: int(k))
    best = derivs[best_key]
    checksum = best["checksum"]
    checksums_to_guids[checksum] = guid
    photo_meta[guid] = {
        "guid": guid,
        "width": int(best.get("width", p.get("width", 0))),
        "height": int(best.get("height", p.get("height", 0))),
        "date": p.get("dateCreated", ""),
        "contributor": p.get("contributorFirstName", ""),
        "caption": p.get("caption", ""),
    }

# Fetch asset URLs
asset_resp = requests.post(f"{base}/webasseturls", json={"photoGuids": list(set(checksums_to_guids.values()))})
asset_data = asset_resp.json()
items = asset_data.get("items", {})
locations = asset_data.get("locations", {})

# Step 4: Download new photos
downloaded = 0
for checksum, guid in checksums_to_guids.items():
    if checksum not in items:
        continue
    item = items[checksum]
    loc_key = item["url_location"]
    loc = locations.get(loc_key, {})
    scheme = loc.get("scheme", "https")
    host = loc.get("hosts", [loc_key])[0]
    url_path = item["url_path"]
    full_url = f"{scheme}://{host}{url_path}"

    filename = f"{guid}.jpg"
    filepath = os.path.join(STREAM_DIR, filename)

    if os.path.exists(filepath):
        continue

    img_resp = requests.get(full_url)
    if img_resp.status_code == 200 and len(img_resp.content) > 1000:
        with open(filepath, "wb") as f:
            f.write(img_resp.content)
        photo_meta[guid]["filename"] = filename
        downloaded += 1
        print(f"  Downloaded: {filename}")
    else:
        print(f"  Failed: {filename} (status={img_resp.status_code})")

# Step 5: Update manifest
all_photos = list(existing.values())
for guid, meta in photo_meta.items():
    if "filename" in meta and guid not in existing:
        meta["filename"] = f"{guid}.jpg"
        all_photos.append(meta)

# Sort by date (newest first)
all_photos.sort(key=lambda p: p.get("date", ""), reverse=True)

manifest_data = {
    "albumName": data.get("streamName", "Scotland Trip 2026"),
    "lastSync": datetime.utcnow().isoformat() + "Z",
    "count": len(all_photos),
    "photos": all_photos
}

with open(MANIFEST, "w") as f:
    json.dump(manifest_data, f, indent=2)

if downloaded > 0:
    print(f"\nSynced {downloaded} new photo(s), {len(all_photos)} total")
    # Git commit and push
    os.chdir(REPO_DIR)
    subprocess.run(["git", "add", "images/stream/", "photos.json"], check=True)
    subprocess.run(["git", "commit", "-m", f"Sync {downloaded} photo(s) from iCloud shared album"], check=True)
    subprocess.run(["git", "push"], check=True)
    print("Pushed to GitHub")
else:
    print("No new photos downloaded")

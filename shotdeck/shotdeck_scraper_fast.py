#!/usr/bin/env python3
"""
ShotDeck Fast Scraper (Cache Method)
====================================

This script scrapes video clips from ShotDeck's CDN cache directory.
It's faster than the comprehensive method but limited to ~1,300 cached clips.

Features:
- Scrapes CDN directory listing for available clips
- Fast parallel video downloads
- All clips are guaranteed to exist (pre-cached)
- Basic metadata extraction
- Groups videos by title with metadata
- Detailed speed tracking

How it works:
1. Fetches the directory listing from https://crunch.shotdeck.com/assets/images/clips/
2. Extracts clip IDs from the Apache-style directory listing
3. Downloads videos in parallel (guaranteed to exist)
4. Fetches metadata for each clip
5. Groups by title and saves to JSON

Limitations:
- Only accesses cached clips (~1,300 at any time)
- Cache contents change as users view different clips on the site
- Does not trigger video generation for uncached clips

Requirements:
- Python 3.10+
- requests, beautifulsoup4
- ShotDeck account with valid session cookie (for metadata)

Usage:
    python shotdeck_scraper_fast.py
"""

import requests
import json
import os
import time
import re
from datetime import datetime
from collections import defaultdict
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed


# =============================================================================
# CONFIGURATION
# =============================================================================

N_VIDEOS = 1000  # Number of videos to scrape (max ~1,300 available)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
VIDEO_DIR = os.path.join(OUTPUT_DIR, "videos")

# Rate limiting
METADATA_DELAY = 0.3     # Seconds between metadata requests
VIDEO_DOWNLOAD_WORKERS = 5  # Parallel video downloads (can be higher since all exist)

# URLs
CDN_DIRECTORY_URL = "https://crunch.shotdeck.com/assets/images/clips/"
VIDEO_BASE_URL = "https://crunch.shotdeck.com/assets/images/clips"
METADATA_BASE_URL = "https://shotdeck.com/browse/shotdetailsajax/image"

# Session cookies - REPLACE with your session cookie from browser
# To get this:
# 1. Log into shotdeck.com in your browser
# 2. Open DevTools (F12) > Application > Cookies > shotdeck.com
# 3. Copy the PHPSESSID value
COOKIES = {
    "PHPSESSID": "YOUR_SESSION_ID_HERE",
}

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "referer": "https://shotdeck.com/",
}


# =============================================================================
# CDN DIRECTORY SCRAPING
# =============================================================================

def scrape_cdn_directory(limit: int = None) -> list[str]:
    """
    Scrape clip IDs from the CDN directory listing.
    
    The CDN serves an Apache-style directory listing showing all
    cached video clips. These clips are guaranteed to exist and
    can be downloaded directly.
    
    Returns:
        List of clip IDs (8-character alphanumeric)
    """
    print("Scraping CDN directory listing...")
    
    try:
        response = requests.get(CDN_DIRECTORY_URL, headers=HEADERS, timeout=60)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        clip_ids = set()
        for link in soup.find_all('a', href=True):
            href = link['href']
            # Match pattern: XXXXXXXX_clip.mp4
            match = re.match(r'^([A-Z0-9]{8})_clip\.mp4$', href)
            if match:
                clip_ids.add(match.group(1))
        
        clip_ids = sorted(list(clip_ids))
        print(f"  Found {len(clip_ids)} cached clips")
        
        if limit and len(clip_ids) > limit:
            clip_ids = clip_ids[:limit]
            print(f"  Limited to {limit} clips")
        
        return clip_ids
        
    except requests.RequestException as e:
        print(f"  Error: {e}")
        return []


# =============================================================================
# VIDEO DOWNLOAD
# =============================================================================

def download_video(shot_id: str, output_dir: str) -> dict:
    """
    Download a video clip from the CDN.
    
    Since we're using the cache method, all clips are guaranteed to exist.
    """
    url = f"{VIDEO_BASE_URL}/{shot_id}_clip.mp4"
    filepath = os.path.join(output_dir, f"{shot_id}_clip.mp4")
    
    # Skip if already downloaded
    if os.path.exists(filepath):
        size = os.path.getsize(filepath)
        return {"shot_id": shot_id, "path": filepath, "size_bytes": size, "status": "exists"}
    
    try:
        response = requests.get(url, stream=True, timeout=60)
        
        if response.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            size = os.path.getsize(filepath)
            return {"shot_id": shot_id, "path": filepath, "size_bytes": size, "status": "downloaded"}
        else:
            return {"shot_id": shot_id, "status": "failed", "error": f"HTTP {response.status_code}"}
    except requests.RequestException as e:
        return {"shot_id": shot_id, "status": "failed", "error": str(e)}


# =============================================================================
# METADATA EXTRACTION
# =============================================================================

def parse_metadata_html(html: str, shot_id: str) -> dict:
    """Parse metadata from ShotDeck's AJAX response."""
    soup = BeautifulSoup(html, 'html.parser')
    metadata = {"shot_id": shot_id}
    
    FIELD_NAMES = {
        'tag': 'tags', 'tags': 'tags',
        'genre': 'genre', 'genres': 'genre',
        'director': 'director', 'directors': 'director',
        'cinematographer': 'cinematographer',
        'actors': 'actors', 'actor': 'actors', 'cast': 'actors',
        'year': 'year',
        'time period': 'time_period',
        'title': 'title', 'movie': 'title', 'film': 'title',
        'music genre': 'music_genre',
        'video genre': 'video_genre',
    }
    
    LIST_FIELDS = {'tags', 'genre', 'director', 'cinematographer', 'actors', 'music_genre', 'video_genre'}
    
    for detail_group in soup.find_all('div', class_='detail-group'):
        label_elem = detail_group.find('p', class_='detail-type')
        if not label_elem:
            continue
        
        label_text = label_elem.get_text(strip=True).rstrip(':').lower()
        if label_text not in FIELD_NAMES:
            continue
        
        field_name = FIELD_NAMES[label_text]
        values_elem = detail_group.find('div', class_='details')
        if not values_elem:
            continue
        
        links = values_elem.find_all('a')
        if links:
            values = [link.get_text(strip=True) for link in links if link.get_text(strip=True)]
        else:
            text = values_elem.get_text(strip=True)
            values = [v.strip() for v in text.split(',') if v.strip()] if ',' in text else [text] if text else []
        
        if values:
            if field_name in LIST_FIELDS:
                metadata[field_name] = values
            else:
                metadata[field_name] = values[0] if len(values) == 1 else values
    
    if 'title' not in metadata:
        title_link = soup.find('a', class_='movie-link')
        if title_link:
            metadata['title'] = title_link.get_text(strip=True)
    
    return metadata


def fetch_metadata(session: requests.Session, shot_id: str) -> dict | None:
    """Fetch metadata for a single shot."""
    url = f"{METADATA_BASE_URL}/{shot_id}/"
    
    try:
        response = session.get(url, headers=HEADERS, timeout=30)
        if response.status_code == 200:
            return parse_metadata_html(response.text, shot_id)
        return None
    except requests.RequestException:
        return None


# =============================================================================
# GROUPING AND OUTPUT
# =============================================================================

def get_title_key(metadata: dict) -> str:
    """Generate a grouping key from metadata."""
    if metadata.get('title'):
        return metadata['title']
    
    artists = metadata.get('actors', [])
    if isinstance(artists, str):
        artists = [artists]
    
    year = metadata.get('year') or metadata.get('time_period')
    
    if artists:
        title_parts = [', '.join(artists)]
        if year:
            title_parts.append(f"({year})")
        return ' '.join(title_parts)
    
    directors = metadata.get('director', [])
    if isinstance(directors, str):
        directors = [directors]
    if directors:
        return ', '.join(directors)
    
    return "Unknown"


def group_by_title(all_metadata: list[dict]) -> dict:
    """Group shots by their title."""
    groups = defaultdict(lambda: {
        "metadata": {},
        "video_count": 0,
        "total_size_mb": 0,
        "shots": []
    })
    
    for item in all_metadata:
        title_key = get_title_key(item)
        group = groups[title_key]
        
        if not group["metadata"]:
            group["metadata"] = {
                "director": item.get('director', []),
                "cinematographer": item.get('cinematographer', []),
                "genre": item.get('genre', []),
                "year": item.get('year') or item.get('time_period'),
            }
        
        shot_info = {"shot_id": item.get('shot_id')}
        for key, value in item.items():
            if key not in ['shot_id'] and value:
                shot_info[key] = value
        
        group["shots"].append(shot_info)
        group["video_count"] += 1
        if item.get('size_bytes'):
            group["total_size_mb"] += item['size_bytes'] / (1024 * 1024)
    
    for group in groups.values():
        group["total_size_mb"] = round(group["total_size_mb"], 2)
    
    return dict(groups)


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 60)
    print("ShotDeck Fast Scraper (Cache Method)")
    print("=" * 60)
    print(f"Target: {N_VIDEOS} videos")
    print()
    
    # Check cookies (only needed for metadata)
    cookies_valid = COOKIES.get("PHPSESSID") != "YOUR_SESSION_ID_HERE"
    if not cookies_valid:
        print("WARNING: No session cookie set.")
        print("Videos will download but metadata will be limited.")
        print()
    
    os.makedirs(VIDEO_DIR, exist_ok=True)
    
    session = requests.Session()
    if cookies_valid:
        session.cookies.update(COOKIES)
    
    # Track timing
    total_start = datetime.now()
    
    # Step 1: Get clip IDs from CDN directory
    print("\n[STEP 1] Scraping CDN directory")
    print("-" * 40)
    discovery_start = datetime.now()
    clip_ids = scrape_cdn_directory(limit=N_VIDEOS)
    discovery_time = (datetime.now() - discovery_start).total_seconds()
    
    if not clip_ids:
        print("No clips found!")
        return
    
    # Step 2: Download videos in parallel
    print(f"\n[STEP 2] Downloading {len(clip_ids)} videos")
    print("-" * 40)
    download_start = datetime.now()
    download_results = []
    
    with ThreadPoolExecutor(max_workers=VIDEO_DOWNLOAD_WORKERS) as executor:
        futures = {
            executor.submit(download_video, clip_id, VIDEO_DIR): clip_id
            for clip_id in clip_ids
        }
        
        completed = 0
        for future in as_completed(futures):
            result = future.result()
            download_results.append(result)
            completed += 1
            
            if completed % 100 == 0:
                print(f"  {completed}/{len(clip_ids)} videos processed")
    
    download_time = (datetime.now() - download_start).total_seconds()
    
    # Step 3: Fetch metadata
    print(f"\n[STEP 3] Fetching metadata")
    print("-" * 40)
    metadata_start = datetime.now()
    all_metadata = []
    
    if cookies_valid:
        for i, clip_id in enumerate(clip_ids, 1):
            metadata = fetch_metadata(session, clip_id)
            if metadata:
                all_metadata.append(metadata)
            else:
                all_metadata.append({"shot_id": clip_id})
            
            if i % 100 == 0:
                print(f"  {i}/{len(clip_ids)} metadata fetched")
            
            time.sleep(METADATA_DELAY)
    else:
        all_metadata = [{"shot_id": clip_id} for clip_id in clip_ids]
        print("  Skipped (no session cookie)")
    
    metadata_time = (datetime.now() - metadata_start).total_seconds()
    
    # Merge download info with metadata
    download_map = {r['shot_id']: r for r in download_results}
    for item in all_metadata:
        dl = download_map.get(item['shot_id'], {})
        if dl.get('path'):
            item['local_path'] = dl['path']
        if dl.get('size_bytes'):
            item['size_bytes'] = dl['size_bytes']
    
    # Step 4: Group and save
    print(f"\n[STEP 4] Grouping and saving results")
    print("-" * 40)
    grouped_data = group_by_title(all_metadata)
    
    # Calculate stats
    total_time = (datetime.now() - total_start).total_seconds()
    downloaded_count = sum(1 for r in download_results if r.get('status') in ['downloaded', 'exists'])
    failed_count = sum(1 for r in download_results if r.get('status') == 'failed')
    total_size = sum(r.get('size_bytes', 0) for r in download_results if r.get('size_bytes'))
    
    # Save results
    output = {
        "scraped_at": datetime.now().isoformat(),
        "method": "fast_cdn_cache",
        "stats": {
            "clips_in_cache": len(clip_ids),
            "videos_downloaded": downloaded_count,
            "videos_failed": failed_count,
            "metadata_retrieved": len([m for m in all_metadata if len(m) > 1]),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "unique_groups": len(grouped_data),
            "timing": {
                "discovery_seconds": round(discovery_time, 1),
                "download_seconds": round(download_time, 1),
                "metadata_seconds": round(metadata_time, 1),
                "total_seconds": round(total_time, 1),
            },
            "speed": {
                "videos_per_second": round(downloaded_count / download_time, 3) if download_time > 0 else 0,
                "mb_per_second": round((total_size / (1024 * 1024)) / download_time, 2) if download_time > 0 else 0,
            }
        },
        "groups": grouped_data
    }
    
    output_path = os.path.join(OUTPUT_DIR, "shotdeck_grouped.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    # Print summary
    print(f"\n{'=' * 60}")
    print("SCRAPING COMPLETE")
    print("=" * 60)
    print(f"Videos downloaded: {downloaded_count}/{len(clip_ids)}")
    print(f"Videos failed: {failed_count}")
    print(f"Unique groups: {len(grouped_data)}")
    print(f"Total size: {output['stats']['total_size_mb']:.2f} MB")
    print()
    print("TIMING:")
    print(f"  CDN discovery: {discovery_time:.1f}s")
    print(f"  Video download: {download_time:.1f}s")
    print(f"  Metadata fetch: {metadata_time:.1f}s")
    print(f"  Total: {total_time:.1f}s")
    print()
    print("SPEED:")
    print(f"  Videos/second: {output['stats']['speed']['videos_per_second']:.3f}")
    print(f"  MB/second: {output['stats']['speed']['mb_per_second']:.2f}")
    print()
    print(f"Output: {output_path}")
    print(f"Videos: {VIDEO_DIR}/")


if __name__ == "__main__":
    main()

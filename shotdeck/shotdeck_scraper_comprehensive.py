#!/usr/bin/env python3
"""
ShotDeck Comprehensive Scraper
==============================

This script scrapes video clips from ShotDeck using their search API to access
the full database of 2.3+ million shots (approximately 400,000+ with video clips).

Features:
- API pagination to discover all shots with video clips
- On-demand video generation via viewclip endpoint
- Full metadata extraction and parsing
- Groups videos by title with corresponding metadata
- Detailed speed and performance tracking

How it works:
1. Paginates through /browse/searchstillsajax to find shots with data-clip='1'
2. Triggers video generation by calling /browse/viewclip endpoint
3. Downloads the generated video from the CDN
4. Fetches metadata from /browse/shotdetailsajax
5. Groups all videos by inferred title and saves to JSON

Requirements:
- Python 3.10+
- requests, beautifulsoup4
- ShotDeck account with valid session cookie

Usage:
    python shotdeck_scraper_comprehensive.py
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

N_VIDEOS = 2000  # Number of videos to scrape (assignment: ~2,000)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
VIDEO_DIR = os.path.join(OUTPUT_DIR, "videos")

# API settings
API_SHOTS_PER_PAGE = 36  # ShotDeck returns ~36 shots per page
API_PAGE_DELAY = 0.3     # Seconds between API page requests
ONLY_WITH_CLIPS = True   # Only retrieve shots that have video clips

# Rate limiting
METADATA_DELAY = 0.5     # Seconds between metadata requests
VIDEO_DOWNLOAD_WORKERS = 3  # Parallel video downloads

# URLs
VIDEO_BASE_URL = "https://crunch.shotdeck.com/assets/images/clips"
METADATA_BASE_URL = "https://shotdeck.com/browse/shotdetailsajax/image"
SEARCH_API_URL = "https://shotdeck.com/browse/searchstillsajax"
VIEWCLIP_URL = "https://crunch.shotdeck.com/browse/viewclip/src/1/s"

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
# API SHOT DISCOVERY
# =============================================================================

def scrape_api_shots(session: requests.Session, limit: int = None) -> tuple[list[str], dict]:
    """
    Scrape shot IDs from ShotDeck's search API.
    
    Paginates through /browse/searchstillsajax to get all shot IDs,
    filtering for shots that have video clips (data-clip='1').
    
    Returns:
        Tuple of (list of shot IDs, stats dict)
    """
    print("Discovering shots via API...")
    print(f"  Limit: {limit or 'None (all shots)'}")
    print(f"  Filter: Only shots with video clips")
    
    all_shot_ids = []
    shots_with_clips = 0
    shots_without_clips = 0
    total_shots = None
    page = 1
    
    while True:
        url = f"{SEARCH_API_URL}/page/{page}"
        
        try:
            response = session.get(url, headers={
                **HEADERS,
                "X-Requested-With": "XMLHttpRequest"
            }, timeout=30)
            
            if response.status_code != 200:
                print(f"  HTTP {response.status_code} on page {page}, stopping.")
                break
            
            html = response.text
            
            # Extract total shots count on first page
            if total_shots is None:
                match = re.search(r'totalShots\s*=\s*(\d+)', html)
                if match:
                    total_shots = int(match.group(1))
                    print(f"  Total shots in database: {total_shots:,}")
            
            # Parse shot IDs from HTML
            soup = BeautifulSoup(html, 'html.parser')
            page_shots = []
            
            for div in soup.find_all('div', class_='outerimage'):
                shot_id = div.get('data-shotid')
                has_clip = div.get('data-clip') == '1'
                
                if shot_id:
                    if has_clip:
                        shots_with_clips += 1
                        if ONLY_WITH_CLIPS:
                            page_shots.append(shot_id)
                    else:
                        shots_without_clips += 1
                        if not ONLY_WITH_CLIPS:
                            page_shots.append(shot_id)
            
            if not page_shots and ONLY_WITH_CLIPS:
                # No clips on this page, but continue searching
                pass
            
            all_shot_ids.extend(page_shots)
            
            # Progress update every 50 pages
            if page % 50 == 0:
                print(f"  Page {page}: {len(all_shot_ids)} shots collected")
            
            # Check limits
            if limit and len(all_shot_ids) >= limit:
                all_shot_ids = all_shot_ids[:limit]
                print(f"  Reached limit of {limit} shots")
                break
            
            if total_shots and page * API_SHOTS_PER_PAGE >= total_shots:
                break
            
            # Check if page had no results
            if not soup.find_all('div', class_='outerimage'):
                break
            
            page += 1
            time.sleep(API_PAGE_DELAY)
            
        except requests.RequestException as e:
            print(f"  Error on page {page}: {e}")
            break
    
    stats = {
        'total_in_database': total_shots,
        'pages_scraped': page,
        'shots_with_clips': shots_with_clips,
        'shots_without_clips': shots_without_clips,
        'shots_collected': len(all_shot_ids),
    }
    
    print(f"\n  API Discovery Complete:")
    print(f"    Pages scraped: {page}")
    print(f"    Shots with video: {shots_with_clips}")
    print(f"    Shots collected: {len(all_shot_ids)}")
    
    return all_shot_ids, stats


# =============================================================================
# VIDEO GENERATION AND DOWNLOAD
# =============================================================================

def trigger_video_generation(shot_id: str, session: requests.Session) -> dict | None:
    """
    Trigger video clip generation by calling the viewclip endpoint.
    
    ShotDeck generates video clips on-demand. This endpoint triggers
    the generation process and returns the video URL.
    """
    url = f"{VIEWCLIP_URL}/{shot_id}"
    
    try:
        response = session.get(url, headers={
            **HEADERS,
            "X-Requested-With": "XMLHttpRequest"
        }, timeout=30)
        
        if response.status_code == 200 and response.text.strip():
            try:
                data = json.loads(response.text)
                if isinstance(data, list) and len(data) >= 2:
                    return {
                        "filename": data[0],
                        "url": data[1],
                        "framerate": data[2] if len(data) > 2 else None,
                        "type": data[3] if len(data) > 3 else None,
                    }
            except json.JSONDecodeError:
                pass
        return None
    except requests.RequestException:
        return None


def download_video(shot_id: str, output_dir: str, session: requests.Session) -> dict:
    """
    Download a video clip, triggering generation if needed.
    """
    filepath = os.path.join(output_dir, f"{shot_id}_clip.mp4")
    
    # Skip if already downloaded
    if os.path.exists(filepath):
        size = os.path.getsize(filepath)
        return {"shot_id": shot_id, "path": filepath, "size_bytes": size, "status": "exists"}
    
    # Trigger video generation
    clip_info = trigger_video_generation(shot_id, session)
    if clip_info:
        url = clip_info.get("url", f"{VIDEO_BASE_URL}/{shot_id}_clip.mp4")
        time.sleep(0.3)  # Brief delay for generation
    else:
        url = f"{VIDEO_BASE_URL}/{shot_id}_clip.mp4"
    
    try:
        response = requests.get(url, stream=True, timeout=120)
        
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
        'cinematographer': 'cinematographer', 'dop': 'cinematographer', 'dp': 'cinematographer',
        'production designer': 'production_designer',
        'costume designer': 'costume_designer',
        'editor': 'editor', 'editors': 'editor',
        'colorist': 'colorist',
        'color': 'color',
        'actors': 'actors', 'actor': 'actors', 'cast': 'actors',
        'time period': 'time_period',
        'year': 'year',
        'aspect ratio': 'aspect_ratio',
        'format': 'format',
        'frame size': 'frame_size',
        'shot type': 'shot_type',
        'lens size': 'lens_size',
        'composition': 'composition',
        'lighting': 'lighting',
        'lighting type': 'lighting_type',
        'time of day': 'time_of_day',
        'interior/exterior': 'interior_exterior',
        'location type': 'location_type',
        'set': 'set',
        'story location': 'story_location',
        'filming location': 'filming_location',
        'title': 'title', 'movie': 'title', 'film': 'title',
        'music genre': 'music_genre',
        'video genre': 'video_genre',
        'stylist': 'stylist',
        'production company': 'production_company',
    }
    
    LIST_FIELDS = {
        'tags', 'genre', 'director', 'cinematographer', 'actors', 'color',
        'shot_type', 'lens_size', 'composition', 'lighting', 'lighting_type',
        'set', 'story_location', 'filming_location', 'editor', 'colorist',
        'production_designer', 'costume_designer', 'music_genre', 'video_genre',
        'stylist', 'production_company',
    }
    
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
    
    # Look for title in other places
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
        
        shot_info = {
            "shot_id": item.get('shot_id'),
            "video_url": f"{VIDEO_BASE_URL}/{item.get('shot_id')}_clip.mp4",
        }
        
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
    print("ShotDeck Comprehensive Scraper")
    print("=" * 60)
    print(f"Target: {N_VIDEOS} videos")
    print()
    
    # Check cookies
    if COOKIES.get("PHPSESSID") == "YOUR_SESSION_ID_HERE":
        print("ERROR: Please set your PHPSESSID cookie!")
        print("1. Log into shotdeck.com")
        print("2. Open DevTools > Application > Cookies")
        print("3. Copy PHPSESSID value to this script")
        return
    
    os.makedirs(VIDEO_DIR, exist_ok=True)
    
    session = requests.Session()
    session.cookies.update(COOKIES)
    
    # Track timing
    total_start = datetime.now()
    
    # Step 1: Discover shots via API
    print("\n[STEP 1] Discovering shots via API")
    print("-" * 40)
    api_start = datetime.now()
    shot_ids, api_stats = scrape_api_shots(session, limit=N_VIDEOS)
    api_time = (datetime.now() - api_start).total_seconds()
    
    if not shot_ids:
        print("No shots found!")
        return
    
    # Step 2: Fetch metadata
    print(f"\n[STEP 2] Fetching metadata for {len(shot_ids)} shots")
    print("-" * 40)
    metadata_start = datetime.now()
    all_metadata = []
    
    for i, shot_id in enumerate(shot_ids, 1):
        metadata = fetch_metadata(session, shot_id)
        if metadata:
            all_metadata.append(metadata)
        else:
            all_metadata.append({"shot_id": shot_id})
        
        if i % 100 == 0:
            print(f"  {i}/{len(shot_ids)} metadata fetched")
        
        time.sleep(METADATA_DELAY)
    
    metadata_time = (datetime.now() - metadata_start).total_seconds()
    print(f"  Metadata fetched: {len(all_metadata)}")
    
    # Step 3: Download videos
    print(f"\n[STEP 3] Downloading videos")
    print("-" * 40)
    download_start = datetime.now()
    download_results = []
    
    with ThreadPoolExecutor(max_workers=VIDEO_DOWNLOAD_WORKERS) as executor:
        futures = {
            executor.submit(download_video, shot_id, VIDEO_DIR, session): shot_id
            for shot_id in shot_ids
        }
        
        completed = 0
        for future in as_completed(futures):
            result = future.result()
            download_results.append(result)
            completed += 1
            
            if completed % 100 == 0:
                print(f"  {completed}/{len(shot_ids)} videos processed")
    
    download_time = (datetime.now() - download_start).total_seconds()
    
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
        "method": "comprehensive_api",
        "stats": {
            "total_shots_requested": len(shot_ids),
            "videos_downloaded": downloaded_count,
            "videos_failed": failed_count,
            "metadata_retrieved": len([m for m in all_metadata if len(m) > 1]),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "unique_groups": len(grouped_data),
            "timing": {
                "api_discovery_seconds": round(api_time, 1),
                "metadata_fetch_seconds": round(metadata_time, 1),
                "video_download_seconds": round(download_time, 1),
                "total_seconds": round(total_time, 1),
            },
            "speed": {
                "videos_per_second": round(downloaded_count / total_time, 3) if total_time > 0 else 0,
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
    print(f"Videos downloaded: {downloaded_count}/{len(shot_ids)}")
    print(f"Videos failed: {failed_count}")
    print(f"Unique groups: {len(grouped_data)}")
    print(f"Total size: {output['stats']['total_size_mb']:.2f} MB")
    print()
    print("TIMING:")
    print(f"  API discovery: {api_time:.1f}s")
    print(f"  Metadata fetch: {metadata_time:.1f}s")
    print(f"  Video download: {download_time:.1f}s")
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

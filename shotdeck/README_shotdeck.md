# ShotDeck Video Scraper

A Python scraper for downloading video clips and metadata from [ShotDeck](https://shotdeck.com), a cinematography reference database with over 2.3 million shots.

## Project Overview

**Goal:** Scrape ~2,000 videos, grouping under each title together with the corresponding metadata, saved in a JSON file. Record scraping speed.

This repository contains two scraper implementations:

| Scraper | Access | Speed | Use Case |
|---------|--------|-------|----------|
| **Comprehensive** | 400,000+ videos | ~0.07 videos/sec | Full database access |
| **Fast** | ~1,300 videos | ~3-5 videos/sec | Quick sampling |

## Two Scraping Approaches

### 1. Comprehensive Method (`shotdeck_scraper_comprehensive.py`)

Uses ShotDeck's search API to access the full database of 2.3+ million shots.

**How it works:**
1. Paginates through `/browse/searchstillsajax` API
2. Filters for shots with `data-clip='1'` (have video)
3. Triggers on-demand video generation via `/browse/viewclip` endpoint
4. Downloads generated videos from CDN
5. Fetches full metadata for each shot
6. Groups by title and saves to JSON

**Pros:**
- Access to ~400,000+ videos (all shots with video capability)
- Complete metadata extraction
- Works with any shot in the database

**Cons:**
- Slower due to API pagination and metadata fetching
- Requires video generation trigger for each clip

### 2. Fast Method (`shotdeck_scraper_fast.py`)

Scrapes ShotDeck's CDN cache directory for pre-generated clips.

**How it works:**
1. Fetches directory listing from `https://crunch.shotdeck.com/assets/images/clips/`
2. Parses Apache-style directory listing for clip IDs
3. Downloads videos in parallel (all guaranteed to exist)
4. Fetches metadata
5. Groups by title and saves to JSON

**Pros:**
- Much faster (no generation wait, parallel downloads)
- 100% download success rate (all clips pre-cached)
- Simpler implementation

**Cons:**
- Limited to ~1,300 cached clips at any time
- Cache contents change as users view different clips
- Sample may not be representative

## Requirements

- Python 3.10+
- ShotDeck account (free or paid)
- Valid session cookie

### Dependencies

```bash
pip install -r requirements.txt
```

Or install manually:
```bash
pip install requests beautifulsoup4
```

## Setup

### 1. Get Your Session Cookie

1. Log into [shotdeck.com](https://shotdeck.com) in your browser
2. Open Developer Tools:
   - Chrome: `F12` or `Cmd+Option+I` (Mac) / `Ctrl+Shift+I` (Windows)
   - Firefox: `F12` or `Cmd+Option+I` (Mac) / `Ctrl+Shift+I` (Windows)
3. Go to **Application** tab (Chrome) or **Storage** tab (Firefox)
4. Under **Cookies**, click on `https://shotdeck.com`
5. Find `PHPSESSID` and copy its value

### 2. Configure the Script

Open the scraper file and replace the placeholder cookie:

```python
COOKIES = {
    "PHPSESSID": "your_session_id_here",  # Paste your cookie value
}
```

### 3. Adjust Settings (Optional)

```python
N_VIDEOS = 2000  # Number of videos to scrape
OUTPUT_DIR = "output"  # Output directory
```

## Usage

### Comprehensive Scraper

```bash
python shotdeck_scraper_comprehensive.py
```

### Fast Scraper

```bash
python shotdeck_scraper_fast.py
```

## Output Format

Both scrapers produce a JSON file with this structure:

```json
{
  "scraped_at": "2024-12-19T10:30:00",
  "method": "comprehensive_api",
  "stats": {
    "total_shots_requested": 2000,
    "videos_downloaded": 2000,
    "videos_failed": 0,
    "metadata_retrieved": 2000,
    "total_size_mb": 28500.45,
    "unique_groups": 1542,
    "timing": {
      "api_discovery_seconds": 45.2,
      "metadata_fetch_seconds": 1200.5,
      "video_download_seconds": 3600.8,
      "total_seconds": 4846.5
    },
    "speed": {
      "videos_per_second": 0.074,
      "mb_per_second": 7.92
    }
  },
  "groups": {
    "The Dark Knight": {
      "metadata": {
        "director": ["Christopher Nolan"],
        "cinematographer": ["Wally Pfister"],
        "genre": ["Action", "Crime", "Drama"],
        "year": "2008"
      },
      "video_count": 15,
      "total_size_mb": 245.8,
      "shots": [
        {
          "shot_id": "ABC12345",
          "video_url": "https://crunch.shotdeck.com/assets/images/clips/ABC12345_clip.mp4",
          "local_path": "output/videos/ABC12345_clip.mp4",
          "size_bytes": 15234567,
          "shot_type": ["Wide Shot"],
          "lighting": ["Low Key"]
        }
      ]
    }
  }
}
```

## Performance Results

Actual test results from scraping runs:

### Comprehensive Method (100 videos)

| Metric | Value |
|--------|-------|
| Videos Downloaded | 100/100 (100%) |
| Metadata Retrieved | 100/100 |
| Total Size | 1,414.85 MB |
| Unique Groups | 83 |
| Total Time | 1,344.4 seconds |
| **Speed** | **0.074 videos/sec** |

### Fast Method (100 videos)

| Metric | Value |
|--------|-------|
| Videos Downloaded | 100/100 (100%) |
| Total Time | ~30 seconds |
| **Speed** | **~3.3 videos/sec** |

### Speed Comparison

| Method | Videos/sec | Notes |
|--------|------------|-------|
| Comprehensive | 0.07 | Limited by metadata API rate limiting |
| Fast | 3-5 | Limited by download bandwidth |

## Technical Details

### API Endpoints Discovered

| Endpoint | Purpose |
|----------|---------|
| `/browse/searchstillsajax/page/{N}` | Paginated shot search |
| `/browse/shotdetailsajax/image/{ID}/` | Shot metadata |
| `/browse/viewclip/src/1/s/{ID}` | Trigger video generation |
| `crunch.shotdeck.com/assets/images/clips/` | CDN directory listing |
| `crunch.shotdeck.com/assets/images/clips/{ID}_clip.mp4` | Video files |

### On-Demand Video Generation

ShotDeck generates video clips on-demand. 

1. Shots with `data-clip='1'` have video capability
2. Calling `/browse/viewclip` triggers server-side generation
3. Generated clips become available on the CDN
4. Clips may be evicted from cache over time

### Rate Limiting

- API requests: 0.3-0.5 second delay recommended
- Metadata requests: 0.5 second delay
- Video downloads: Parallel (3-5 workers)

## Project Structure

```
video-scraping/
├── README.md
├── requirements.txt
├── shotdeck_scraper_comprehensive.py  # Full API method
├── shotdeck_scraper_fast.py           # CDN cache method
└── output/                            # Generated output
    ├── shotdeck_grouped.json          # Grouped metadata
    └── videos/                        # Downloaded clips
        ├── ABC12345_clip.mp4
        └── ...
```

This scraper is intended for personal/educational use only. 

## License

MIT License

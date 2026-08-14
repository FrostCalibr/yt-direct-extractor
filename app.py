import os
import re
import time
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp
import urllib.request
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yt_extractor")

app = FastAPI(
    title="YouTube Direct Link Extractor",
    description="Zero-bandwidth video link extractor with client-side residential IP fallback & Cobalt v10 integration",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ExtractRequest(BaseModel):
    url: str

@app.get("/health")
def health_check():
    return {"status": "ok", "timestamp": time.time()}

def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'youtube\.com\/shorts\/([0-9A-Za-z_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def fetch_cobalt(url: str) -> Optional[Dict[str, Any]]:
    """Cobalt v10 API endpoint call."""
    try:
        cobalt_url = "https://api.cobalt.tools/"
        payload = json.dumps({
            "url": url,
            "videoQuality": "720"
        }).encode('utf-8')
        
        req = urllib.request.Request(
            cobalt_url,
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            if response.status in [200, 201]:
                data = json.loads(response.read().decode('utf-8'))
                status = data.get('status')
                direct_url = data.get('url')
                
                if direct_url and status in ['tunnel', 'redirect', 'picker']:
                    video_id = extract_video_id(url) or "video"
                    return {
                        "success": True,
                        "title": f"YouTube Video ({video_id})",
                        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id != "video" else None,
                        "duration": "Direct CDN",
                        "uploader": "YouTube",
                        "view_count": "N/A",
                        "streams": {
                            "combined": [{
                                "format_id": "cobalt_stream",
                                "quality": "HD Direct Link (Cobalt)",
                                "height": 720,
                                "ext": "mp4",
                                "filesize": "High Speed CDN",
                                "direct_url": direct_url,
                                "type": "video_audio"
                            }],
                            "audio": []
                        }
                    }
    except Exception as e:
        logger.warning(f"Server-side Cobalt call failed: {e}")
    return None

@app.post("/api/extract")
def extract_video_info(payload: ExtractRequest):
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    video_id = extract_video_id(url)

    # Step 1: Check if YOUTUBE_COOKIES_TEXT is configured for yt-dlp
    cookies_text = os.getenv("YOUTUBE_COOKIES_TEXT")
    if cookies_text:
        cookies_path = "/tmp/youtube_cookies.txt"
        with open(cookies_path, "w") as f:
            f.write(cookies_text)
        
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'cookiefile': cookies_path
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    # Return yt-dlp extracted streams
                    formats_list = info.get('formats', [])
                    combined_streams = []
                    for f in formats_list:
                        if f.get('url') and f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                            combined_streams.append({
                                "format_id": f.get('format_id'),
                                "quality": f"{f.get('height')}p" if f.get('height') else "HD",
                                "height": f.get('height', 0),
                                "ext": f.get('ext', 'mp4'),
                                "filesize": "Direct Stream",
                                "direct_url": f.get('url'),
                                "type": "video_audio"
                            })
                    combined_streams.sort(key=lambda x: x.get('height', 0), reverse=True)
                    return {
                        "success": True,
                        "title": info.get('title', 'YouTube Video'),
                        "thumbnail": info.get('thumbnail'),
                        "duration": str(info.get('duration', '')),
                        "uploader": info.get('uploader', 'YouTube'),
                        "view_count": str(info.get('view_count', '')),
                        "streams": { "combined": combined_streams[:4], "audio": [] }
                    }
        except Exception as e:
            logger.warning(f"yt-dlp with cookies failed: {e}")

    # Step 2: Try Cobalt v10 API on server
    cobalt_res = fetch_cobalt(url)
    if cobalt_res:
        return cobalt_res

    # Step 3: Server IP is flagged by YouTube datacenter bot protection.
    # Return client-side fallback instruction so user's browser performs residential extraction!
    return {
        "success": True,
        "client_fallback": True,
        "video_id": video_id,
        "video_url": url
    }

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "API active."})

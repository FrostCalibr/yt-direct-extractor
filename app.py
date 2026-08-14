import os
import re
import time
import logging
import json
import urllib.request
import urllib.parse
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("yt_extractor")

app = FastAPI(
    title="YouTube Direct Link Extractor",
    description="Authenticated yt-dlp stream extractor with session cookies",
    version="5.0.0"
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
        r'(?:v=|\/)([0-9A-Za-z_-]{11})',
        r'youtu\.be\/([0-9A-Za-z_-]{11})',
        r'youtube\.com\/shorts\/([0-9A-Za-z_-]{11})'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def format_bytes(size: Optional[int]) -> str:
    if not size:
        return "Direct Stream"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "Unknown"
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def fetch_authenticated_yt_dlp(url: str) -> Dict[str, Any]:
    """Primary extraction engine using valid session cookies."""
    cookies_text = os.getenv("YOUTUBE_COOKIES_TEXT")
    cookies_path = "/tmp/youtube_cookies.txt"
    
    if cookies_text:
        with open(cookies_path, "w") as f:
            f.write(cookies_text)
    else:
        local_cookies = os.path.join(os.path.dirname(__file__), "cookies.txt")
        if os.path.exists(local_cookies):
            cookies_path = local_cookies

    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'format': 'best',
        'extract_flat': False,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    if os.path.exists(cookies_path):
        logger.info(f"[[Authenticated Engine]] Using YouTube session cookie file: {cookies_path}")
        ydl_opts['cookiefile'] = cookies_path
    else:
        logger.warning("[[Warning]] No cookie file found. Running unauthenticated extraction.")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise Exception("yt-dlp returned empty info dict")

        if 'entries' in info:
            info = info['entries'][0]

        formats_list = info.get('formats', [])
        combined_streams = []
        audio_streams = []
        seen_resolutions = set()

        for f in formats_list:
            stream_url = f.get('url')
            if not stream_url:
                continue

            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            height = f.get('height')
            ext = f.get('ext', 'mp4')
            filesize = f.get('filesize') or f.get('filesize_approx')

            if vcodec != 'none' and acodec != 'none' and height:
                res_key = f"{height}p"
                if res_key not in seen_resolutions:
                    seen_resolutions.add(res_key)
                    combined_streams.append({
                        "format_id": f.get('format_id'),
                        "quality": res_key,
                        "height": height,
                        "ext": ext,
                        "filesize": format_bytes(filesize),
                        "direct_url": stream_url,
                        "type": "video_audio"
                    })
            elif vcodec == 'none' and acodec != 'none':
                abr = f.get('abr')
                quality_label = f"{int(abr)} kbps" if abr else "Audio"
                audio_streams.append({
                    "format_id": f.get('format_id'),
                    "quality": quality_label,
                    "ext": ext,
                    "filesize": format_bytes(filesize),
                    "direct_url": stream_url,
                    "type": "audio_only"
                })

        combined_streams.sort(key=lambda x: x.get('height', 0), reverse=True)

        if not combined_streams and not audio_streams:
            fallback_url = info.get('url')
            if fallback_url:
                combined_streams.append({
                    "format_id": "best",
                    "quality": "Direct Stream",
                    "ext": info.get('ext', 'mp4'),
                    "filesize": "Direct CDN Link",
                    "direct_url": fallback_url,
                    "type": "video_audio"
                })

        logger.info(f"[[Success]] Extracted {len(combined_streams)} video and {len(audio_streams)} audio streams with session cookies")
        return {
            "success": True,
            "engine": "Authenticated_yt-dlp",
            "title": info.get('title', 'YouTube Video'),
            "thumbnail": info.get('thumbnail'),
            "duration": format_duration(info.get('duration')),
            "uploader": info.get('uploader', 'YouTube Creator'),
            "view_count": f"{info.get('view_count', 0):,}" if info.get('view_count') else "N/A",
            "streams": {
                "combined": combined_streams[:4],
                "audio": audio_streams[:3]
            }
        }

@app.post("/api/extract")
def extract_video_info(payload: ExtractRequest):
    url = payload.url.strip()
    logger.info(f"=== Extraction Request Received for URL: {url} ===")
    
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    video_id = extract_video_id(url)
    if not video_id:
        logger.error(f"Failed to parse video ID from URL: {url}")
        raise HTTPException(status_code=400, detail="Invalid YouTube URL format. Could not parse video ID.")

    try:
        return fetch_authenticated_yt_dlp(url)
    except Exception as e:
        err_msg = str(e)
        logger.error(f"=== Extraction Failed for video_id {video_id}: {err_msg} ===")
        raise HTTPException(
            status_code=400,
            detail=f"Extraction error: {err_msg}"
        )

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "API active."})

import os
import time
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yt_extractor")

app = FastAPI(
    title="YouTube Direct Link Extractor",
    description="Zero-bandwidth serverless video direct link extractor powered by yt-dlp",
    version="1.2.0"
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

def format_bytes(size: Optional[int]) -> str:
    if not size:
        return "Unknown size"
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

def extract_with_clients(url: str, clients: List[str]) -> Dict[str, Any]:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'format': 'best',
        'extract_flat': False,
        'extractor_args': {
            'youtube': {
                'player_client': clients
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    cookies_path = os.getenv("YOUTUBE_COOKIES_PATH", "cookies.txt")
    if os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

@app.post("/api/extract")
def extract_video_info(payload: ExtractRequest):
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    info = None
    last_error = None

    client_strategies = [
        ['ios', 'android'],
        ['mweb', 'web_creator'],
        ['tvhtml5', 'android_vr'],
        ['web']
    ]

    for clients in client_strategies:
        try:
            logger.info(f"Attempting yt-dlp extraction with player clients: {clients}")
            info = extract_with_clients(url, clients)
            if info:
                break
        except Exception as e:
            last_error = e
            logger.warning(f"Strategy {clients} failed: {e}")
            continue

    if not info:
        err_msg = str(last_error) if last_error else "Could not extract video info."
        raise HTTPException(status_code=400, detail=err_msg)

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

    fallback_url = info.get('url')
    if not combined_streams and fallback_url:
        combined_streams.append({
            "format_id": "best",
            "quality": "Direct Stream",
            "ext": info.get('ext', 'mp4'),
            "filesize": "Direct Link",
            "direct_url": fallback_url,
            "type": "video_audio"
        })

    return {
        "success": True,
        "title": info.get('title', 'YouTube Video'),
        "thumbnail": info.get('thumbnail'),
        "duration": format_duration(info.get('duration')),
        "uploader": info.get('uploader', 'Unknown Creator'),
        "view_count": f"{info.get('view_count', 0):,}" if info.get('view_count') else "N/A",
        "streams": {
            "combined": combined_streams[:4],
            "audio": audio_streams[:3]
        }
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

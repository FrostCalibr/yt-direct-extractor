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
    description="Zero-bandwidth serverless video direct link extractor powered by yt-dlp & Piped fallback",
    version="1.3.0"
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

def fetch_piped_fallback(video_id: str) -> Dict[str, Any]:
    """Fallback extraction using public Piped / Invidious instances when datacenter IP is blocked."""
    piped_instances = [
        f"https://pipedapi.kavin.rocks/streams/{video_id}",
        f"https://api.piped.video/streams/{video_id}",
        f"https://piped-api.garudalinux.org/streams/{video_id}"
    ]

    for instance_url in piped_instances:
        try:
            logger.info(f"Attempting fallback stream extraction via Piped instance: {instance_url}")
            req = urllib.request.Request(
                instance_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=6) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    
                    combined_streams = []
                    audio_streams = []

                    # Parse video streams
                    for v in data.get('videoStreams', []):
                        if v.get('url') and v.get('quality'):
                            combined_streams.append({
                                "format_id": "piped_video",
                                "quality": v.get('quality'),
                                "height": int(v.get('quality', '0p').replace('p', '')) if 'p' in str(v.get('quality')) else 0,
                                "ext": v.get('format', 'mp4').lower(),
                                "filesize": "Direct Stream",
                                "direct_url": v.get('url'),
                                "type": "video_audio"
                            })

                    # Parse audio streams
                    for a in data.get('audioStreams', []):
                        if a.get('url'):
                            bitrate = a.get('bitrate')
                            quality_label = f"{int(bitrate / 1000)} kbps" if bitrate else "Audio"
                            audio_streams.append({
                                "format_id": "piped_audio",
                                "quality": quality_label,
                                "ext": a.get('format', 'm4a').lower(),
                                "filesize": "Direct Stream",
                                "direct_url": a.get('url'),
                                "type": "audio_only"
                            })

                    combined_streams.sort(key=lambda x: x.get('height', 0), reverse=True)

                    return {
                        "success": True,
                        "title": data.get('title', 'YouTube Video'),
                        "thumbnail": data.get('thumbnailUrl'),
                        "duration": format_duration(data.get('duration')),
                        "uploader": data.get('uploader', 'YouTube Creator'),
                        "view_count": f"{data.get('views', 0):,}" if data.get('views') else "N/A",
                        "streams": {
                            "combined": combined_streams[:4],
                            "audio": audio_streams[:3]
                        }
                    }
        except Exception as e:
            logger.warning(f"Piped instance {instance_url} failed: {e}")
            continue

    raise Exception("All datacenter bypass strategies and fallback instances were unreachable.")

def extract_with_yt_dlp(url: str) -> Dict[str, Any]:
    # Check if YOUTUBE_COOKIES_TEXT env var is supplied
    cookies_text = os.getenv("YOUTUBE_COOKIES_TEXT")
    cookies_path = "/tmp/youtube_cookies.txt"
    
    if cookies_text:
        with open(cookies_path, "w") as f:
            f.write(cookies_text)

    client_strategies = [
        ['ios', 'android'],
        ['mweb', 'web_creator'],
        ['tvhtml5', 'android_vr'],
        ['web']
    ]

    last_error = None

    for clients in client_strategies:
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
            }
        }

        if os.path.exists(cookies_path):
            ydl_opts['cookiefile'] = cookies_path

        try:
            logger.info(f"Attempting yt-dlp extraction with player clients: {clients}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
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
        except Exception as e:
            last_error = e
            logger.warning(f"yt-dlp strategy {clients} failed: {e}")

    # If all yt-dlp strategies fail due to bot verification, trigger Piped fallback
    video_id = extract_video_id(url)
    if video_id:
        logger.info(f"yt-dlp failed on datacenter IP. Triggering Piped fallback for video_id: {video_id}")
        return fetch_piped_fallback(video_id)

    raise last_error or Exception("Could not extract video info.")

@app.post("/api/extract")
def extract_video_info(payload: ExtractRequest):
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    try:
        return extract_with_yt_dlp(url)
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        raise HTTPException(status_code=400, detail=f"Extraction failed: {str(e)}")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "API active."})

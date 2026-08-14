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
    description="Zero-bandwidth video link extractor with active Invidious & Cobalt multi-fallback",
    version="1.4.0"
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

def fetch_invidious_fallback(video_id: str) -> Dict[str, Any]:
    """Fallback extraction using active Invidious instances."""
    invidious_instances = [
        f"https://inv.tux.pizza/api/v1/videos/{video_id}",
        f"https://invidious.privacydev.net/api/v1/videos/{video_id}",
        f"https://invidious.nerdvpn.de/api/v1/videos/{video_id}",
        f"https://invidious.drgns.space/api/v1/videos/{video_id}"
    ]

    for instance_url in invidious_instances:
        try:
            logger.info(f"Attempting fallback via Invidious API: {instance_url}")
            req = urllib.request.Request(
                instance_url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    
                    combined_streams = []
                    audio_streams = []

                    for format_item in data.get('formatStreams', []):
                        if format_item.get('url') and format_item.get('qualityLabel'):
                            res_val = format_item.get('height', 0)
                            combined_streams.append({
                                "format_id": "invidious_video",
                                "quality": format_item.get('qualityLabel'),
                                "height": res_val,
                                "ext": format_item.get('container', 'mp4'),
                                "filesize": "Direct Stream",
                                "direct_url": format_item.get('url'),
                                "type": "video_audio"
                            })

                    for audio_item in data.get('adaptiveFormats', []):
                        if audio_item.get('url') and 'audio' in audio_item.get('type', ''):
                            bitrate = audio_item.get('bitrate')
                            quality_label = f"{int(int(bitrate) / 1000)} kbps" if bitrate else "Audio"
                            audio_streams.append({
                                "format_id": "invidious_audio",
                                "quality": quality_label,
                                "ext": audio_item.get('container', 'm4a'),
                                "filesize": "Direct Stream",
                                "direct_url": audio_item.get('url'),
                                "type": "audio_only"
                            })

                    combined_streams.sort(key=lambda x: x.get('height', 0), reverse=True)

                    if combined_streams or audio_streams:
                        return {
                            "success": True,
                            "title": data.get('title', 'YouTube Video'),
                            "thumbnail": data.get('videoThumbnails', [{}])[0].get('url') if data.get('videoThumbnails') else None,
                            "duration": format_duration(data.get('lengthSeconds')),
                            "uploader": data.get('author', 'YouTube Creator'),
                            "view_count": f"{data.get('viewCount', 0):,}" if data.get('viewCount') else "N/A",
                            "streams": {
                                "combined": combined_streams[:4],
                                "audio": audio_streams[:3]
                            }
                        }
        except Exception as e:
            logger.warning(f"Invidious instance {instance_url} failed: {e}")
            continue

    raise Exception("Invidious fallback unreachable.")

def fetch_cobalt_fallback(video_id: str) -> Dict[str, Any]:
    """Fallback extraction using Cobalt API."""
    try:
        cobalt_url = "https://api.cobalt.tools/"
        payload = json.dumps({"url": f"https://www.youtube.com/watch?v={video_id}"}).encode('utf-8')
        req = urllib.request.Request(
            cobalt_url,
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0'
            }
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status in [200, 201]:
                data = json.loads(response.read().decode('utf-8'))
                direct_link = data.get('url')
                if direct_link:
                    return {
                        "success": True,
                        "title": f"YouTube Video ({video_id})",
                        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                        "duration": "N/A",
                        "uploader": "YouTube",
                        "view_count": "N/A",
                        "streams": {
                            "combined": [{
                                "format_id": "cobalt_direct",
                                "quality": "Best Stream (Cobalt)",
                                "height": 720,
                                "ext": "mp4",
                                "filesize": "Direct Stream",
                                "direct_url": direct_link,
                                "type": "video_audio"
                            }],
                            "audio": []
                        }
                    }
    except Exception as e:
        logger.warning(f"Cobalt API failed: {e}")

    raise Exception("Cobalt fallback unreachable.")

@app.post("/api/extract")
def extract_video_info(payload: ExtractRequest):
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    video_id = extract_video_id(url)

    # Strategy 1: yt-dlp
    client_strategies = [
        ['ios', 'android'],
        ['mweb', 'web_creator'],
        ['tvhtml5', 'android_vr'],
        ['web']
    ]

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

        try:
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

                    if combined_streams or audio_streams:
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
        except Exception:
            continue

    # Strategy 2: Invidious API
    if video_id:
        try:
            return fetch_invidious_fallback(video_id)
        except Exception as e:
            logger.warning(f"Invidious fallback failed: {e}")

    # Strategy 3: Cobalt API
    if video_id:
        try:
            return fetch_cobalt_fallback(video_id)
        except Exception as e:
            logger.warning(f"Cobalt fallback failed: {e}")

    # Strategy 4: Return client fallback instruction for browser residential fetch
    if video_id:
        return {
            "success": True,
            "client_fallback": True,
            "video_id": video_id
        }

    raise HTTPException(status_code=400, detail="Could not extract video links.")

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "API active."})

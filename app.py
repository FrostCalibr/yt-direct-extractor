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
    description="Multi-engine YouTube stream extractor with WEB_EMBEDDED_PLAYER & Cookie support",
    version="4.2.0"
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

def parse_cipher_url(cipher_str: str) -> Optional[str]:
    try:
        parsed = urllib.parse.parse_qs(cipher_str)
        url = parsed.get('url', [None])[0]
        sig = parsed.get('s', [None])[0]
        sp = parsed.get('sp', ['sig'])[0]
        if url:
            if sig:
                return f"{url}&{sp}={sig}"
            return url
    except Exception as e:
        logger.warning(f"Failed to parse cipher string: {e}")
    return None

def fetch_innertube_client(video_id: str, client_name: str, client_version: str, extra_client_data: dict, user_agent: str) -> Dict[str, Any]:
    endpoint = "https://www.youtube.com/youtubei/v1/player"
    
    client_ctx = {
        "clientName": client_name,
        "clientVersion": client_version,
        "hl": "en",
        "gl": "US"
    }
    client_ctx.update(extra_client_data)
    
    payload = {
        "context": {
            "client": client_ctx
        },
        "videoId": video_id,
        "thirdParty": {
            "embedUrl": f"https://www.youtube.com/embed/{video_id}"
        },
        "contentCheckOk": True,
        "racyCheckOk": True
    }
    
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'User-Agent': user_agent,
            'Referer': f'https://www.youtube.com/embed/{video_id}'
        }
    )
    
    with urllib.request.urlopen(req, timeout=6) as resp:
        if resp.status != 200:
            raise Exception(f"InnerTube status {resp.status}")
        
        data = json.loads(resp.read().decode('utf-8'))
        playability = data.get('playabilityStatus', {})
        status = playability.get('status')
        
        if status != 'OK':
            reason = playability.get('reason', 'Playability status not OK')
            raise Exception(f"YouTube status {status}: {reason}")
            
        details = data.get('videoDetails', {})
        title = details.get('title', f'YouTube Video ({video_id})')
        author = details.get('author', 'YouTube Creator')
        length_sec = int(details.get('lengthSeconds', 0))
        duration_str = f"{length_sec // 60}:{length_sec % 60:02d}" if length_sec else "N/A"
        thumbnails = details.get('thumbnail', {}).get('thumbnails', [{}])
        thumb_url = thumbnails[-1].get('url') if thumbnails else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        
        streaming_data = data.get('streamingData', {})
        formats = streaming_data.get('formats', []) + streaming_data.get('adaptiveFormats', [])
        
        combined_streams = []
        audio_streams = []
        seen_resolutions = set()

        for fmt in formats:
            direct_url = fmt.get('url')
            if not direct_url and 'signatureCipher' in fmt:
                direct_url = parse_cipher_url(fmt['signatureCipher'])
                
            if not direct_url:
                continue

            mime = fmt.get('mimeType', '')
            quality_label = fmt.get('qualityLabel') or f"{fmt.get('height', 0)}p"
            height = fmt.get('height', 0)
            
            if 'video' in mime:
                if height and quality_label not in seen_resolutions:
                    seen_resolutions.add(quality_label)
                    combined_streams.append({
                        "format_id": str(fmt.get('itag')),
                        "quality": quality_label,
                        "height": height,
                        "ext": "mp4" if "mp4" in mime else "webm",
                        "filesize": "Direct CDN Stream",
                        "direct_url": direct_url,
                        "type": "video_audio"
                    })
            elif 'audio' in mime:
                bitrate = fmt.get('bitrate')
                q_text = f"{bitrate // 1000} kbps" if bitrate else "Audio"
                audio_streams.append({
                    "format_id": str(fmt.get('itag')),
                    "quality": q_text,
                    "ext": "m4a" if "mp4" in mime else "webm",
                    "filesize": "Direct CDN Stream",
                    "direct_url": direct_url,
                    "type": "audio_only"
                })

        combined_streams.sort(key=lambda x: x.get('height', 0), reverse=True)

        if not combined_streams and not audio_streams:
            raise Exception("No playable stream URLs returned")

        logger.info(f"[[Success: {client_name} Embedded]] Extracted {len(combined_streams)} video & {len(audio_streams)} audio streams for {video_id}")
        return {
            "success": True,
            "engine": f"InnerTube_{client_name}_Embedded",
            "title": title,
            "thumbnail": thumb_url,
            "duration": duration_str,
            "uploader": author,
            "view_count": f"{int(details.get('viewCount', 0)):,}" if details.get('viewCount') else "N/A",
            "streams": {
                "combined": combined_streams[:4],
                "audio": audio_streams[:3]
            }
        }

def fetch_innertube_multi_client(video_id: str) -> Dict[str, Any]:
    client_configs = [
        {
            "name": "WEB_EMBEDDED_PLAYER",
            "version": "5.20240801.01.00",
            "extra": {},
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
        },
        {
            "name": "IOS",
            "version": "19.29.1",
            "extra": {"deviceMake": "Apple", "deviceModel": "iPhone16,2", "osName": "iOS", "osVersion": "17.5.1"},
            "ua": "com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X; en_US)"
        },
        {
            "name": "ANDROID_VR",
            "version": "1.59.19",
            "extra": {"deviceMake": "Oculus", "deviceModel": "Quest 3", "osName": "Android", "osVersion": "12"},
            "ua": "Mozilla/5.0 (Linux; Android 12; Quest 3) AppleWebKit/537.36 (KHTML, like Gecko) OculusBrowser/32.0.0.8.18 Mobile Safari/537.36"
        },
        {
            "name": "TVHTML5",
            "version": "7.20240801.00.00",
            "extra": {},
            "ua": "Mozilla/5.0 (SmartHub; SMART-TV; U; Linux/SmartTV) AppleWebKit/537.42 (KHTML, like Gecko) SmartTV Safari/537.42"
        }
    ]

    last_err = None
    for config in client_configs:
        try:
            logger.info(f"[[Engine 1]] Trying InnerTube client payload: {config['name']}")
            return fetch_innertube_client(
                video_id,
                config['name'],
                config['version'],
                config['extra'],
                config['ua']
            )
        except Exception as e:
            last_err = e
            logger.warning(f"InnerTube client {config['name']} failed: {e}")
            continue

    raise last_err or Exception("All InnerTube client payloads failed")

def fetch_ytdlp_with_cookies(url: str) -> Dict[str, Any]:
    cookies_text = os.getenv("YOUTUBE_COOKIES_TEXT")
    if not cookies_text:
        raise Exception("YOUTUBE_COOKIES_TEXT environment variable not configured")
        
    cookies_path = "/tmp/youtube_cookies.txt"
    with open(cookies_path, "w") as f:
        f.write(cookies_text)
        
    logger.info("[[Engine 2]] Attempting yt-dlp extraction with environment cookies")
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'cookiefile': cookies_path,
        'format': 'best'
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise Exception("yt-dlp returned empty info dict")
            
        combined_streams = []
        for f in info.get('formats', []):
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
            "engine": "yt-dlp_cookies",
            "title": info.get('title', 'YouTube Video'),
            "thumbnail": info.get('thumbnail'),
            "duration": str(info.get('duration', '')),
            "uploader": info.get('uploader', 'YouTube'),
            "view_count": str(info.get('view_count', '')),
            "streams": { "combined": combined_streams[:4], "audio": [] }
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

    errors = []

    # Engine 1: Multi-Client InnerTube API (WEB_EMBEDDED_PLAYER / IOS / ANDROID_VR / TVHTML5)
    try:
        return fetch_innertube_multi_client(video_id)
    except Exception as e:
        err_msg = f"Engine 1 (InnerTube Multi-Client) failed: {str(e)}"
        logger.warning(err_msg)
        errors.append(err_msg)

    # Engine 2: yt-dlp with YOUTUBE_COOKIES_TEXT
    try:
        return fetch_ytdlp_with_cookies(url)
    except Exception as e:
        err_msg = f"Engine 2 (yt-dlp cookies) failed: {str(e)}"
        logger.warning(err_msg)
        errors.append(err_msg)

    error_summary = " | ".join(errors)
    logger.error(f"=== Extraction Failed for video_id {video_id}. Diagnostic trace: {error_summary} ===")
    raise HTTPException(
        status_code=400,
        detail=f"Extraction failed for video ID '{video_id}'. Diagnostics: {error_summary}"
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

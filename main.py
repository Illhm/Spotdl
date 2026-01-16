from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests 
import uvicorn 
from typing import Optional
import re
import time

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for the web player
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TrackInfo(BaseModel):
    title: str
    artist: str
    thumbnail: str | None
    media_url: str

SPOTMATE_HOME = "https://spotmate.online/en1"
SPOTMATE_ORIGIN = "https://spotmate.online"
UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"

def fetch_csrf_token(session: requests.Session, home_url: str) -> str:
    resp = session.get(
        home_url,
        headers={"User-Agent": UA, "Referer": home_url},
        timeout=20
    )
    resp.raise_for_status()
    match = re.search(r'name="csrf-token"\s+content="([^"]+)"', resp.text)
    if not match:
        raise ValueError("CSRF token not found on home page")
    return match.group(1)

def post_json(session: requests.Session, url: str, payload: dict, csrf_token: str, referer: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-CSRF-TOKEN": csrf_token,
        "User-Agent": UA,
        "Referer": referer,
        "Origin": SPOTMATE_ORIGIN,
        "Accept": "*/*",
    }
    resp = session.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()

def poll_conversion_task(session: requests.Session, task_id: str, max_attempts: int = 40) -> dict:
    task_url = f"{SPOTMATE_ORIGIN}/tasks/{task_id}"
    headers = {"User-Agent": UA, "Referer": SPOTMATE_HOME}
    for _ in range(max_attempts):
        time.sleep(4.5)
        resp = session.get(task_url, headers=headers, timeout=20)
        if not resp.ok:
            continue
        try:
            payload = resp.json()
        except ValueError:
            continue
        return payload
    return {}

def parse_track_data(payload: dict) -> dict:
    if payload.get("type") != "track":
        raise HTTPException(status_code=400, detail="Only Spotify track URLs are supported")
    title = payload.get("name") or "Unknown Title"
    artists = payload.get("artists") or []
    artist = artists[0].get("name") if isinstance(artists, list) and artists else "Unknown Artist"
    images = (payload.get("album") or {}).get("images") or []
    thumbnail = images[0].get("url") if isinstance(images, list) and images else None
    return {"title": title, "artist": artist, "thumbnail": thumbnail}

def extract_download_url(payload: dict) -> str | None:
    if payload.get("error") is False and payload.get("url"):
        return payload.get("url")
    info = payload.get("data") or {}
    if info.get("url"):
        return info.get("url")
    if isinstance(info.get("result"), dict) and info["result"].get("url"):
        return info["result"].get("url")
    return None

def get_track_data_internal(url: str) -> dict:
    if "open.spotify.com/track/" not in url:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL")

    session = requests.Session()

    try:
        csrf_token = fetch_csrf_token(session, SPOTMATE_HOME)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch CSRF token: {str(e)}")

    try:
        track_payload = post_json(
            session,
            f"{SPOTMATE_ORIGIN}/getTrackData",
            {"spotify_url": url},
            csrf_token,
            SPOTMATE_HOME,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch track info: {str(e)}")

    track_data = parse_track_data(track_payload)

    try:
        convert_payload = post_json(
            session,
            f"{SPOTMATE_ORIGIN}/convert",
            {"urls": url},
            csrf_token,
            SPOTMATE_HOME,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start conversion: {str(e)}")

    media_url = extract_download_url(convert_payload)
    task_id = convert_payload.get("task_id") or convert_payload.get("taskId")
    if not media_url and task_id:
        task_payload = poll_conversion_task(session, task_id)
        media_url = extract_download_url(task_payload)

    if not media_url:
        raise HTTPException(status_code=404, detail="No media found for this track")

    return {
        "title": track_data["title"],
        "artist": track_data["artist"],
        "thumbnail": track_data["thumbnail"],
        "media_url": media_url,
    }

def render_player(track: dict) -> str:
    # Minimal player with background image
    thumb = track.get("thumbnail") or ""
    title = track.get("title")
    artist = track.get("artist")
    media = track.get("media_url")
    
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>{title} - {artist}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 0;
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            background-color: #000;
            overflow: hidden;
        }}
        .bg-image {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-image: url('{thumb}');
            background-position: center;
            background-size: cover;
            filter: blur(20px) brightness(0.5);
            z-index: 1;
        }}
        .player-card {{
            position: relative;
            z-index: 2;
            background: rgba(0,0,0,0.6);
            backdrop-filter: blur(10px);
            padding: 30px;
            border-radius: 20px;
            text-align: center;
            color: white;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
            max-width: 90%;
            width: 300px;
        }}
        .album-art {{
            width: 200px;
            height: 200px;
            border-radius: 10px;
            background-image: url('{thumb}');
            background-size: cover;
            background-position: center;
            margin: 0 auto 20px auto;
            box-shadow: 0 5px 15px rgba(0,0,0,0.5);
        }}
        h2 {{
            margin: 0 0 5px 0;
            font-size: 1.2rem;
        }}
        p {{
            margin: 0 0 20px 0;
            color: #ccc;
            font-size: 0.9rem;
        }}
        audio {{
            width: 100%;
            border-radius: 30px;
        }}
    </style>
</head>
<body>
    <div class="bg-image"></div>
    <div class="player-card">
        <div class="album-art"></div>
        <h2>{title}</h2>
        <p>{artist}</p>
        <audio controls autoplay>
            <source src="{media}" type="audio/mpeg">
            Your browser does not support the audio element.
        </audio>
    </div>
</body>
</html>
"""

def render_home_form() -> str:
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Spotify Web Player</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: #121212;
            color: white;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            margin: 0;
        }
        .container {
            text-align: center;
            background: #282828;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 4px 10px rgba(0,0,0,0.3);
        }
        h1 { color: #1db954; }
        input {
            padding: 10px;
            width: 250px;
            border-radius: 20px;
            border: none;
            outline: none;
        }
        button {
            padding: 10px 20px;
            background: #1db954;
            color: white;
            border: none;
            border-radius: 20px;
            cursor: pointer;
            font-weight: bold;
            margin-left: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Spotify Player</h1>
        <form action="/" method="get">
            <input type="text" name="url" placeholder="Paste Spotify URL..." required>
            <button type="submit">GO</button>
        </form>
    </div>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def read_root(url: Optional[str] = None):
    if url:
        try:
            track_data = get_track_data_internal(url)
            return render_player(track_data)
        except HTTPException as e:
            return f"<h1>Error: {e.detail}</h1>"
        except Exception as e:
            return f"<h1>Error: {str(e)}</h1>"
    else:
        return render_home_form()

@app.get("/info", response_model=TrackInfo)
def get_track_info(url: str):
    data = get_track_data_internal(url)
    return data

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

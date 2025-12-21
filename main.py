from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
import spotify_dl_v3 as sdl
import uvicorn
from typing import Optional

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

def get_track_data_internal(url: str) -> dict:
    if "open.spotify.com/track/" not in url:
        raise HTTPException(status_code=400, detail="Invalid Spotify URL")

    session = requests.Session()
    
    # 1. Discovery
    try:
        ajax_url, nonce = sdl.fetch_home_and_discover(session, sdl.DEFAULT_BASE)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to discover API: {str(e)}")

    # 2. Call API
    try:
        code, data = sdl.call_info_api(session, ajax_url, url, nonce, referer=sdl.DEFAULT_BASE)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Failed to call info API: {str(e)}")

    if code != 200:
        raise HTTPException(status_code=code, detail="Upstream API error")

    # 3. Parse Response using helper from spotify_dl_v3
    try:
        title, artist, media_url, thumbnail = sdl.extract_track_details(data)
    except Exception as e:
         raise HTTPException(status_code=500, detail=f"Failed to parse response: {str(e)}")
            
    if not media_url:
        raise HTTPException(status_code=404, detail="No media found for this track")
        
    return {
        "title": title,
        "artist": artist,
        "thumbnail": thumbnail,
        "media_url": media_url
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
    uvicorn.run(app, host="0.0.0.0", port=8000)

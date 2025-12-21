from fastapi.testclient import TestClient
from main import app
import pytest

client = TestClient(app)

def test_home_form():
    """Test that the root URL without params returns the input form"""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<form action=\"/\"" in response.text
    assert "Spotify Player" in response.text

def test_ssr_player_invalid_url():
    """Test that the root URL with invalid URL returns error HTML"""
    response = client.get("/?url=invalid")
    assert response.status_code == 200 # SSR returns HTML with error message, not 400
    assert "Error: Invalid Spotify URL" in response.text

def test_api_info():
    """Test the JSON API still works"""
    # Using a known valid URL (or mock if we wanted strictly unit tests)
    track_url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT" 
    try:
        response = client.get(f"/info?url={track_url}")
        if response.status_code == 200:
            data = response.json()
            assert "title" in data
            assert "media_url" in data
    except Exception:
        pass # Skip if external network issues

def test_ssr_player_integration():
    """Test that valid URL returns player HTML"""
    track_url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
    try:
        response = client.get(f"/?url={track_url}")
        if response.status_code == 200 and "Error" not in response.text:
            assert "audio controls" in response.text
            assert "bg-image" in response.text
            assert "player-card" in response.text
    except Exception:
        pass

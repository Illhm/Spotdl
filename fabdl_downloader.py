"""FabDL Spotify track downloader.

This script reproduces the observed request/response flow used by spodownloader.com
for obtaining an MP3 download link from the FabDL API. Given a Spotify track URL it
performs the following steps:

1. Request track metadata to obtain the internal `gid` and `track_id`.
2. Trigger the MP3 conversion task for the track.
3. Poll the conversion progress until the download link becomes available.
4. Download the MP3 file to disk.

Usage:
    python fabdl_downloader.py "https://open.spotify.com/track/5WOSNVChcadlsCRiqXE45K"

An optional ``--output`` argument can be used to override the default output file name.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

BASE_URL = "https://api.fabdl.com"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://spodownloader.com",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"
    ),
}


class FabDLClient:
    """HTTP client that mirrors the observed request flow for the FabDL API."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def _get_json(self, url: str) -> Dict[str, Any]:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "result" not in data:
            raise ValueError(f"Unexpected response structure from {url!r}: {data!r}")
        return data["result"]

    def get_track_metadata(self, spotify_url: str) -> Dict[str, Any]:
        params = {"url": spotify_url}
        response = self.session.get(f"{BASE_URL}/spotify/get", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "result" not in data:
            raise ValueError(f"Unexpected metadata response: {data!r}")
        return data["result"]

    def request_conversion(self, gid: int, track_id: str) -> Dict[str, Any]:
        url = f"{BASE_URL}/spotify/mp3-convert-task/{gid}/{track_id}"
        return self._get_json(url)

    def poll_progress(
        self,
        tid: str,
        interval: float = 2.0,
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        url = f"{BASE_URL}/spotify/mp3-convert-progress/{tid}"
        deadline = time.time() + timeout
        while True:
            result = self._get_json(url)
            status = result.get("status")
            download_url = result.get("download_url")
            if status == 3 and download_url:
                return result
            if time.time() >= deadline:
                raise TimeoutError("Timed out waiting for conversion to complete")
            time.sleep(interval)

    def download_file(self, download_path: str, output_path: Path) -> Path:
        if download_path.startswith("/"):
            download_url = f"{BASE_URL}{download_path}"
        else:
            download_url = download_path
        with self.session.get(download_url, stream=True, timeout=30) as response:
            response.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file_obj.write(chunk)
        return output_path


def sanitize_filename(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[\s]+", " ", value)
    value = re.sub(r"[^\w\-\.,\s]", "", value, flags=re.UNICODE)
    value = value.replace(" ", "_")
    return value or "download"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Download Spotify tracks via FabDL API")
    parser.add_argument("spotify_url", help="Spotify track URL to download")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file path. Defaults to '<artist>-<title>.mp3'.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between progress checks (default: 2)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Maximum seconds to wait for conversion (default: 120)",
    )
    args = parser.parse_args(argv)

    client = FabDLClient()

    print("Fetching track metadata...")
    metadata = client.get_track_metadata(args.spotify_url)
    gid = metadata.get("gid")
    track_id = metadata.get("id") or metadata.get("track_id")
    track_name = metadata.get("name", "track")
    artists = metadata.get("artists", "artist")

    if gid is None or track_id is None:
        raise RuntimeError("Missing gid or track_id in metadata response")

    print(f"Requesting conversion task for {track_name} by {artists}...")
    task = client.request_conversion(gid, track_id)
    tid = task.get("tid")
    if tid is None:
        raise RuntimeError("Conversion task response did not include a 'tid'")

    print("Waiting for conversion to finish...")
    progress = client.poll_progress(tid, interval=args.poll_interval, timeout=args.timeout)

    download_url = progress.get("download_url")
    if not download_url:
        raise RuntimeError("Conversion completed but no download URL was provided")

    if args.output:
        output_path = args.output
    else:
        filename = sanitize_filename(f"{artists}-{track_name}") + ".mp3"
        output_path = Path(filename)

    print(f"Downloading MP3 to {output_path}...")
    client.download_file(download_url, output_path)
    print("Download completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unofficial Spotify Downloader (Downloaderize.com)
-------------------------------------------------
- Discover WP AJAX endpoint + nonce from homepage.
- POST action=spotify_downloader_get_info with the Spotify track URL.
- Download the first media URL returned.
"""
import re
import sys
import os
import json
import time
import argparse
from urllib.parse import urljoin, urlparse, parse_qs
import requests

DEFAULT_BASE = "https://spotify.downloaderize.com/"
UA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"

def debug(msg: str) -> None:
    print(f"[DEBUG] {msg}", flush=True)

def _normalize_url(u: str) -> str:
    if not u:
        return u
    u = u.strip()
    # Unescape JSON-style escaped slashes
    u = u.replace("\/", "/")
    # Handle protocol-relative URLs
    if u.startswith("//"):
        u = "https:" + u
    return u

def _extract_obj_fields(html_text: str, var_names):
    ajax_url = None
    nonce = None
    for varname in var_names:
        # match: var <name> = { ... };
        m = re.search(rf"{varname}\s*=\s*\{{(.*?)\}}", html_text, re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        blob = m.group(1)
        m_ajax = re.search(r"ajaxurl['\"]?\s*:\s*['\"]([^'\"]+)['\"]", blob)
        if m_ajax and not ajax_url:
            ajax_url = m_ajax.group(1)
        m_nonce = re.search(r"nonce['\"]?\s*:\s*['\"]([0-9a-zA-Z]{8,})['\"]", blob)
        if m_nonce and not nonce:
            nonce = m_nonce.group(1)
        if ajax_url and nonce:
            break
    return ajax_url, nonce

def fetch_home_and_discover(session: requests.Session, base_url: str):
    """
    Returns (ajax_url, nonce)
    Tries common WordPress patterns: window.ajaxurl, localized vars, data-nonce.
    """
    base_url = base_url if base_url.endswith("/") else base_url + "/"
    resp = session.get(base_url, headers={"User-Agent": UA, "Referer": base_url}, timeout=20)
    resp.raise_for_status()
    html_text = resp.text

    # 1) window.ajaxurl or ajaxurl = '...'
    ajax_url = None
    m = re.search(r"(?:window\.)?ajaxurl\s*=\s*['\"]([^'\"]+)['\"]", html_text, re.IGNORECASE)
    if m:
        ajax_url = m.group(1)

    # 2) Localized variables that often carry ajaxurl + nonce
    var_candidates = [
        r"spotify_downloader",
        r"spotifyDownloader",
        r"spotify_downloader_vars",
        r"sd_vars",
        r"sts_vars",
        r"stsData",
    ]
    a2, n2 = _extract_obj_fields(html_text, var_candidates)
    if a2 and not ajax_url:
        ajax_url = a2
    nonce = n2

    # 3) data-nonce attributes
    if not nonce:
        m3 = re.search(r'data-nonce=["\']([0-9a-zA-Z]+)["\']', html_text)
        if m3:
            nonce = m3.group(1)

    # 4) Generic search anywhere for ajaxurl if still missing
    if not ajax_url:
        m4 = re.search(r"ajaxurl['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", html_text, re.IGNORECASE)
        if m4:
            ajax_url = m4.group(1)

    # 5) Fallback to default WP endpoint
    if not ajax_url:
        ajax_url = urljoin(base_url, "wp-admin/admin-ajax.php")

    # Normalize
    ajax_url = _normalize_url(ajax_url)
    return ajax_url, nonce

def call_info_api(session: requests.Session, ajax_url: str, track_url: str, nonce: str, referer: str):
    """
    POST form data:
      action=spotify_downloader_get_info
      url=<track>
      nonce=<nonce-from-home> (optional if not found)
    """
    data = {
        "action": "spotify_downloader_get_info",
        "url": track_url,
    }
    if nonce:
        data["nonce"] = nonce

    origin = f"{urlparse(referer).scheme}://{urlparse(referer).netloc}"
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": referer.rstrip("/"),
    }

    resp = session.post(ajax_url, data=data, headers=headers, timeout=30)
    text = resp.text
    try:
        payload = resp.json()
    except Exception:
        # try best-effort JSON extraction
        j = re.search(r"\{.*\}", text, re.S)
        payload = json.loads(j.group(0)) if j else {"raw": text}
    return resp.status_code, payload

def sanitize_filename(name: str) -> str:
    name = name.strip().replace("/", "-").replace("\\", "-")
    name = re.sub(r"[^\w\s\-\.,\(\)\[\]&]+", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "output"

def choose_media(data: dict):
    medias = data.get("medias") or []
    if not isinstance(medias, list) or not medias:
        return None
    # prefer mp3
    for m in medias:
        if isinstance(m, dict) and isinstance(m.get("url"), str) and ".mp3" in m["url"].lower():
            return m
    # fallback first with url
    for m in medias:
        if isinstance(m, dict) and isinstance(m.get("url"), str) and m["url"]:
            return m
    return None

def infer_tags_from_query(media_url: str, fallback_title: str, fallback_artist: str):
    try:
        q = parse_qs(urlparse(media_url).query or "")
        name = q.get("name", [fallback_title])[0]
        artist = q.get("artist", [fallback_artist])[0]
        return name, artist
    except Exception:
        return fallback_title, fallback_artist

def pick_extension_from_headers_and_url(headers: dict, media_url: str, forced_ext: str = None) -> str:
    if forced_ext:
        if not forced_ext.startswith("."):
            forced_ext = "." + forced_ext
        return forced_ext
    ct = (headers.get("Content-Type") or "").lower()
    cd = headers.get("Content-Disposition") or ""
    # map by content-type
    ct_map = {
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/aac": ".aac",
        "audio/m4a": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/webm": ".webm",
    }
    for k, v in ct_map.items():
        if k in ct:
            return v
    # try Content-Disposition filename
    m = re.search(r'filename="([^"]+)"', cd)
    if m:
        _, ext = os.path.splitext(m.group(1))
        if ext:
            return ext
    # try URL path extension
    path_ext = os.path.splitext(urlparse(media_url).path)[1]
    if path_ext:
        return path_ext
    # default
    return ".mp3"

def extract_track_details(payload: dict):
    """
    Helper to extract details from the payload logic.
    Returns (title, artist, media_url, thumbnail)
    """
    # Some servers wrap in 'data' or 'result'
    for key in ("data", "result"):
        if isinstance(payload.get(key), dict) and any(k in payload[key] for k in ("medias", "title", "author", "artists")):
            payload = payload[key]
            break

    title = payload.get("title") or "Unknown Title"
    artist = payload.get("author") or payload.get("artists") or "Unknown Artist"
    
    media = choose_media(payload)
    media_url = media.get("url") if media else None
    
    # Use name/artist from URL query if available
    if media_url:
        title, artist = infer_tags_from_query(media_url, title, artist)
        
    thumb = payload.get("thumbnail") or payload.get("image")
    return title, artist, media_url, thumb

def stream_download(session: requests.Session, url: str, out_path_base: str, referer: str, forced_ext: str = None):
    headers = {"User-Agent": UA, "Referer": referer}
    with session.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        # decide extension before creating file
        ext = pick_extension_from_headers_and_url(r.headers, url, forced_ext=forced_ext)
        out_path = out_path_base if out_path_base.endswith(ext) else (out_path_base + ext)
        total = int(r.headers.get("Content-Length") or 0)
        chunk = 8192
        done = 0
        start = time.time()
        with open(out_path, "wb") as f:
            for part in r.iter_content(chunk_size=chunk):
                if not part:
                    continue
                f.write(part)
                done += len(part)
                if total:
                    pct = (done / total) * 100.0
                    sys.stdout.write(f"\rDownloading: {pct:0.1f}% ({done}/{total} bytes)")
                else:
                    sys.stdout.write(f"\rDownloading: {done} bytes")
                sys.stdout.flush()
        dur = time.time() - start
        sys.stdout.write(f"\nDone in {dur:0.2f}s → {out_path}\n")
        return out_path

def main():
    ap = argparse.ArgumentParser(description="Downloaderize Spotify track downloader (unofficial).")
    ap.add_argument("track_url", help="Spotify track URL, e.g. https://open.spotify.com/track/<id>")
    ap.add_argument("-b", "--base", default=DEFAULT_BASE, help="Base site (default: %(default)s)")
    ap.add_argument("--ajax-url", default=None, help="Override AJAX URL if discovery fails")
    ap.add_argument("--nonce", default=None, help="Override nonce if discovery fails")
    ap.add_argument("-o", "--output", default=None, help="Explicit output filename without extension (we'll infer)")
    ap.add_argument("--ext", default=None, help="Force output extension, e.g. mp3")
    ap.add_argument("--print-json", action="store_true", help="Print parsed JSON and exit (no download)")
    ap.add_argument("--debug", action="store_true", help="Verbose debug output")
    args = ap.parse_args()

    if "open.spotify.com/track/" not in args.track_url:
        print("ERROR: Please provide a valid Spotify track URL (https://open.spotify.com/track/...).", file=sys.stderr)
        sys.exit(2)

    sess = requests.Session()

    # Discovery (allow override)
    ajax_url = _normalize_url(args.ajax_url) if args.ajax_url else None
    nonce = args.nonce if args.nonce else None

    if not ajax_url or not nonce:
        discovered_ajax, discovered_nonce = fetch_home_and_discover(sess, args.base)
        if not ajax_url:
            ajax_url = discovered_ajax
        if not nonce:
            nonce = discovered_nonce

    if args.debug:
        debug(f"Fetching homepage to discover AJAX + nonce: {args.base}")
        debug(f"AJAX URL: {ajax_url}")
        debug(f"Nonce: {nonce or '(none)'}")

    # Call API
    code, data = call_info_api(sess, ajax_url, args.track_url, nonce, referer=args.base)
    if args.debug:
        debug(f"API HTTP {code}")

    if not isinstance(data, dict):
        print("ERROR: Unexpected response type; not JSON object.", file=sys.stderr)
        print(str(data)[:800])
        sys.exit(1)

    title, artist, media_url, thumb = extract_track_details(data)
    
    if not media_url:
        print("ERROR: No downloadable media found in response.", file=sys.stderr)
        print(json.dumps(data, indent=2, ensure_ascii=False)[:1200])
        sys.exit(1)

    print(f"Track: {title}\nArtist: {artist}")
    if thumb:
        print(f"Thumbnail: {thumb}")

    # Determine output base name
    if args.output:
        out_base = args.output
    else:
        out_base = sanitize_filename(f"{artist} - {title}")

    out_base_abs = os.path.abspath(out_base)
    print(f"Downloading media: {media_url}")
    final_path = stream_download(sess, media_url, out_base_abs, referer=args.base, forced_ext=args.ext)

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Aborted.", file=sys.stderr)
        sys.exit(130)

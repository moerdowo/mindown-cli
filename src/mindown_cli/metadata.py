"""Post-download audio tagger.

For an audio file produced by yt-dlp, look up the track on Apple's iTunes
Search API, fetch lyrics from LRCLib, fetch a 1200x1200 cover, and have
ffmpeg rewrite the file in place with full ID3v2.3 / iTunes-atom metadata.

Mirrors Sources/Mindown/MetadataEnricher.swift.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import ui
from .ai_client import AIClient, AIError
from .config import Config

USER_AGENT = "Mindown-CLI/0.1 (https://github.com/moerdowo/mindown)"


@dataclass
class EnrichResult:
    applied: bool
    message: str


def _http_json(url: str, timeout: float = 15.0) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        return None


def _http_bytes(url: str, timeout: float = 15.0) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


def cleaned_query(raw: str) -> str:
    s = raw or ""
    patterns = [
        r"\(\s*official\s*(music)?\s*(video|audio)\s*\)",
        r"\[\s*official\s*(music)?\s*(video|audio)\s*\]",
        r"\(\s*lyric[s]?\s*(video)?\s*\)",
        r"\[\s*lyric[s]?\s*(video)?\s*\]",
        r"\(\s*visualiz(er|ation)\s*\)",
        r"\(\s*audio\s*\)",
        r"\[\s*audio\s*\]",
        r"\(\s*hd\s*\)",
        r"\[\s*hd\s*\]",
        r"\(\s*4k\s*\)",
        r"\(\s*explicit\s*\)",
        r"\(\s*radio\s*edit\s*\)",
        r"\(\s*remaster(ed)?\s*\d*\s*\)",
        r"\bft\.?\b",
        r"\bfeat\.?\b",
    ]
    for pattern in patterns:
        s = re.sub(pattern, " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*-\s*topic\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _itunes_search(query: str) -> Optional[Dict[str, Any]]:
    qs = urllib.parse.urlencode({
        "term": query,
        "entity": "song",
        "limit": "5",
        "country": "us",
    })
    payload = _http_json(f"https://itunes.apple.com/search?{qs}")
    if not payload:
        return None
    for r in payload.get("results", []):
        if r.get("trackName"):
            return r
    return None


def _itunes_album_copyright(collection_id: int) -> Optional[str]:
    qs = urllib.parse.urlencode({"id": str(collection_id), "entity": "album"})
    payload = _http_json(f"https://itunes.apple.com/lookup?{qs}")
    if not payload:
        return None
    for r in payload.get("results", []):
        if r.get("wrapperType") == "collection" and r.get("copyright"):
            return r["copyright"]
    return None


def _lrclib_lyrics(
    artist: str,
    track: str,
    album: Optional[str],
    duration_seconds: Optional[int],
) -> Optional[str]:
    params: List[Tuple[str, str]] = [
        ("artist_name", artist),
        ("track_name", track),
    ]
    if album:
        params.append(("album_name", album))
    if duration_seconds:
        params.append(("duration", str(duration_seconds)))
    qs = urllib.parse.urlencode(params)
    payload = _http_json(f"https://lrclib.net/api/get?{qs}")
    if not payload:
        return None
    if payload.get("instrumental"):
        return None
    plain = (payload.get("plainLyrics") or "").strip()
    return plain or None


REFUSAL_NEEDLES = (
    "i can't", "i cannot", "i'm unable", "i am unable",
    "i won't", "i will not",
    "due to copyright", "i don't have access",
    "as an ai", "i'm sorry", "no_lyrics",
)


def _ai_lyrics_fallback(
    cfg: Config,
    artist: str,
    track: str,
    album: Optional[str],
) -> Optional[str]:
    if not cfg.ai_lyrics_fallback or not cfg.is_configured:
        return None
    album_part = f' from the album "{album}"' if album else ""
    user_prompt = (
        f'Provide the full lyrics for the song "{track}" by {artist}{album_part}.\n\n'
        "Rules:\n"
        "- Return ONLY the lyrics text — no titles, no artist line, no commentary, no markdown formatting.\n"
        "- Use a single newline between lines and a blank line between verses / sections.\n"
        "- If you do not know the actual lyrics, respond with EXACTLY the token: NO_LYRICS\n"
        "- Do not invent or paraphrase lyrics."
    )
    client = AIClient(cfg.api_key, cfg.base_url, cfg.model)
    try:
        choice = client.chat([
            {"role": "system",
             "content": "You are a song-lyrics retrieval helper. You output plain-text lyrics only."},
            {"role": "user", "content": user_prompt},
        ])
    except AIError:
        return None
    raw = (choice.content or "").strip()
    if not raw or raw == "NO_LYRICS":
        return None
    lowered = raw.lower()
    if any(n in lowered for n in REFUSAL_NEEDLES):
        return None
    if len(raw) < 80:
        return None
    return raw


def _fetch_cover(track: Dict[str, Any]) -> Optional[str]:
    raw = track.get("artworkUrl100")
    if not raw:
        return None
    upscaled = raw.replace("100x100bb", "1200x1200bb").replace("100x100", "1200x1200")
    data = _http_bytes(upscaled)
    if not data:
        return None
    tmp = Path(tempfile.gettempdir()) / f"mindown_cover_{uuid.uuid4().hex}.jpg"
    try:
        tmp.write_bytes(data)
        return str(tmp)
    except OSError:
        return None


def _write_tags(
    file_path: str,
    track: Dict[str, Any],
    cover_path: Optional[str],
    copyright_text: Optional[str],
    lyrics: Optional[str],
    ffmpeg_path: str,
) -> Tuple[bool, str]:
    src = Path(file_path)
    ext = src.suffix.lstrip(".").lower()
    tmp = src.with_suffix(f".mindown_tag.{ext}")

    args: List[str] = [ffmpeg_path, "-y", "-loglevel", "error", "-i", str(src)]
    if cover_path:
        args += ["-i", cover_path]

    args += ["-map", "0"]
    if cover_path:
        args += ["-map", "1"]

    args += ["-c", "copy"]

    if ext == "mp3":
        args += ["-id3v2_version", "3", "-write_id3v1", "1"]

    def add_meta(key: str, value: Optional[str]) -> None:
        if value:
            args.extend(["-metadata", f"{key}={value}"])

    title = track.get("trackName")
    artist = track.get("artistName")
    album = track.get("collectionName")

    add_meta("title", title)
    if artist:
        add_meta("artist", artist)
        add_meta("album_artist", artist)
        add_meta("composer", artist)
    add_meta("album", album)

    release_date = track.get("releaseDate") or ""
    if len(release_date) >= 4:
        year = release_date[:4]
        add_meta("date", year)
        add_meta("year", year)

    add_meta("genre", track.get("primaryGenreName"))

    n = track.get("trackNumber")
    if n:
        total = track.get("trackCount")
        add_meta("track", f"{n}/{total}" if total else str(n))

    d = track.get("discNumber")
    if d:
        total = track.get("discCount")
        add_meta("disc", f"{d}/{total}" if total else str(d))

    add_meta("copyright", copyright_text)
    if lyrics:
        add_meta("lyrics-eng", lyrics)
        add_meta("lyrics", lyrics)

    if cover_path:
        args += ["-disposition:v", "attached_pic"]
        args += ["-metadata:s:v", "title=Album cover"]
        args += ["-metadata:s:v", "comment=Cover (front)"]

    args.append(str(tmp))

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        return False, f"ffmpeg failed: {e}"

    if proc.returncode != 0:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        msg = (proc.stderr or "").strip().splitlines()
        first = msg[0] if msg else f"ffmpeg exited {proc.returncode}"
        return False, first

    try:
        src.unlink()
        tmp.rename(src)
    except OSError as e:
        return False, f"replace failed: {e}"
    return True, "ok"


def enrich(cfg: Config, file_path: str, search_title: str) -> EnrichResult:
    if not cfg.itunes_lookup_enabled:
        return EnrichResult(False, "iTunes lookup disabled")
    if not file_path or not Path(file_path).exists():
        return EnrichResult(False, "file missing")
    if not cfg.ffmpeg_path or not os.access(cfg.ffmpeg_path, os.X_OK):
        return EnrichResult(False, "ffmpeg not configured")

    query = cleaned_query(search_title)
    if not query:
        return EnrichResult(False, "empty title")

    track = _itunes_search(query)
    if not track:
        return EnrichResult(False, "no iTunes match")

    duration_sec: Optional[int] = None
    if track.get("trackTimeMillis"):
        try:
            duration_sec = int(int(track["trackTimeMillis"]) / 1000)
        except (TypeError, ValueError):
            duration_sec = None

    artist = track.get("artistName") or ""
    title = track.get("trackName") or ""
    album = track.get("collectionName")
    collection_id = track.get("collectionId")

    copyright_text = _itunes_album_copyright(collection_id) if collection_id else None

    lyrics_source = "none"
    lyrics: Optional[str] = None
    if artist and title:
        lyrics = _lrclib_lyrics(artist, title, album, duration_sec)
        if lyrics:
            lyrics_source = "lrclib"
        else:
            ai = _ai_lyrics_fallback(cfg, artist, title, album)
            if ai:
                lyrics = ai
                lyrics_source = "ai"

    cover_path = _fetch_cover(track)
    try:
        ok, msg = _write_tags(
            file_path=file_path,
            track=track,
            cover_path=cover_path,
            copyright_text=copyright_text,
            lyrics=lyrics,
            ffmpeg_path=cfg.ffmpeg_path,
        )
    finally:
        if cover_path:
            try:
                os.remove(cover_path)
            except OSError:
                pass

    if not ok:
        return EnrichResult(False, f"ffmpeg tag write failed: {msg}")

    bits: List[str] = []
    if artist and title:
        bits.append(f"{artist} — {title}")
    if lyrics_source == "lrclib":
        bits.append("+lyrics")
    elif lyrics_source == "ai":
        bits.append("+lyrics(AI)")
    if copyright_text:
        bits.append("+©")
    summary = " ".join(bits) or "tagged"
    return EnrichResult(True, f"tagged · {summary}")

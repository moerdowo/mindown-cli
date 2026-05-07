"""Tool definitions for the chat model + the search_youtube implementation.

Mirrors Sources/Mindown/AITools.swift.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Tuple

DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_youtube",
            "description": (
                "Search YouTube via yt-dlp. Returns up to `limit` candidate "
                "videos with title, channel, duration in seconds, and the "
                "canonical webpage URL. Use this to find URLs for downloads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Search query — typically '<artist> <song title> "
                            "official audio' or similar."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Number of results, default 5.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_downloads",
            "description": (
                "Surface a list of proposed downloads to the user for "
                "approval. The user reviews each item, ticks/unticks them, "
                "and confirms. Approved items are queued automatically. "
                "Returns counts and per-item approval status. Always call "
                "this BEFORE assuming a download has happened."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "url":   {"type": "string", "description": "Canonical YouTube URL."},
                                "title": {"type": "string", "description": "Human-readable title (artist - song)."},
                                "format": {
                                    "type": "string",
                                    "enum": ["mp3", "m4a", "opus", "wav", "mp4", "webm", "mkv"],
                                },
                                "quality": {
                                    "type": "string",
                                    "description": (
                                        "For video: best, 2160p, 1440p, 1080p, "
                                        "720p, 480p, 360p. For audio: best, "
                                        "320, 256, 192, 128 (kbps)."
                                    ),
                                },
                                "note": {
                                    "type": "string",
                                    "description": "Short justification, optional.",
                                },
                            },
                            "required": ["url", "title", "format", "quality"],
                        },
                    },
                },
                "required": ["items"],
            },
        },
    },
]


def search_youtube(query: str, limit: int, yt_dlp_path: str) -> str:
    """Run `yt-dlp ytsearch:` and return a JSON string for the model."""
    if not yt_dlp_path or not os.path.isfile(yt_dlp_path) or not os.access(yt_dlp_path, os.X_OK):
        return json.dumps({"error": "yt-dlp not configured — set its path with /config"})

    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "empty query"})

    bounded = max(1, min(int(limit or 5), 10))

    args = [
        yt_dlp_path,
        f"ytsearch{bounded}:{q}",
        "--print", "%(id)s|||%(title)s|||%(uploader)s|||%(duration)s|||%(webpage_url)s",
        "--skip-download",
        "--no-warnings",
        "--quiet",
        "--ignore-errors",
        "--flat-playlist",
    ]

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return json.dumps({"error": f"could not run yt-dlp: {e}"})

    results: List[Dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("|||")
        if len(parts) < 5:
            continue
        try:
            duration_sec = int(float(parts[3]))
        except ValueError:
            duration_sec = 0
        results.append({
            "id": parts[0],
            "title": parts[1],
            "channel": parts[2],
            "duration_seconds": duration_sec,
            "url": parts[4],
        })

    return json.dumps({"query": q, "results": results}, ensure_ascii=False)

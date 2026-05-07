"""Format / quality enums and parsing — mirrors Sources/Mindown/DownloadModels.swift."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

AUDIO_FORMATS = {"mp3", "m4a", "opus", "wav"}
VIDEO_FORMATS = {"mp4", "webm", "mkv"}
ALL_FORMATS = AUDIO_FORMATS | VIDEO_FORMATS


def is_audio(fmt: str) -> bool:
    return fmt.lower() in AUDIO_FORMATS


def parse_format(s: str) -> str:
    f = (s or "").lower().strip()
    if f in ALL_FORMATS:
        return f
    return "mp3"


@dataclass
class Quality:
    label: str
    height_limit: Optional[int] = None  # video
    audio_kbps: Optional[int] = None    # audio


QUALITY_BEST = Quality("best")


def parse_quality(s: str, audio: bool) -> Quality:
    lower = (
        (s or "")
        .lower()
        .replace("kbps", "")
        .replace("k", "")
        .replace("p", "")
        .strip()
    )
    if lower in ("best", ""):
        return QUALITY_BEST
    if audio:
        if lower in ("320", "256", "192", "128"):
            return Quality(f"{lower} kbps", audio_kbps=int(lower))
        return QUALITY_BEST
    if lower == "4k":
        lower = "2160"
    if lower in ("2160", "1440", "1080", "720", "480", "360"):
        return Quality(f"{lower}p", height_limit=int(lower))
    return QUALITY_BEST

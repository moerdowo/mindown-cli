"""Persistent user config: API key, model, binary paths, download dir.

Stored as JSON under ~/.config/mindown-cli/config.json (or
$XDG_CONFIG_HOME/mindown-cli/config.json if set). The file is created with
0600 permissions because it holds an API key.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import ui


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "mindown-cli"


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    yt_dlp_path: str = ""
    ffmpeg_path: str = ""
    download_dir: str = ""
    itunes_lookup_enabled: bool = True
    ai_lyrics_fallback: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def save(self) -> Path:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            return cls()
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls()
        cfg = cls()
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg


def _which(name: str) -> str:
    found = shutil.which(name)
    return found or ""


def _default_download_dir() -> str:
    home = Path.home()
    candidate = home / "Downloads" / "Mindown"
    return str(candidate)


def first_run_setup() -> Config:
    """Interactively gather config from the user."""
    print(ui.bold("Welcome to Mindown CLI."))
    print(ui.dim(
        "Chat-driven YouTube → mp3/mp4 downloader with iTunes + LRCLib tagging."
    ))
    print()

    cfg = Config.load()

    api_key = ui.prompt(
        "OpenAI-compatible API key",
        default=(cfg.api_key or None),
        secret=True,
    )
    while not api_key:
        ui.warn("API key is required.")
        api_key = ui.prompt("OpenAI-compatible API key", secret=True)

    base_url = ui.prompt("Base URL", default=cfg.base_url or "https://api.openai.com/v1")
    model = ui.prompt("Model", default=cfg.model or "gpt-4o-mini")

    yt = ui.prompt(
        "yt-dlp path",
        default=cfg.yt_dlp_path or _which("yt-dlp") or "/opt/homebrew/bin/yt-dlp",
    )
    ff = ui.prompt(
        "ffmpeg path",
        default=cfg.ffmpeg_path or _which("ffmpeg") or "/opt/homebrew/bin/ffmpeg",
    )
    download_dir = ui.prompt(
        "Download directory",
        default=cfg.download_dir or _default_download_dir(),
    )

    cfg.api_key = api_key
    cfg.base_url = base_url.rstrip("/")
    cfg.model = model
    cfg.yt_dlp_path = yt
    cfg.ffmpeg_path = ff
    cfg.download_dir = str(Path(download_dir).expanduser())
    Path(cfg.download_dir).mkdir(parents=True, exist_ok=True)

    path = cfg.save()
    ui.success(f"Saved config to {path}")
    return cfg


def ensure_configured(reconfigure: bool = False) -> Config:
    cfg = Config.load()
    if reconfigure or not cfg.is_configured:
        cfg = first_run_setup()
    if not cfg.download_dir:
        cfg.download_dir = _default_download_dir()
        Path(cfg.download_dir).mkdir(parents=True, exist_ok=True)
        cfg.save()
    if not cfg.yt_dlp_path:
        cfg.yt_dlp_path = _which("yt-dlp")
    if not cfg.ffmpeg_path:
        cfg.ffmpeg_path = _which("ffmpeg")
    return cfg

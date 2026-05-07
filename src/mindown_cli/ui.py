"""Tiny ANSI-color helpers and prompt utilities."""
from __future__ import annotations

import os
import shutil
import sys
from getpass import getpass
from typing import Optional

_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _wrap(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(s: str) -> str:    return _wrap("1", s)
def dim(s: str) -> str:     return _wrap("2", s)
def red(s: str) -> str:     return _wrap("31", s)
def green(s: str) -> str:   return _wrap("32", s)
def yellow(s: str) -> str:  return _wrap("33", s)
def blue(s: str) -> str:    return _wrap("34", s)
def cyan(s: str) -> str:    return _wrap("36", s)


def info(msg: str) -> None:
    print(f"{dim('▸')} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{yellow('!')} {msg}", flush=True)


def error(msg: str) -> None:
    print(f"{red('✗')} {msg}", file=sys.stderr, flush=True)


def success(msg: str) -> None:
    print(f"{green('✓')} {msg}", flush=True)


def prompt(message: str, default: Optional[str] = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    if secret:
        raw = getpass(f"{message}{suffix}: ")
    else:
        raw = input(f"{message}{suffix}: ")
    raw = raw.strip()
    if not raw and default is not None:
        return default
    return raw


def term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 20)).columns
    except OSError:
        return default


def progress_bar(frac: float, width: int = 20, fill: str = "█", empty: str = "░") -> str:
    """Render a unicode-block progress bar at the given fraction (0..1)."""
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return fill * filled + empty * (width - filled)


# Block-letter "MINDOWN" + tagline. Width ~62 cols, fits any normal terminal.
_BANNER = r"""
███╗   ███╗██╗███╗   ██╗██████╗  ██████╗ ██╗    ██╗███╗   ██╗
████╗ ████║██║████╗  ██║██╔══██╗██╔═══██╗██║    ██║████╗  ██║
██╔████╔██║██║██╔██╗ ██║██║  ██║██║   ██║██║ █╗ ██║██╔██╗ ██║
██║╚██╔╝██║██║██║╚██╗██║██║  ██║██║   ██║██║███╗██║██║╚██╗██║
██║ ╚═╝ ██║██║██║ ╚████║██████╔╝╚██████╔╝╚███╔███╔╝██║ ╚████║
╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═════╝  ╚═════╝  ╚══╝╚══╝ ╚═╝  ╚═══╝
"""


def banner(subtitle: str = "") -> str:
    """Return the ASCII banner, colorized when stdout is a TTY."""
    art = _BANNER.rstrip("\n")
    art = cyan(art) if _USE_COLOR else art
    tag = "  cli · ai-driven yt-dlp + ffmpeg downloader"
    if subtitle:
        tag = f"  {subtitle}"
    tag = dim(tag) if _USE_COLOR else tag
    return f"{art}\n{tag}\n"


def print_banner(subtitle: str = "") -> None:
    print(banner(subtitle))

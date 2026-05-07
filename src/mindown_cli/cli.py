"""Mindown CLI entry point.

Mirrors the Mindown SwiftUI app's AI assistant flow as a terminal REPL:
  - first-run setup collects API key + paths and persists them
  - user types a song / artist / video request
  - the model calls search_youtube → propose_downloads → user approves
  - approved items are downloaded with yt-dlp, audio is tagged via ffmpeg
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ui
from .chat import ChatSession
from .config import Config, config_path, ensure_configured, first_run_setup


HELP_TEXT = """\
Slash commands:
  /help            show this help
  /config          re-run setup (API key, base URL, model, paths)
  /show            show current config (API key redacted)
  /reset           clear chat history
  /dir <path>      set the download directory
  /model <name>    switch model
  /tag on|off      toggle iTunes tag enrichment
  /ailyrics on|off toggle the AI lyrics fallback (off by default)
  /quit, /exit     exit

Anything else is sent to the AI assistant. Try things like:
  anti-hero by taylor swift as mp3
  top 5 radiohead songs
  bohemian rhapsody music video as mp4 1080p
"""


def _show_config(cfg: Config) -> None:
    redacted = cfg.api_key[:4] + "…" if cfg.api_key else "(unset)"
    print(ui.bold("Mindown CLI config:"))
    print(f"  config path     : {config_path()}")
    print(f"  api_key         : {redacted}")
    print(f"  base_url        : {cfg.base_url}")
    print(f"  model           : {cfg.model}")
    print(f"  yt_dlp_path     : {cfg.yt_dlp_path}")
    print(f"  ffmpeg_path     : {cfg.ffmpeg_path}")
    print(f"  download_dir    : {cfg.download_dir}")
    print(f"  itunes tag      : {'on' if cfg.itunes_lookup_enabled else 'off'}")
    print(f"  ai lyrics fallback: {'on' if cfg.ai_lyrics_fallback else 'off'}")


def _handle_command(cfg: Config, session: ChatSession, line: str) -> bool:
    """Return True if we should keep looping, False to exit."""
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit", "/q"):
        return False
    if cmd in ("/help", "/?"):
        print(HELP_TEXT)
        return True
    if cmd == "/show":
        _show_config(cfg)
        return True
    if cmd == "/config":
        new = first_run_setup()
        cfg.__dict__.update(new.__dict__)
        session.cfg = cfg
        return True
    if cmd == "/reset":
        session.reset()
        ui.info("chat history cleared")
        return True
    if cmd == "/dir":
        if not arg:
            ui.warn("usage: /dir <path>")
            return True
        path = Path(arg).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        cfg.download_dir = str(path)
        cfg.save()
        ui.success(f"download_dir = {cfg.download_dir}")
        return True
    if cmd == "/model":
        if not arg:
            ui.warn("usage: /model <name>")
            return True
        cfg.model = arg.strip()
        cfg.save()
        ui.success(f"model = {cfg.model}")
        return True
    if cmd == "/tag":
        if arg.lower() in ("on", "true", "1"):
            cfg.itunes_lookup_enabled = True
        elif arg.lower() in ("off", "false", "0"):
            cfg.itunes_lookup_enabled = False
        else:
            ui.warn("usage: /tag on|off")
            return True
        cfg.save()
        ui.success(f"itunes tag = {'on' if cfg.itunes_lookup_enabled else 'off'}")
        return True
    if cmd == "/ailyrics":
        if arg.lower() in ("on", "true", "1"):
            cfg.ai_lyrics_fallback = True
        elif arg.lower() in ("off", "false", "0"):
            cfg.ai_lyrics_fallback = False
        else:
            ui.warn("usage: /ailyrics on|off")
            return True
        cfg.save()
        ui.success(f"ai lyrics fallback = {'on' if cfg.ai_lyrics_fallback else 'off'}")
        return True

    ui.warn(f"unknown command: {cmd} — type /help")
    return True


def _repl(cfg: Config) -> None:
    session = ChatSession(cfg)
    print()
    print(ui.bold("Mindown CLI") +
          ui.dim(f"  · model: {cfg.model}  · dir: {cfg.download_dir}"))
    print(ui.dim("Type a song/video request, or /help for commands. /quit to exit."))
    print()
    while True:
        try:
            line = input(f"{ui.cyan('›')} ").strip()
        except EOFError:
            print()
            return
        except KeyboardInterrupt:
            print()
            ui.info("(ctrl-c — type /quit to exit)")
            continue
        if not line:
            continue
        if line.startswith("/"):
            if not _handle_command(cfg, session, line):
                return
            continue
        try:
            session.send(line)
        except KeyboardInterrupt:
            print()
            ui.warn("interrupted")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mindown",
        description="AI-powered CLI for downloading songs/videos via yt-dlp.",
    )
    parser.add_argument(
        "--config", action="store_true",
        help="run the first-time setup again (overwrite stored config)",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="print stored config and exit",
    )
    args = parser.parse_args(argv)

    if args.show:
        cfg = Config.load()
        _show_config(cfg)
        return 0

    cfg = ensure_configured(reconfigure=args.config)
    if not cfg.is_configured:
        ui.error("API key is required. Run `mindown --config` to set up.")
        return 1

    try:
        _repl(cfg)
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

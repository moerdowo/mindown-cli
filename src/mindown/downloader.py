"""yt-dlp orchestration with live terminal progress.

Mirrors the argument construction in Sources/Mindown/DownloadManager.swift —
same output template, same `--progress-template` format, same audio/video
format-string logic — but renders progress in the terminal instead of a UI.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import ui
from .config import Config
from .media import Quality, is_audio


@dataclass
class DownloadResult:
    ok: bool
    output_path: str = ""
    title: str = ""
    error: str = ""


def _build_args(
    cfg: Config,
    url: str,
    fmt: str,
    quality: Quality,
) -> List[str]:
    out_template = str(
        Path(cfg.download_dir) / "%(title).200B [%(id)s].%(ext)s"
    )

    args: List[str] = [
        "-o", out_template,
        "--no-playlist", "--newline", "--no-colors", "--no-mtime",
    ]

    if cfg.ffmpeg_path:
        args += ["--ffmpeg-location", cfg.ffmpeg_path]

    if is_audio(fmt):
        args += ["-x", "--audio-format", fmt]
        if quality.audio_kbps:
            args += ["--audio-quality", f"{quality.audio_kbps}K"]
        else:
            args += ["--audio-quality", "0"]
    else:
        height_filter = f"[height<={quality.height_limit}]" if quality.height_limit else ""
        format_string = (
            f"bv*[ext={fmt}]{height_filter}+ba[ext=m4a]/"
            f"bv*{height_filter}+ba/"
            f"b{height_filter}/b"
        )
        args += ["-f", format_string, "--merge-output-format", fmt]

    args += [
        "--progress-template",
        "DL|%(progress._percent_str)s|%(progress._speed_str)s|"
        "%(progress._eta_str)s|%(progress._total_bytes_str)s|%(info.title)s",
        "--print", "after_move:FILE|%(filepath)s",
        "--print", "before_dl:TITLE|%(title)s",
        url,
    ]
    return args


class _ProgressBar:
    """Two-line progress display that redraws on a TTY:

      queue ▓▓▓░░░░░░░░░░░░░░░░░  2/4
      item  ████░░░░░░░░░░░░░░░░ 25.0%  1.2 MiB/s  ETA 00:08  4.5MiB

    On non-TTY output we stay silent — yt-dlp's own newline output is
    already piping through `run_download`.
    """

    def __init__(self, label: str, queue_index: int = 1, queue_total: int = 1):
        self.label = label
        self.qi = max(1, queue_index)
        self.qt = max(1, queue_total)
        self.tty = sys.stdout.isatty()
        self.lines_drawn = 0
        self.last_item_pct = 0.0

    def _render(self, item_pct: float, speed: str, eta: str, total: str) -> tuple[str, str]:
        bar_width = 20
        item_bar = ui.progress_bar(item_pct, width=bar_width)
        # Queue fraction = fully completed items + fractional progress on current.
        completed = self.qi - 1
        queue_frac = (completed + item_pct) / self.qt
        queue_bar = ui.progress_bar(queue_frac, width=bar_width)

        queue_line = (
            f"  {ui.dim('queue')} {ui.cyan(queue_bar)} "
            f"{ui.bold(f'{self.qi}/{self.qt}')} "
            f"{ui.dim(f'· {queue_frac * 100:4.1f}%')}"
        )
        item_line = (
            f"  {ui.dim('item ')} {ui.green(item_bar)} "
            f"{item_pct * 100:5.1f}%  "
            f"{ui.dim(speed or '—')}  "
            f"ETA {ui.dim(eta or '—')}  "
            f"{ui.dim(total or '')}"
        )

        # Hard-truncate so we don't wrap and break the overwrite trick.
        max_w = ui.term_width(120)
        if len(queue_line) > max_w:
            queue_line = queue_line[:max_w]
        if len(item_line) > max_w:
            item_line = item_line[:max_w]
        return queue_line, item_line

    def update(self, pct: float, speed: str, eta: str, total: str) -> None:
        if not self.tty:
            return
        self.last_item_pct = pct
        queue_line, item_line = self._render(pct, speed, eta, total)
        if self.lines_drawn:
            # Cursor sits at end of the last drawn line; move up to the top of the block.
            sys.stdout.write(f"\033[{self.lines_drawn - 1}A\r")
        sys.stdout.write(f"\033[2K{queue_line}\n\033[2K{item_line}")
        sys.stdout.flush()
        self.lines_drawn = 2

    def finish(self) -> None:
        if self.tty:
            sys.stdout.write("\n")
            sys.stdout.flush()


def _parse_percent(s: str) -> Optional[float]:
    s = s.replace("%", "").strip()
    if not s or s == "NA":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return max(0.0, min(1.0, v / 100.0))


def run_download(
    cfg: Config,
    url: str,
    fmt: str,
    quality: Quality,
    label: str,
    queue_index: int = 1,
    queue_total: int = 1,
) -> DownloadResult:
    if not cfg.yt_dlp_path or not os.access(cfg.yt_dlp_path, os.X_OK):
        return DownloadResult(ok=False, error="yt-dlp not found — run /config")

    Path(cfg.download_dir).mkdir(parents=True, exist_ok=True)

    args = [cfg.yt_dlp_path] + _build_args(cfg, url, fmt, quality)

    env = os.environ.copy()
    env["PATH"] = ":".join(["/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])

    print(f"  {ui.dim('cmd:')} {ui.dim(shlex.join(args[:1]) + ' …')}")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

    bar = _ProgressBar(label=label, queue_index=queue_index, queue_total=queue_total)
    title = ""
    output_path = ""
    last_line = ""

    def drain_stderr() -> None:
        nonlocal last_line
        assert proc.stderr is not None
        for line in proc.stderr:
            last_line = line.rstrip()

    t = threading.Thread(target=drain_stderr, daemon=True)
    t.start()

    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            if line.startswith("TITLE|"):
                title = line[len("TITLE|"):].strip()
                if title:
                    print(f"  {ui.dim('title:')} {title}")
                continue
            if line.startswith("FILE|"):
                output_path = line[len("FILE|"):].strip()
                continue
            if line.startswith("DL|"):
                parts = line.split("|")
                if len(parts) >= 5:
                    pct = _parse_percent(parts[1])
                    if pct is not None:
                        bar.update(
                            pct,
                            speed=parts[2].strip(),
                            eta=parts[3].strip(),
                            total=parts[4].strip(),
                        )
                    if len(parts) >= 6 and not title:
                        t6 = parts[5].strip()
                        if t6 and t6 != "NA":
                            title = t6
                continue
            # Other yt-dlp chatter — ignore on TTY; show on non-TTY
            if not sys.stdout.isatty():
                print(line)
    except KeyboardInterrupt:
        proc.terminate()
        bar.finish()
        return DownloadResult(ok=False, error="canceled")

    proc.wait()
    bar.finish()
    t.join(timeout=1.0)

    if proc.returncode == 0:
        return DownloadResult(ok=True, output_path=output_path, title=title)
    msg = last_line or f"yt-dlp exited with code {proc.returncode}"
    return DownloadResult(ok=False, error=msg, title=title, output_path=output_path)

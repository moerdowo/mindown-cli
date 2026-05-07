"""Chat REPL with tool-calling orchestration.

Mirrors Sources/Mindown/ChatViewModel.swift — keeps a persistent OpenAI-shape
transcript across user turns so {assistant tool_calls → tool results} pairing
stays valid, runs up to 8 tool-call iterations per user message, and routes
the two tools (search_youtube, propose_downloads) through host-side handlers.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List

from . import metadata, tools, ui
from .ai_client import AIClient, AIError, ToolCall
from .config import Config
from .downloader import run_download
from .media import is_audio, parse_format, parse_quality

SYSTEM_PROMPT = """\
You are Mindown's media assistant. You help the user find and download \
songs and videos via the bundled yt-dlp.

Workflow:
1. When the user asks for a song, artist, or video, call search_youtube \
   with a clear query. Prefer official channels, official audio, lyric \
   videos, or topic auto-uploads over fan covers and reaction videos. \
   Skip live versions unless the user asks for them.
2. If the user asks for several songs (e.g. "top 5 from artist X"), use \
   your knowledge of the artist's catalogue to pick titles, then call \
   search_youtube once per title.
3. After collecting candidates, call propose_downloads with the chosen \
   items. The user must approve before anything is downloaded.
4. Default to MP3 audio at "best" quality. Use video (MP4 1080p) only \
   if the user explicitly asks for video / a music video.
5. propose_downloads returns AFTER the approved items have already \
   been downloaded — its result includes `downloaded_count`, \
   `failed_count`, and `rejected_count`. Reply with a one-line \
   summary like "downloaded 3 of 5" (mention failures if any).

Allowed formats: mp3, m4a, opus, wav (audio); mp4, webm, mkv (video).
Allowed qualities: best, 2160p, 1440p, 1080p, 720p, 480p, 360p \
(video); 320, 256, 192, 128 (audio kbps).
"""


@dataclass
class ProposedItem:
    title: str
    url: str
    fmt: str
    quality: str
    note: str = ""


def _parse_proposed_items(args_json: str) -> List[ProposedItem]:
    try:
        args = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        return []
    out: List[ProposedItem] = []
    for raw in args.get("items", []):
        try:
            out.append(ProposedItem(
                title=str(raw.get("title", "")),
                url=str(raw.get("url", "")),
                fmt=str(raw.get("format", "")),
                quality=str(raw.get("quality", "")),
                note=str(raw.get("note") or ""),
            ))
        except Exception:
            continue
    return out


def _print_queue_status(done: int, total: int) -> None:
    """One-line queue progress, printed after each item completes."""
    if total <= 0:
        return
    frac = done / total
    bar = ui.progress_bar(frac, width=24)
    pct_str = f"{frac * 100:5.1f}%"
    print(f"  {ui.dim('queue:')} {ui.cyan(bar)} "
          f"{ui.bold(f'{done}/{total}')} {ui.dim(f'· {pct_str}')}")


def _print_proposal(items: List[ProposedItem]) -> None:
    print()
    print(ui.bold("Proposed downloads:"))
    for i, it in enumerate(items, start=1):
        print(f"  [{i}] {ui.bold(it.title)}")
        print(ui.dim(f"      {it.fmt} / {it.quality}"))
        if it.note:
            print(ui.dim(f"      {it.note}"))
        print(ui.dim(f"      {it.url}"))
    print()


def _ask_decisions(
    items: List[ProposedItem],
    auto_approve: bool = False,
    first_only: bool = False,
) -> List[bool]:
    """Show the proposal and collect a per-item approve/reject vector.

    When `auto_approve` is True we skip the prompt entirely and accept every
    item — used by `--yes` / `--prompt` non-interactive runs. When
    `first_only` is True (e.g. `--first`) we approve item 1 and reject the
    rest, also without prompting.
    """
    _print_proposal(items)

    if first_only:
        decisions = [i == 0 for i in range(len(items))]
        skipped = max(0, len(items) - 1)
        ui.info(
            f"first-only: keeping item 1, skipping {skipped} other(s)"
            if skipped else "first-only: 1 item proposed, keeping it"
        )
        return decisions

    if auto_approve:
        ui.info(f"auto-approving {len(items)} item(s)")
        return [True] * len(items)

    while True:
        choice = ui.prompt(
            "Approve? [Y]es / [n]o / [s]elect / numbers like '1,3'",
            default="y",
        ).lower()
        if choice in ("y", "yes", ""):
            return [True] * len(items)
        if choice in ("n", "no"):
            return [False] * len(items)
        if choice in ("s", "select"):
            decisions: List[bool] = []
            for i, it in enumerate(items, start=1):
                ans = ui.prompt(f"  [{i}] {it.title} — keep?", default="y").lower()
                decisions.append(ans in ("y", "yes", ""))
            return decisions
        # parse numeric list "1,3"
        try:
            picks = {int(x) for x in choice.replace(" ", "").split(",") if x}
            if picks and all(1 <= p <= len(items) for p in picks):
                return [(i + 1) in picks for i in range(len(items))]
        except ValueError:
            pass
        ui.warn("didn't understand — type y, n, s, or item numbers like '1,3'")


def _run_proposal(
    cfg: Config,
    items: List[ProposedItem],
    auto_approve: bool = False,
    first_only: bool = False,
) -> str:
    """Show approval UI, run approved downloads sequentially, return JSON for the model."""
    if not items:
        return json.dumps({"error": "propose_downloads called with empty items"})

    decisions = _ask_decisions(items, auto_approve=auto_approve, first_only=first_only)
    approved = [i for i, d in zip(items, decisions) if d]

    # Track per-item outcome so the model can summarize accurately.
    outcomes: Dict[str, Dict[str, Any]] = {
        item.url: {"title": item.title, "approved": False, "downloaded": False, "error": ""}
        for item in items
    }
    for item, d in zip(items, decisions):
        outcomes[item.url]["approved"] = bool(d)

    if approved:
        total = len(approved)
        print()
        print(ui.bold(f"Queue · {total} item(s)"))
        _print_queue_status(0, total)
        print()
        for idx, item in enumerate(approved, start=1):
            fmt = parse_format(item.fmt)
            qual = parse_quality(item.quality, audio=is_audio(fmt))
            label = item.title or item.url
            print(f"{ui.bold(f'[{idx}/{total}]')} {label}  "
                  f"{ui.dim(f'({fmt}, {qual.label})')}")
            result = run_download(
                cfg, item.url, fmt, qual,
                label=label,
                queue_index=idx,
                queue_total=total,
            )
            if not result.ok:
                ui.error(result.error or "download failed")
                outcomes[item.url]["error"] = result.error or "download failed"
                _print_queue_status(idx, total)
                print()
                continue
            outcomes[item.url]["downloaded"] = True
            ui.success(f"saved: {result.output_path}")
            if is_audio(fmt):
                title_for_lookup = result.title or item.title
                print(f"  {ui.dim('tagging…')}")
                er = metadata.enrich(cfg, result.output_path, title_for_lookup)
                if er.applied:
                    ui.success(er.message)
                else:
                    ui.warn(f"tag skipped — {er.message}")
            _print_queue_status(idx, total)
            print()

    downloaded = sum(1 for o in outcomes.values() if o["downloaded"])
    failed = sum(1 for o in outcomes.values() if o["approved"] and not o["downloaded"])
    rejected = sum(1 for o in outcomes.values() if not o["approved"])

    report = {
        "downloaded_count": downloaded,
        "failed_count": failed,
        "rejected_count": rejected,
        "items": [
            {
                "title": o["title"],
                "approved": o["approved"],
                "downloaded": o["downloaded"],
                **({"error": o["error"]} if o["error"] else {}),
            }
            for o in outcomes.values()
        ],
    }
    return json.dumps(report, ensure_ascii=False)


def _execute_tool(
    cfg: Config,
    call: ToolCall,
    auto_approve: bool = False,
    first_only: bool = False,
) -> str:
    if call.name == "search_youtube":
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        query = str(args.get("query", ""))
        limit = int(args.get("limit", 5) or 5)
        ui.info(f"search_youtube · {query}")
        return tools.search_youtube(query, limit, cfg.yt_dlp_path)

    if call.name == "propose_downloads":
        items = _parse_proposed_items(call.arguments)
        return _run_proposal(cfg, items, auto_approve=auto_approve, first_only=first_only)

    return json.dumps({"error": f"unknown tool: {call.name}"})


class ChatSession:
    def __init__(self, cfg: Config, auto_approve: bool = False, first_only: bool = False):
        self.cfg = cfg
        self.auto_approve = auto_approve
        self.first_only = first_only
        self.transcript: List[Dict[str, Any]] = []

    def reset(self) -> None:
        self.transcript.clear()

    def send(self, user_text: str) -> None:
        self.transcript.append({"role": "user", "content": user_text})
        client = AIClient(self.cfg.api_key, self.cfg.base_url, self.cfg.model)

        for _ in range(8):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.transcript]
            try:
                choice = client.chat(messages, tools=tools.DEFINITIONS)
            except AIError as e:
                ui.error(str(e))
                return

            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": choice.content,
            }
            if choice.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in choice.tool_calls
                ]
            self.transcript.append(assistant_msg)

            if choice.content:
                print(f"{ui.cyan('mindown')} {choice.content}")

            if not choice.tool_calls:
                return

            for tc in choice.tool_calls:
                result = _execute_tool(
                    self.cfg,
                    tc,
                    auto_approve=self.auto_approve,
                    first_only=self.first_only,
                )
                self.transcript.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.name,
                    "content": result,
                })

        ui.warn("hit tool-call iteration cap (8)")

---
name: mindown-cli
description: Download songs/videos from YouTube via an AI-driven CLI that wraps yt-dlp + ffmpeg, with automatic ID3 / iTunes-atom tagging (cover art, lyrics) on audio files. Trigger on requests like "download song X", "get the music video for Y as mp4", "save Z as mp3", "grab the album version of …", "find and download …". Targets the `mindown` CLI installed from this repo.
---

# mindown-cli skill

A chat-driven CLI for downloading songs and videos. The user describes
what they want in natural language; the CLI asks an OpenAI-compatible
model to pick YouTube URLs, runs `yt-dlp` to download, and
post-processes audio with iTunes Search + LRCLib metadata via `ffmpeg`.

The CLI is designed to be driven by an AI agent in non-interactive
mode — every action below assumes you (the agent) are calling
`mindown` from a shell.

## When to use

Pick this skill when the user wants any of:
- A specific song downloaded as audio (any of mp3 / m4a / opus / wav)
- A specific music video or YouTube video downloaded as video
  (mp4 / webm / mkv)
- Multiple songs from an artist ("top 5 X songs", "every track from album Y")
- A clean copy with proper ID3 tags / cover art / lyrics already embedded

Do NOT pick this skill for:
- Streaming-only workflows (Spotify links, Apple Music, etc.)
- Downloading paid / DRM content
- Anything that isn't on a yt-dlp-supported site

## Prerequisites — check these BEFORE running

1. **The `mindown` binary is installed.**
   ```bash
   which mindown || python3 -m mindown_cli.cli --help
   ```
   If neither resolves, the user needs to `pip install -e .` from the
   repo root. Tell them; do not attempt to install without permission.

2. **`yt-dlp` and `ffmpeg` are on PATH.**
   ```bash
   which yt-dlp ffmpeg
   ```
   On macOS: `brew install yt-dlp ffmpeg`. On Linux: distro package
   manager. Surface the exact missing tool to the user — do not install
   silently.

3. **The CLI is configured (API key + paths set).**
   ```bash
   mindown --show
   ```
   - If the output shows `api_key: (unset)` or the config file is
     missing, the user must run `mindown --config` interactively
     themselves (it prompts for an API key via `getpass`, which an
     agent should not type into).
   - Do NOT try to write the config file directly; it lives at
     `~/.config/mindown-cli/config.json` (mode 0600) and the API key
     belongs to the user.

If any prereq fails, stop and tell the user what's missing.

## Primary command pattern (agents always use this)

```bash
mindown --prompt "<natural-language request>" [flags]
```

`--prompt` (or `-p`) runs one turn:
- Sends the prompt to the model.
- Auto-approves every download the model proposes (no y/n prompt).
- Runs the downloads sequentially with a progress bar.
- Tags audio files via iTunes Search + LRCLib + ffmpeg.
- Exits with code 0 on success, 130 on Ctrl-C.

There is no banner in `--prompt` mode, so output pipes cleanly.

## Flags worth knowing

| Flag | What it does | When to use it |
|---|---|---|
| `-p TEXT`, `--prompt TEXT` | One-shot non-interactive run — REQUIRED for agent use | Always |
| `--first` | Keep only the first proposed item from each turn | When the user wants exactly one song / video, even if their phrasing is ambiguous (e.g. they only give a song title with no artist) |
| `--ai-lyrics` | Use the chat model as a lyrics fallback when LRCLib has no match | When the user explicitly asks for lyrics and accepts the extra API cost |
| `--no-ai-lyrics` | Force-disable AI lyrics for this run | When the user wants only verified (LRCLib) lyrics |
| `-y`, `--yes` | Auto-approve in REPL mode (no effect with `-p`, which already auto-approves) | Rarely; agents should prefer `-p` |
| `--show` | Print current config (API key redacted), exit | Prerequisite check |
| `--config` | Re-run interactive setup | NEVER call from an agent — it prompts for an API key |
| `--no-banner` | Suppress ASCII banner in REPL | Cosmetic |

`--ai-lyrics` and `--no-ai-lyrics` are mutually exclusive.

## Choosing flags from the user's intent

| User asks for | Recommended invocation |
|---|---|
| "Download X by Y as mp3" (one song) | `mindown --first -p "X by Y as mp3"` |
| "Download X by Y" (no format given) | `mindown --first -p "X by Y"` — the model defaults to mp3 best |
| "Download top 5 / all / a list of songs" | `mindown -p "<their phrasing>"` (no `--first`) |
| "Download the music video for X as mp4 1080p" | `mindown --first -p "X music video as mp4 1080p"` |
| "...and include the lyrics" | Add `--ai-lyrics` when LRCLib often misses the artist (indie, very new tracks, non-English) |

Pass the user's intent through verbatim in `-p` when reasonable —
the model is the planner. Do not over-edit their prompt.

## Examples

```bash
# Single song, audio
mindown --first -p "<song> by <artist> as mp3"

# Music video
mindown --first -p "<song> music video as mp4 1080p"

# Multiple songs (no --first)
mindown -p "top 5 songs from <artist>"

# With AI lyrics fallback
mindown --first --ai-lyrics -p "<song> by <artist> as mp3"

# Different audio container / quality
mindown --first -p "<song> by <artist> as wav"
mindown --first -p "<song> by <artist> as m4a 256"
```

## What gets created

Files land in the user's configured download directory (default
`~/Downloads/Mindown`, override with `/dir <path>` in REPL or by
re-running `--config`). Filenames follow the template
`%(title).200B [%(id)s].%(ext)s`, so duplicate titles never clobber.

After every successful audio download, the file is rewritten in place
with these tags (where iTunes / LRCLib data is available):

| Field | Source | ID3v2.3 / iTunes atom |
|---|---|---|
| title / artist / album / album_artist / composer | iTunes Search | TIT2/TPE1/TALB/TPE2/TCOM · ©nam/©ART/©alb/aART/©wrt |
| year | iTunes Search · `releaseDate` | TYER · ©day |
| genre | iTunes Search · `primaryGenreName` | TCON · ©gen |
| track / disc | iTunes Search | TRCK/TPOS · trkn/disk |
| copyright | iTunes album lookup | TCOP · cprt |
| cover art (1200×1200) | iTunes Search | APIC · covr |
| lyrics | LRCLib (or AI fallback if `--ai-lyrics`) | USLT (lang=eng) · ©lyr |

Video downloads are NOT tagged — yt-dlp's defaults apply.

## Reading the output

The CLI emits structured-ish lines. To extract the saved path
programmatically, look for `✓ saved: <path>` in stdout. To check
whether tagging succeeded, look for `✓ tagged · …` (success) vs
`! tag skipped — <reason>` (warning, file is still there).

Per-item queue progress lines look like:
```
queue: ████████░░░░░░░░░░░░░░░░ 1/3 ·  33.3%
```
And the model's final assistant message is prefixed with `mindown ` —
something like `mindown downloaded 3 of 3.`

Exit codes: `0` success, `130` Ctrl-C, `1` config not set up.

## Failure modes and how to handle them

| Symptom | What it means | What to do |
|---|---|---|
| `API key is required.` | Config file missing or `api_key` empty | Tell the user to run `mindown --config` |
| `yt-dlp not found — run /config` | yt-dlp path invalid | Check `which yt-dlp`, then `mindown --config` to pick it up |
| `API error 401 …` | Bad API key | User edits config or re-runs `mindown --config` |
| `API error 429 …` | Rate limit | Back off and retry |
| `network error: …` | Transient | Retry once |
| `no iTunes match` after a successful download | Track wasn't found in iTunes; file is still saved untagged | Not an error — note in summary |
| Downloads succeed but `tag skipped — ffmpeg tag write failed` | Usually a codec/container issue with the muxed file | File is still on disk; surface the warning |

If the model proposes nothing (the `propose_downloads` tool isn't
called), it usually means the request was off-task — try refining the
prompt with format / artist / song specifics.

## Safety / things NOT to do

- Do NOT run `mindown --config` from an agent. It uses `getpass` to
  collect the API key — only the user should type it.
- Do NOT write to `~/.config/mindown-cli/config.json` directly. The
  user owns that file.
- Do NOT pipe untrusted text into `-p` without sanitizing — the
  prompt becomes a chat message; while the system prompt constrains
  the tool calls, you still control the user-side input.
- Do NOT download for the user without their request (this skill is
  user-driven, not autonomous).
- Be aware that `--ai-lyrics` makes a follow-up API call per audio
  track. Cost scales with the number of tracks downloaded.

## Verifying after a run

After a successful run:
```bash
# List the most recent files in the user's download dir
ls -lt "$(python3 -c 'import json,pathlib;p=pathlib.Path.home()/".config/mindown-cli/config.json";print(json.loads(p.read_text())["download_dir"])')" | head -5

# Inspect tags on the most recent audio file
ffprobe -v error -show_entries format_tags <path/to/file.mp3>
```

## One-liner cheat sheet

```bash
# Default: single song as mp3, with iTunes tags + LRCLib lyrics
mindown --first -p "<song> by <artist>"

# With AI lyrics if LRCLib misses
mindown --first --ai-lyrics -p "<song> by <artist>"

# Music video
mindown --first -p "<song> music video as mp4 1080p"

# Bulk
mindown -p "top 10 <artist> songs"
```

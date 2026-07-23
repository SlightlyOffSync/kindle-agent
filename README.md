# Kindle Agent Feed

Push coding-agent replies to a jailbroken Kindle Oasis so you can read them on e-ink.

This is **not** a long-running daemon or system service. Mac-side work is **user-space hooks**: when Cursor or Codex finishes a turn, a small script writes the inbox, renders page PNGs, and SSH-pushes to the Kindle. Nothing needs to sit in Login Items for that path — hooks only run when an agent runs.

The Kindle app itself is also user-space: open it from **KUAL → Agent Feed** when you want to read. You can add a shortcut later if you like; it is not required.

## Architecture

```
Cursor / Codex turn ends
        │
        ▼
   hook script (user process)
        │
        ▼
  inbox/sessions/<agent>-<id>/
    meta.json · feed.md · pages/*.png
        │
        ▼
  scripts/push-to-kindle.sh  →  SSH → /mnt/us/agentfeed/
        │
        ▼
  KUAL → Agent Feed (library + reader)
```

## Prerequisites (Mac)

- Python 3 with deps in `requirements.txt` (mistune, PyMuPDF, Pillow; Pygments optional for code highlighting)
- `sshpass` (Homebrew) for password SSH, or key-based SSH to the Kindle
- Kindle on Wi‑Fi with SSH reachable (USBNetLite / Dropbear)

Config lives in `config/kindle.env` (gitignored). Copy the example and fill in your Kindle SSH details:

```bash
cp config/kindle.env.example config/kindle.env
chmod 600 config/kindle.env
./scripts/discover-kindle.sh   # optional: find/update host
```

Cursor hook wiring example: `config/cursor-hooks.json.example` (point the command at this repo’s `scripts/cursor-inbox-hook.py`).
## Prerequisites (Kindle)

Jailbroken Oasis-class device with KUAL. Install once (USB mass storage or SSH):

- `device/extensions/agentfeed/` → `/mnt/us/extensions/agentfeed/`
- Feed data lands at `/mnt/us/agentfeed/`

Use `/mnt/us/libkh/bin/fbink` for image blit (KOReader’s `fbink` often has no image support).

## Hooks (user-space producers)

Hooks fire **at the end of each agent turn**, not when you open the Kindle. An agent can run many turns before you read — that is expected.

- Each turn **appends** to that session’s `feed.md` (file-locked).
- Each turn **requests** a push; if a push is already running, later turns only mark the inbox dirty.
- The pusher **drains**: after SSH it re-checks whether more turns arrived and re-pushes if needed.

So the Kindle should end up with the full multi-turn transcript, not just the last message. Stress test: `scripts/test-multiturn-hooks.py`.

### Cursor

Already wired via `~/.cursor/hooks.json` → `afterAgentResponse` → `~/.cursor/hooks/kindle-agent-inbox.py`  
(project mirror: `.cursor/hooks.json` + `scripts/cursor-inbox-hook.py`)

Agent id in the library: **`cursor`**.

### Codex

Requires `[features] hooks = true` in `~/.codex/config.toml` (already set if you use other Codex hooks).

`~/.codex/hooks.json` wires:

| Event | Why |
|-------|-----|
| **PostToolUse** | Mid-turn: Codex often emits assistant *commentary* between tools; `Stop` never sees those |
| **Stop** | End of turn: drains transcript + `last_assistant_message` fallback (turns with no tools) |

Both run `scripts/codex-midturn-hook.py`, which tails `transcript_path` from a watermark under `inbox/.codex-tail/` and dedupes by message id.

First time (or after hook changes), open Codex TUI and run `/hooks` so the new commands are trusted.

Smoke-test without a full Codex turn:

```bash
echo '{"session_id":"smoke-1","cwd":"/tmp","model":"test","last_assistant_message":"Hello from Codex smoke."}' \
  | /usr/bin/python3 scripts/codex-stop-hook.py
```

Check `inbox/.codex-hook.log` and a new `inbox/sessions/codex-smoke-1/` folder. Push runs in the background if `config/kindle.env` is valid.

## Rendering

Mac-side only (no Kindle compute): `scripts/render_pages.py` uses **mistune** (real Markdown parser, tables/GFM plugins) → HTML → **PyMuPDF Story** pagination → grayscale PNGs. Dependencies in `requirements.txt`.

Hooks call this automatically; you can also run it yourself:

```bash
# all sessions
./scripts/push-to-kindle.sh

# one session
./scripts/push-to-kindle.sh cursor-6cbd846b-f24d-4bdf-9703-457b6e5589d1
```

Logs: `inbox/.push.log`.

## Kindle UX

1. KUAL → **Agent Feed → Open**
2. **Library**: page keys move · tap opens a session · `*` = unread/updated · power quits
3. **Reader**: page keys flip pages · tap (or upper key on page 1) returns to library · status strip shows `N/M` and `* new` when more pages arrived · power quits

Pages are Mac-rendered PNGs shown with FBInk’s **default** (readable) waveform — not the fast `DU` mode (that made text look thin).

While you are interacting, new pushes are noticed on the next keypress (`* new` / page-down at EOF). Mid-read pages do not flicker from a live push.

| Key | Library | Reader |
|-----|---------|--------|
| Upper page (104) | Previous session | Previous page (or library on page 1) |
| Lower page (109) | Next session | Next page |
| Tap | Open session | Back to library |
| Power | Quit | Quit |

## Sleep & battery

Agent Feed is **not** a background Kindle service. While it is open it holds `preventScreenSaver` so the screen stays on for reading.

- **5 minutes of inactivity** (no page key / tap / power): the pager releases that hold and lets powerd sleep. The process stays resident; it does **not** kill `waitforkey` (doing so can wedge Oasis input).
- **Any key after idle**: stay-awake is re-armed and the 5‑minute timer resets.
- **Power quit**: clears the screensaver hold, resumes the stock UI, and returns home — normal Kindle sleep applies after that.
- **Idle timeout** is `IDLE_SEC=300` in `device/extensions/agentfeed/bin/pager.sh`.

You do **not** need to keep the Kindle plugged in for normal use. Quit (or let it idle-sleep) when you are done. SSH pushes from the Mac only succeed while the Kindle is awake and on Wi‑Fi; there is no wake-on-agent-reply path.

## Inbox schema

```text
inbox/
  index.json
  sessions.tsv                 # BusyBox-friendly: id|agent|title|HH:MM|pages|unread
  sessions/<agent>-<conv>/
    meta.json
    feed.md
    pages/page-NNNN.png
    pages/manifest.json
```

Producers should not special-case the Kindle — only write this layout (via `scripts/inbox_lib.py`).

## Why not a system service?

A launchd / Login Item daemon would mostly idle waiting for agent traffic. Hooks already fire at the right moment and exit. Keeping everything user-space means:

- no always-on process to babysit
- no root LaunchDaemon
- optional Login Item only if you later want a separate always-on helper (not needed today)

## Useful paths

| Path | Role |
|------|------|
| `config/kindle.env.example` | SSH target template (copy → `kindle.env`) |
| `config/kindle.env` | Local SSH secrets (**gitignored**) |
| `scripts/inbox_lib.py` | Shared ingest / index |
| `scripts/render_pages.py` | Markdown → grayscale PNGs |
| `scripts/push-to-kindle.sh` | Render + tar-over-SSH |
| `scripts/codex-stop-hook.py` | Codex Stop producer |
| `device/extensions/agentfeed/` | KUAL extension + pager |
| `inbox/.hook.log` | Cursor hook log |
| `inbox/.codex-hook.log` | Codex hook log |
| `inbox/.push.log` | Push log |

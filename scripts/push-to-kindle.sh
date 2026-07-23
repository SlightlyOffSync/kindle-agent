#!/usr/bin/env bash
# Render session page images and push inbox (sessions + index) to Kindle over SSH.
# End-of-turn hooks may fire many times before a human reads — we serialize and
# drain so late turns still land in the final push.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INBOX="${KINDLE_AGENT_INBOX:-$ROOT/inbox}"
SESSIONS="$INBOX/sessions"
INDEX="$INBOX/index.json"
CONFIG="$ROOT/config/kindle.env"
LOG="$INBOX/.push.log"
KNOWN="$ROOT/config/known_hosts"
DISCOVER="$ROOT/scripts/discover-kindle.sh"
DIRTY="$INBOX/.push-dirty"

mkdir -p "$INBOX" "$SESSIONS" "$ROOT/config"
touch "$LOG"

ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "$(ts) $*" >>"$LOG"; }

if [[ -f "$CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG" || true
fi

SSH_HOST="${KINDLE_SSH_HOST:-}"
SSH_USER="${KINDLE_SSH_USER:-root}"
SSH_PORT="${KINDLE_SSH_PORT:-22}"
SSH_PASS="${KINDLE_SSH_PASSWORD:-}"
SSH_DEST="${KINDLE_SSH_DEST:-/mnt/us/agentfeed}"

if [[ -z "$SSH_PASS" ]]; then
  echo "Missing KINDLE_SSH_PASSWORD. Copy config/kindle.env.example → config/kindle.env" >&2
  exit 1
fi

ONLY_SID="${1:-}"

# Serialize pushes (macOS has no flock(1); atomic mkdir).
LOCK_DIR="$INBOX/.push.lock.d"
acquire_lock() {
  local i=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    i=$((i + 1))
    if [[ -d "$LOCK_DIR" ]]; then
      local age
      age=$(( $(date +%s) - $(stat -f %m "$LOCK_DIR" 2>/dev/null || echo 0) ))
      if [[ "$age" -gt 180 ]]; then
        rm -rf "$LOCK_DIR"
        continue
      fi
    fi
    if [[ "$i" -gt 180 ]]; then
      return 1
    fi
    sleep 1
  done
  return 0
}
release_lock() { rm -rf "$LOCK_DIR" 2>/dev/null || true; }
trap release_lock EXIT

if ! acquire_lock; then
  log "skip: could not acquire push lock"
  exit 0
fi

feed_fp() {
  if [[ -n "$ONLY_SID" ]]; then
    local f="$SESSIONS/$ONLY_SID/feed.md"
    if [[ -f "$f" ]]; then
      wc -c <"$f" | tr -d ' '
    else
      echo 0
    fi
  else
    find "$SESSIONS" -name feed.md -print0 2>/dev/null \
      | xargs -0 cat 2>/dev/null \
      | wc -c \
      | tr -d ' '
  fi
}

render_sessions() {
  KINDLE_AGENT_ROOT="$ROOT" uv run --directory "$ROOT" python - <<'PY' "$ONLY_SID"
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["KINDLE_AGENT_ROOT"])
sys.path.insert(0, str(ROOT / "scripts"))
from inbox_lib import rebuild_index, session_dir, update_session_meta, read_json

only = sys.argv[1] if len(sys.argv) > 1 else ""
sessions_root = Path(os.environ.get("KINDLE_AGENT_INBOX", ROOT / "inbox")) / "sessions"
sessions_root.mkdir(parents=True, exist_ok=True)

targets = []
if only:
    targets = [only]
else:
    targets = [p.name for p in sessions_root.iterdir() if p.is_dir()]

for sid in targets:
    d = session_dir(sid)
    feed = d / "feed.md"
    if not feed.exists():
        continue
    pages = d / "pages"
    import subprocess, json
    proc = subprocess.run(
        ["uv", "run", "--directory", str(ROOT), "python", "scripts/render_pages.py", str(feed), str(pages)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        continue
    try:
        manifest = json.loads(proc.stdout.strip() or "{}")
    except Exception:
        manifest = {}
    meta = read_json(d / "meta.json", {})
    if not isinstance(meta, dict):
        meta = {"id": sid, "agent": "unknown", "conversation_id": sid}
    # Only refresh page count — do NOT bump updated_at / unread for every session
    # on a bulk push (that made the library show identical times and all "*").
    update_session_meta(
        sid,
        agent=str(meta.get("agent") or "unknown"),
        conversation_id=str(meta.get("conversation_id") or sid),
        cwd=str(meta.get("cwd") or ""),
        model=str(meta.get("model") or ""),
        title=meta.get("title"),
        pages=int(manifest.get("pages") or 0),
        touch=False,
    )
rebuild_index()
print("rendered", len(targets))
PY
}

ssh_opts=(
  -o ConnectTimeout=5
  -o StrictHostKeyChecking=accept-new
  -o UserKnownHostsFile="$KNOWN"
  -o PreferredAuthentications=password
  -o PubkeyAuthentication=no
  -o NumberOfPasswordPrompts=1
  -p "${SSH_PORT}"
)

run_ssh() {
  sshpass -p "${SSH_PASS}" ssh "${ssh_opts[@]}" "${SSH_USER}@${1}" "${2}"
}

push_ssh() {
  if [[ -z "${SSH_HOST}" && -x "${DISCOVER}" ]]; then
    SSH_HOST="$("${DISCOVER}" 2>/dev/null || true)"
  fi
  if [[ -z "${SSH_HOST}" ]]; then
    log "skip: no SSH host"
    return 1
  fi

  local err
  err=$(mktemp)
  if ! run_ssh "$SSH_HOST" "mkdir -p '${SSH_DEST}/sessions' '${SSH_DEST}/staging'" 2>"$err"; then
    log "ssh mkdir failed: $(tr '\n' ' ' <"$err")"
    rm -f "$err"
    return 1
  fi

  local STAGE="${SSH_DEST}/staging"
  if ! run_ssh "$SSH_HOST" "rm -rf '${STAGE}' && mkdir -p '${STAGE}/sessions'" 2>"$err"; then
    log "ssh stage failed: $(tr '\n' ' ' <"$err")"
    rm -f "$err"
    return 1
  fi

  if [[ -n "$ONLY_SID" && -d "$SESSIONS/$ONLY_SID" ]]; then
    if ! (
      cd "$SESSIONS/$ONLY_SID" && tar cf - .
    ) | sshpass -p "${SSH_PASS}" ssh "${ssh_opts[@]}" "${SSH_USER}@${SSH_HOST}" \
        "mkdir -p '${STAGE}/sessions/${ONLY_SID}' && cd '${STAGE}/sessions/${ONLY_SID}' && tar xf -" 2>"$err"; then
      log "ssh tar session failed: $(tr '\n' ' ' <"$err")"
      rm -f "$err"
      return 1
    fi
  else
    if ! (
      cd "$SESSIONS" && tar cf - .
    ) | sshpass -p "${SSH_PASS}" ssh "${ssh_opts[@]}" "${SSH_USER}@${SSH_HOST}" \
        "cd '${STAGE}/sessions' && tar xf -" 2>"$err"; then
      log "ssh tar sessions failed: $(tr '\n' ' ' <"$err")"
      rm -f "$err"
      return 1
    fi
  fi

  if [[ -f "$INDEX" ]]; then
    if ! sshpass -p "${SSH_PASS}" scp \
      -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KNOWN" \
      -o PreferredAuthentications=password -o PubkeyAuthentication=no -P "${SSH_PORT}" \
      "$INDEX" "${SSH_USER}@${SSH_HOST}:${STAGE}/index.json" 2>"$err"; then
      log "ssh scp index failed: $(tr '\n' ' ' <"$err")"
      rm -f "$err"
      return 1
    fi
  fi
  if [[ -f "$INBOX/sessions.tsv" ]]; then
    sshpass -p "${SSH_PASS}" scp \
      -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$KNOWN" \
      -o PreferredAuthentications=password -o PubkeyAuthentication=no -P "${SSH_PORT}" \
      "$INBOX/sessions.tsv" "${SSH_USER}@${SSH_HOST}:${STAGE}/sessions.tsv" 2>/dev/null || true
  fi

  local swap_cmd
  if [[ -n "$ONLY_SID" ]]; then
    swap_cmd="rm -rf '${SSH_DEST}/sessions/${ONLY_SID}.prev'; if [ -d '${SSH_DEST}/sessions/${ONLY_SID}' ]; then mv '${SSH_DEST}/sessions/${ONLY_SID}' '${SSH_DEST}/sessions/${ONLY_SID}.prev'; fi; mv '${STAGE}/sessions/${ONLY_SID}' '${SSH_DEST}/sessions/${ONLY_SID}'; cp -f '${STAGE}/index.json' '${SSH_DEST}/index.json' 2>/dev/null; cp -f '${STAGE}/sessions.tsv' '${SSH_DEST}/sessions.tsv' 2>/dev/null; date -u +%Y-%m-%dT%H:%M:%SZ > '${SSH_DEST}/pushed_at'; rm -rf '${SSH_DEST}/sessions/${ONLY_SID}.prev' '${STAGE}'"
  else
    swap_cmd="rm -rf '${SSH_DEST}/sessions.prev'; if [ -d '${SSH_DEST}/sessions' ]; then mv '${SSH_DEST}/sessions' '${SSH_DEST}/sessions.prev'; fi; mv '${STAGE}/sessions' '${SSH_DEST}/sessions'; cp -f '${STAGE}/index.json' '${SSH_DEST}/index.json' 2>/dev/null; cp -f '${STAGE}/sessions.tsv' '${SSH_DEST}/sessions.tsv' 2>/dev/null; date -u +%Y-%m-%dT%H:%M:%SZ > '${SSH_DEST}/pushed_at'; rm -rf '${SSH_DEST}/sessions.prev' '${STAGE}'"
  fi

  if ! run_ssh "$SSH_HOST" "$swap_cmd" 2>"$err"; then
    log "ssh swap failed: $(tr '\n' ' ' <"$err")"
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  return 0
}

# Drain loop: agent may keep appending turns while we render/push.
pass=0
max_pass=6
while [[ "$pass" -lt "$max_pass" ]]; do
  pass=$((pass + 1))
  rm -f "$DIRTY"
  before=$(feed_fp)
  render_sessions >/dev/null
  if ! push_ssh; then
    log "push failed on pass=${pass}"
    exit 0
  fi
  # Let overlapping hook appends settle, then see if more arrived.
  sleep 0.4
  after=$(feed_fp)
  if [[ ! -f "$DIRTY" && "$before" == "$after" ]]; then
    break
  fi
  log "drain: more turns arrived (fp ${before}->${after}), re-push pass=${pass}"
done

cat >"$CONFIG" <<EOF
KINDLE_SSH_HOST=${SSH_HOST}
KINDLE_SSH_USER=${SSH_USER}
KINDLE_SSH_PORT=${SSH_PORT}
KINDLE_SSH_PASSWORD=${SSH_PASS}
KINDLE_SSH_DEST=${SSH_DEST}
KINDLE_AGENT_MAX_PAGES=${KINDLE_AGENT_MAX_PAGES:-15}
KINDLE_AGENT_MAX_FEED_BYTES=${KINDLE_AGENT_MAX_FEED_BYTES:-24576}
EOF
chmod 600 "$CONFIG"

n=$(uv run --directory "$ROOT" python -c 'import json;print(len(json.load(open("'"$INDEX"'")).get("sessions",[])))' 2>/dev/null || echo 0)
log "ssh: pushed sessions=${n} only=${ONLY_SID:-all} passes=${pass} to ${SSH_USER}@${SSH_HOST}:${SSH_DEST}"
exit 0

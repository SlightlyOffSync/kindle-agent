#!/usr/bin/env bash
# Discover Kindle SSH on LAN. Prints IP; exit 1 if none.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${ROOT}/config/kindle.env"
PASS="${KINDLE_SSH_PASSWORD:-}"
USER="${KINDLE_SSH_USER:-root}"
PORT="${KINDLE_SSH_PORT:-22}"
KNOWN="$ROOT/config/known_hosts"
mkdir -p "$ROOT/config"

if [[ -f "${CONFIG}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG}" || true
  PASS="${KINDLE_SSH_PASSWORD:-$PASS}"
  USER="${KINDLE_SSH_USER:-$USER}"
  PORT="${KINDLE_SSH_PORT:-$PORT}"
fi

if [[ -z "$PASS" ]]; then
  echo "Missing KINDLE_SSH_PASSWORD. Copy config/kindle.env.example → config/kindle.env" >&2
  exit 1
fi

try_host() {
  local host="$1"
  if ! nc -z -G 1 "$host" "$PORT" >/dev/null 2>&1; then
    return 1
  fi
  if command -v sshpass >/dev/null 2>&1; then
    if sshpass -p "$PASS" ssh \
      -o ConnectTimeout=3 \
      -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile="$KNOWN" \
      -o PreferredAuthentications=password \
      -o PubkeyAuthentication=no \
      -p "$PORT" "${USER}@${host}" 'test -d /mnt/us && echo kindle-ok' 2>/dev/null | grep -q kindle-ok; then
      echo "$host"
      return 0
    fi
  fi
  return 1
}

# Saved config host first
if [[ -n "${KINDLE_SSH_HOST:-}" ]] && try_host "${KINDLE_SSH_HOST}"; then
  exit 0
fi

# USB network mode default
if try_host 192.168.15.244; then
  exit 0
fi

# Scan current /24 for open SSH, then probe Kindle login
scan_prefix() {
  local prefix="$1"
  local pids=()
  local out
  out=$(mktemp)
  for last in $(seq 1 254); do
    (
      host="${prefix}.${last}"
      nc -z -G 1 "$host" "$PORT" >/dev/null 2>&1 && echo "$host" >>"$out"
    ) &
    pids+=($!)
    # keep fan-out bounded
    if (( ${#pids[@]} >= 40 )); then
      wait "${pids[0]}" || true
      pids=("${pids[@]:1}")
    fi
  done
  wait || true
  sort -u "$out"
  rm -f "$out"
}

for iface in en0 en1; do
  ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
  [[ -z "$ip" ]] && continue
  prefix=$(echo "$ip" | awk -F. '{print $1"."$2"."$3}')
  while read -r host; do
    [[ -z "$host" || "$host" == "$ip" ]] && continue
    if try_host "$host"; then
      exit 0
    fi
  done < <(scan_prefix "$prefix")
done

exit 1

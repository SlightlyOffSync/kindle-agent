#!/bin/sh
# Agent Feed — session library + image reader with on-device status overlay.
# Keys: upper/lower = navigate; touch = open / back to library; power = quit.

set -u

ROOT="/mnt/us/agentfeed"
SESSIONS="${ROOT}/sessions"
INDEX="${ROOT}/index.json"
STATE="${ROOT}/app.state"
LOG="${ROOT}/pager.log"

FBINK="/mnt/us/libkh/bin/fbink"
[ -x "${FBINK}" ] || FBINK="/mnt/us/extensions/MRInstaller/bin/KHF/fbink"
[ -x "${FBINK}" ] || FBINK="/mnt/us/koreader/fbink"
EIPS="/usr/sbin/eips"

FONT_REG="/mnt/us/koreader/fonts/noto/NotoSerif-Regular.ttf"
FONT_BOLD="/mnt/us/koreader/fonts/noto/NotoSerif-Bold.ttf"
# Status strip near bottom of 1680px screen
STATUS_TOP=1610
STATUS_SIZE=12
# Library typography hierarchy (TrueType points @ ~300dpi)
# Note: 1pt ≈ 4.17px at 300dpi — FBInk truncates to 0 lines if the
# top/bottom margins leave less vertical room than one glyph.
LIB_TITLE_SIZE=20
LIB_LEGEND_SIZE=11
LIB_AGENT_SIZE=15
LIB_META_SIZE=12
LIB_LEFT=48
LIB_RIGHT=48
LIB_TITLE_TOP=40
LIB_LEGEND_TOP=120
LIB_LIST_TOP=190
LIB_AGENT_GAP=70
LIB_ROW_STEP=130
SCREEN_H=1680
PREFETCH=2
LOAD_RETRIES=6
LOAD_SLEEP_US=350000
# Allow Kindle sleep after this many seconds with no key/touch activity.
IDLE_SEC=300

KEY_PREV=104
KEY_NEXT=109
KEY_HOME=102
KEY_MENU=139
KEY_POWER=116
KEY_BACK=158
# Touch events observed on Oasis
KEY_TAP1=330
KEY_TAP2=333

MODE="library"   # library | reader
SEL=0
PAGE=1
SESSION_ID=""
SEEN_MAX=1
HAVE_NEW=0
DISPLAYED_STAMP=""
IGNORE_TAP_ONCE=0

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $*" >>"${LOG}" 2>/dev/null; }

ensure_dirs() {
	mkdir -p "${ROOT}" "${SESSIONS}" 2>/dev/null
	touch "${LOG}" 2>/dev/null
}

stop_ui() {
	lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
	if killall -0 awesome 2>/dev/null; then
		killall -STOP awesome 2>/dev/null
		STOPPED_AWESOME=1
	else
		STOPPED_AWESOME=0
	fi
}

keep_awake() {
	lipc-set-prop com.lab126.powerd preventScreenSaver 1 2>/dev/null
}

allow_sleep() {
	log "idle ${IDLE_SEC}s — allowing sleep"
	lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
	# Let powerd suspend when ready (do not fake the power button).
	lipc-set-prop com.lab126.powerd deferSuspend 0 2>/dev/null
}

restore_ui() {
	lipc-set-prop com.lab126.powerd preventScreenSaver 0 2>/dev/null
	if [ "${STOPPED_AWESOME:-0}" = "1" ]; then
		killall -CONT awesome 2>/dev/null
	fi
	lipc-set-prop com.lab126.appmgrd start app://com.lab126.booklet.home 2>/dev/null
}

cleanup() { restore_ui; }
trap cleanup EXIT INT TERM HUP

fbink_msg() {
	# Status strip — default waveform (readable text); hard timeout as safety.
	if [ -f "${FONT_REG}" ]; then
		if [ -x /usr/bin/timeout ]; then
			/usr/bin/timeout -s KILL 5 "${FBINK}" -k "top=${STATUS_TOP},left=0,width=1264,height=70" >/dev/null 2>&1
			/usr/bin/timeout -s KILL 5 "${FBINK}" -q \
				-t "regular=${FONT_REG},size=${STATUS_SIZE},top=${STATUS_TOP},bottom=12,left=40,right=40" \
				"$1" >/dev/null 2>&1
		else
			"${FBINK}" -k "top=${STATUS_TOP},left=0,width=1264,height=70" >/dev/null 2>&1
			"${FBINK}" -q \
				-t "regular=${FONT_REG},size=${STATUS_SIZE},top=${STATUS_TOP},bottom=12,left=40,right=40" \
				"$1" >/dev/null 2>&1
		fi
	else
		"${FBINK}" -q -S 1 -pm -y -1 "$1" >/dev/null 2>&1
	fi
}

# Draw one TrueType line at an absolute top pixel.
# Optional 4th arg "bold".
# Do NOT clamp with a tight bottom margin: at 300dpi, pt→px is ~4.17x, and a
# too-small band makes FBInk truncate to 0 lines (blank library).
fbink_line() {
	_size="$1"
	_top="$2"
	_text="$3"
	_style="${4:-}"
	[ -n "${_text}" ] || return 0
	if [ ! -f "${FONT_REG}" ]; then
		"${FBINK}" -q -S 1 -pm "${_text}" >/dev/null 2>&1
		return 0
	fi
	if [ "${_style}" = "bold" ] && [ -f "${FONT_BOLD}" ]; then
		"${FBINK}" -q \
			-t "regular=${FONT_REG},bold=${FONT_BOLD},size=${_size},top=${_top},bottom=24,left=${LIB_LEFT},right=${LIB_RIGHT}" \
			"${_text}" >/dev/null 2>&1
	else
		"${FBINK}" -q \
			-t "regular=${FONT_REG},size=${_size},top=${_top},bottom=24,left=${LIB_LEFT},right=${LIB_RIGHT}" \
			"${_text}" >/dev/null 2>&1
	fi
}

# --- index / session helpers ---

session_count() {
	if [ -f "${INDEX}" ]; then
		grep -c '"id"' "${INDEX}" 2>/dev/null | tr -d ' '
	else
		ls -1d "${SESSIONS}"/* 2>/dev/null | wc -l | tr -d ' '
	fi
}

# Build flat list files for BusyBox from sessions.tsv (id|agent|title|HH:MM|pages|unread)
rebuild_lists() {
	IDS="${ROOT}/.ids"
	AGENTS="${ROOT}/.agents"
	METAS="${ROOT}/.metas"
	: >"${IDS}"
	: >"${AGENTS}"
	: >"${METAS}"
	TSV="${ROOT}/sessions.tsv"
	if [ -f "${TSV}" ]; then
		while IFS='|' read -r id agent title hm pages unread; do
			[ -n "${id}" ] || continue
			echo "${id}" >>"${IDS}"
			mark=""
			[ "${unread}" = "1" ] && mark="* "
			# Hide * if opened at this page count (simple; avoids heavy stamp work)
			if [ -n "${mark}" ] && [ -f "${ROOT}/.openstamp" ]; then
				if grep -q "^${id}|${pages}$" "${ROOT}/.openstamp" 2>/dev/null; then
					mark=""
				fi
			fi
			echo "${mark}${agent}" >>"${AGENTS}"
			echo "${title}  ·  ${hm}  ·  p${pages}" >>"${METAS}"
		done <"${TSV}"
	fi
	if [ ! -s "${IDS}" ]; then
		for d in "${SESSIONS}"/*; do
			[ -d "$d" ] || continue
			sid=$(basename "$d")
			case "${sid}" in
				._*) continue ;;
			esac
			echo "$sid" >>"${IDS}"
			echo "${sid}" >>"${AGENTS}"
			echo "" >>"${METAS}"
		done
	fi
}

list_len() {
	wc -l <"${ROOT}/.ids" 2>/dev/null | tr -d ' '
}

id_at() {
	# 0-based
	sed -n "$(($1 + 1))p" "${ROOT}/.ids" 2>/dev/null
}

agent_at() {
	sed -n "$(($1 + 1))p" "${ROOT}/.agents" 2>/dev/null
}

meta_at() {
	sed -n "$(($1 + 1))p" "${ROOT}/.metas" 2>/dev/null
}

session_pages_dir() {
	echo "${SESSIONS}/${1}/pages"
}

page_count_for() {
	pd=$(session_pages_dir "$1")
	n=0
	# Explicit glob avoids AppleDouble / odd ls pipe issues on BusyBox
	for f in "${pd}"/page-[0-9][0-9][0-9][0-9].png; do
		[ -f "$f" ] || continue
		n=$((n + 1))
	done
	echo "${n}"
}

page_file() {
	printf '%s/page-%04d.png' "$(session_pages_dir "${SESSION_ID}")" "$1"
}

show_loading() {
	msg="${1:-loading…}"
	# Bitmap text only — TrueType mid-screen has caused blank/hang edge cases
	"${FBINK}" -q -S 1 -pmM "${msg}" >/dev/null 2>&1
}

# Warm next/prev page into FS cache (foreground, tiny read — no background dd).
prefetch_pages() {
	[ -n "${SESSION_ID}" ] || return 0
	count=$(page_count_for "${SESSION_ID}")
	count=${count:-0}
	[ "${count}" -gt 0 ] || return 0
	n=1
	while [ "${n}" -le "${PREFETCH}" ]; do
		p=$((PAGE + n))
		if [ "${p}" -le "${count}" ]; then
			f=$(page_file "${p}")
			[ -f "${f}" ] && dd if="${f}" of=/dev/null bs=4k count=2 2>/dev/null
		fi
		p=$((PAGE - n))
		if [ "${p}" -ge 1 ]; then
			f=$(page_file "${p}")
			[ -f "${f}" ] && dd if="${f}" of=/dev/null bs=4k count=2 2>/dev/null
		fi
		n=$((n + 1))
	done
}

wait_for_page_file() {
	target="$1"
	f=$(page_file "${target}")
	[ -f "${f}" ] && return 0
	show_loading "loading ${target}…"
	r=0
	while [ "${r}" -lt "${LOAD_RETRIES}" ]; do
		usleep "${LOAD_SLEEP_US}" 2>/dev/null || sleep 1
		count=$(page_count_for "${SESSION_ID}")
		count=${count:-0}
		f=$(page_file "${target}")
		[ -f "${f}" ] && return 0
		if [ "${count}" -gt 0 ] && [ "${target}" -gt "${count}" ]; then
			return 1
		fi
		r=$((r + 1))
		show_loading "loading ${target}… (${r})"
	done
	return 1
}

# Lightweight stamp — no grep -o (can wedge BusyBox edge cases); manifest is tiny.
content_stamp_for() {
	mf="$(session_pages_dir "$1")/manifest.json"
	if [ -f "${mf}" ]; then
		# single line fingerprint
		wc -c <"${mf}" | tr -d ' '
	else
		echo "0"
	fi
}

display_image() {
	# Default waveform (GC16-class) for readable text — no DU.
	# Hard timeout so a stuck blit cannot wedge the key loop forever
	# (BusyBox timeout supports -s KILL, not GNU -k).
	f="$1"
	if [ -x /usr/bin/timeout ]; then
		/usr/bin/timeout -s KILL 12 \
			"${FBINK}" -g "file=${f},halign=center,valign=center" \
			>/dev/null 2>&1 && return 0
		/usr/bin/timeout -s KILL 12 \
			"${EIPS}" -g "${f}" >/dev/null 2>&1 && return 0
	else
		"${FBINK}" -g "file=${f},halign=center,valign=center" >/dev/null 2>&1 && return 0
		[ -x "${EIPS}" ] && "${EIPS}" -g "${f}" >/dev/null 2>&1 && return 0
	fi
	return 1
}

reader_status_text() {
	count=$(page_count_for "${SESSION_ID}")
	count=${count:-0}
	status="${PAGE}/${count}"
	if [ "${HAVE_NEW}" -eq 1 ]; then
		status="${status}  * new  (page down)"
	fi
	status="${status}  | tap=sessions  power=quit"
	echo "${status}"
}

# Soft-check for newly pushed content without full page redraw.
poll_reader_updates() {
	[ "${MODE}" = "reader" ] || return 0
	[ -n "${SESSION_ID}" ] || return 0
	count=$(page_count_for "${SESSION_ID}")
	count=${count:-0}
	stamp=$(content_stamp_for "${SESSION_ID}")
	new=0
	if [ "${count}" -gt "${PAGE}" ]; then
		new=1
	elif [ "${count}" -eq "${PAGE}" ] && [ -n "${stamp}" ] && [ "${stamp}" != "${DISPLAYED_STAMP}" ]; then
		new=1
	fi
	if [ "${new}" -eq 1 ]; then
		if [ "${HAVE_NEW}" -ne 1 ]; then
			HAVE_NEW=1
			fbink_msg "$(reader_status_text)"
			log "poll *new page=${PAGE} count=${count}"
		fi
	else
		if [ "${HAVE_NEW}" -eq 1 ]; then
			HAVE_NEW=0
			fbink_msg "$(reader_status_text)"
		fi
	fi
}

# --- drawing ---

draw_library() {
	rebuild_lists
	n=$(list_len)
	n=${n:-0}
	if [ "${n}" -gt 0 ] && [ "${SEL}" -ge "${n}" ]; then
		SEL=$((n - 1))
	fi
	[ "${SEL}" -lt 0 ] && SEL=0

	"${FBINK}" -k >/dev/null 2>&1

	# Title
	fbink_line "${LIB_TITLE_SIZE}" "${LIB_TITLE_TOP}" "Agent Feed" bold
	# Legend (smaller)
	fbink_line "${LIB_LEGEND_SIZE}" "${LIB_LEGEND_TOP}" "page keys move · tap opens · power quits"

	top="${LIB_LIST_TOP}"
	if [ "${n}" -eq 0 ]; then
		fbink_line "${LIB_META_SIZE}" "${top}" "(no sessions yet)"
		top=$((top + LIB_ROW_STEP))
	else
		i=0
		while [ "$i" -lt "$n" ]; do
			agent=$(agent_at "$i")
			meta=$(meta_at "$i")
			if [ "$i" -eq "${SEL}" ]; then
				agent_line="> ${agent}"
			else
				agent_line="  ${agent}"
			fi
			fbink_line "${LIB_AGENT_SIZE}" "${top}" "${agent_line}" bold
			fbink_line "${LIB_META_SIZE}" "$((top + LIB_AGENT_GAP))" "    ${meta}"
			top=$((top + LIB_ROW_STEP))
			i=$((i + 1))
		done
	fi

	# Footer legend
	fbink_line "${LIB_LEGEND_SIZE}" "${top}" "* = updated"
	save_state
}

show_reader_page() {
	count=$(page_count_for "${SESSION_ID}")
	count=${count:-0}
	if [ "${count}" -le 0 ]; then
		"${FBINK}" -k >/dev/null 2>&1
		show_loading "no pages yet"
		save_state
		return
	fi
	[ "${PAGE}" -lt 1 ] && PAGE=1
	[ "${PAGE}" -gt "${count}" ] && PAGE=${count}

	if ! wait_for_page_file "${PAGE}"; then
		fbink_msg "${PAGE}/?  waiting for image  | tap=sessions"
		log "missing page=${PAGE} sid=${SESSION_ID}"
		save_state
		return
	fi

	f=$(page_file "${PAGE}")
	if ! display_image "${f}"; then
		"${FBINK}" -k >/dev/null 2>&1
		show_loading "image display failed"
		log "display failed page=${PAGE} file=${f}"
		save_state
		return
	fi

	[ "${PAGE}" -gt "${SEEN_MAX}" ] && SEEN_MAX=${PAGE}
	DISPLAYED_STAMP=$(content_stamp_for "${SESSION_ID}")
	HAVE_NEW=0
	# Status strip only — skip heavy prefetch (was stalling page-flips on FAT)
	fbink_msg "$(reader_status_text)"
	save_state
}

open_selected() {
	sid=$(id_at "${SEL}")
	[ -n "${sid}" ] || return
	SESSION_ID="${sid}"
	MODE="reader"
	HAVE_NEW=0
	DISPLAYED_STAMP=""
	count=$(page_count_for "${SESSION_ID}")
	count=${count:-0}
	if [ "${count}" -lt 1 ]; then
		PAGE=1
		log "open session=${SESSION_ID} empty"
		show_reader_page
		return
	fi
	# Start at latest page
	PAGE=${count}
	SEEN_MAX=${PAGE}
	# Record opened at this page count (for * clearing)
	printf '%s|%s\n' "${SESSION_ID}" "${count}" >>"${ROOT}/.openstamp"
	log "open session=${SESSION_ID} page=${PAGE} count=${count}"
	# Let touch release settle so it is not handled as "back to library"
	usleep 250000 2>/dev/null || sleep 1
	show_reader_page
	IGNORE_TAP_ONCE=1
}

back_to_library() {
	MODE="library"
	SESSION_ID=""
	HAVE_NEW=0
	rebuild_lists
	draw_library
	log "library"
}

save_state() {
	printf 'MODE=%s\nSEL=%s\nPAGE=%s\nSESSION_ID=%s\nSEEN_MAX=%s\n' \
		"${MODE}" "${SEL}" "${PAGE}" "${SESSION_ID}" "${SEEN_MAX}" >"${STATE}"
}

load_state() {
	MODE="library"
	SEL=0
	PAGE=1
	SESSION_ID=""
	SEEN_MAX=1
	if [ -f "${STATE}" ]; then
		# shellcheck disable=SC1090
		. "${STATE}"
	fi
}

wait_key() {
	# Block on waitforkey without SIGTERM (killing it can wedge Oasis input).
	# Poll alongside it so we can release preventScreenSaver after IDLE_SEC.
	keyf="${ROOT}/.waitkey"
	rm -f "${keyf}"
	waitforkey >"${keyf}" 2>/dev/null &
	wp=$!
	waited=0
	slept=0
	while true; do
		if [ -s "${keyf}" ] || ! kill -0 "${wp}" 2>/dev/null; then
			wait "${wp}" 2>/dev/null
			out=$(cat "${keyf}" 2>/dev/null) || out=""
			rm -f "${keyf}"
			# shellcheck disable=SC2086
			set -- ${out}
			code=${1:-0}
			state=${2:-0}

			# Any event (even filtered) counts as activity — re-arm stay-awake.
			waited=0
			if [ "${slept}" -eq 1 ]; then
				keep_awake
				slept=0
				log "wake from idle"
			fi

			if [ -z "${code}" ] || [ "${code}" = "0" ]; then
				waitforkey >"${keyf}" 2>/dev/null &
				wp=$!
				continue
			fi
			# Page-turn keys: key-down only
			if [ "${code}" = "${KEY_PREV}" ] || [ "${code}" = "${KEY_NEXT}" ]; then
				if [ "${state}" = "1" ]; then
					echo "${code}"
					return 0
				fi
				waitforkey >"${keyf}" 2>/dev/null &
				wp=$!
				continue
			fi
			# Power / home / back
			if [ "${code}" = "${KEY_POWER}" ] || [ "${code}" = "${KEY_HOME}" ] || [ "${code}" = "${KEY_MENU}" ] || [ "${code}" = "${KEY_BACK}" ]; then
				echo "${code}"
				return 0
			fi
			# Touch
			if [ "${code}" = "${KEY_TAP1}" ] || [ "${code}" = "${KEY_TAP2}" ]; then
				echo "${code}"
				return 0
			fi
			waitforkey >"${keyf}" 2>/dev/null &
			wp=$!
			continue
		fi

		sleep 1
		waited=$((waited + 1))
		if [ "${slept}" -eq 0 ] && [ "${waited}" -ge "${IDLE_SEC}" ]; then
			allow_sleep
			slept=1
		fi
	done
}

do_exit() {
	log "exit key=${1:-} mode=${MODE}"
	fbink_msg "bye"
	usleep 200000 2>/dev/null || sleep 1
	exit 0
}

# --- main ---

ensure_dirs
[ -x "${FBINK}" ] || { eips 0 0 "agentfeed: fbink missing"; exit 1; }

stop_ui
usleep 200000 2>/dev/null || sleep 1

load_state
# Always start in library so sessions are reachable
MODE="library"
rebuild_lists
draw_library
log "start library sel=${SEL} sessions=$(list_len)"

while true; do
	key=$(wait_key)
	log "key=${key} mode=${MODE} sel=${SEL} page=${PAGE} sid=${SESSION_ID}"

	case "${key}" in
		"${KEY_HOME}"|"${KEY_MENU}"|"${KEY_POWER}"|"${KEY_BACK}")
			do_exit "${key}"
			;;
	esac

	if [ "${MODE}" = "library" ]; then
		case "${key}" in
			"${KEY_NEXT}")
				n=$(list_len); n=${n:-0}
				if [ "$n" -gt 0 ] && [ "${SEL}" -lt $((n - 1)) ]; then
					SEL=$((SEL + 1))
					draw_library
				fi
				;;
			"${KEY_PREV}")
				if [ "${SEL}" -gt 0 ]; then
					SEL=$((SEL - 1))
					draw_library
				fi
				;;
			"${KEY_TAP1}"|"${KEY_TAP2}")
				open_selected
				;;
		esac
	else
		# Soft-check for pushes when user is already interacting
		poll_reader_updates
		# reader
		case "${key}" in
			"${KEY_TAP1}"|"${KEY_TAP2}")
				if [ "${IGNORE_TAP_ONCE}" = "1" ]; then
					IGNORE_TAP_ONCE=0
					log "ignore tap once after open"
				else
					back_to_library
				fi
				;;
			"${KEY_NEXT}")
				count=$(page_count_for "${SESSION_ID}")
				count=${count:-0}
				stamp=$(content_stamp_for "${SESSION_ID}")
				if [ "${PAGE}" -lt "${count}" ]; then
					PAGE=$((PAGE + 1))
					show_reader_page
				elif [ "${count}" -gt 0 ] && { [ "${HAVE_NEW}" -eq 1 ] || [ "${stamp}" != "${DISPLAYED_STAMP}" ]; }; then
					if [ "${count}" -gt "${PAGE}" ]; then
						PAGE=$((PAGE + 1))
					fi
					show_reader_page
				else
					fbink_msg "${PAGE}/${count}  end  | tap=sessions"
				fi
				;;
			"${KEY_PREV}")
				if [ "${PAGE}" -gt 1 ]; then
					PAGE=$((PAGE - 1))
					show_reader_page
				else
					back_to_library
				fi
				;;
		esac
	fi
done

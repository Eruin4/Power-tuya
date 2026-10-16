#!/bin/bash
POLL=${POLL_SECONDS:-5}
RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
MUTED_SINKS_FILE="$RUNTIME_DIR/monitor-power-muted-sinks"

asleep=0
was_online=0

# Both compatibility commands are clients of the local IoT API.
TUYA_CONTROL=${TUYA_CONTROL:-/home/eruin/project/power-tuya/bin/tuya-control}
LIGHT_CONTROL=${LIGHT_CONTROL:-/home/eruin/project/power-tuya/bin/light-control}
TUYA_ENABLED=${TUYA_ENABLED:-1}
TUYA_WAKE_DELAY=${TUYA_WAKE_DELAY:-7}
TUYA_LIGHT_NAME=${TUYA_LIGHT_NAME:-bedroom-light}
TUYA_CEILING_PYTHON=${TUYA_CEILING_PYTHON:-/home/eruin/project/power-tuya/.venv/bin/python}
TUYA_CEILING_SCRIPT=${TUYA_CEILING_SCRIPT:-/home/eruin/project/power-tuya/ceiling_prompt.py}
ceiling_checked_for_online=0
ceiling_prompt_pid=
connector_state=unknown
light_checked_for_online=0
light_on_pending=0
ceiling_upper_on_pending=0
TUYA_LIGHT_TIMEZONE=${TUYA_LIGHT_TIMEZONE:-Asia/Seoul}

set_monitor_connector_power() {
  local action=$1
  [ "$TUYA_ENABLED" = 1 ] || return 0
  [ "$connector_state" != "$action" ] || return 0
  if timeout 50 "$TUYA_CONTROL" monitor-plug "$action"; then
    connector_state=$action
    echo "$(date '+%F %T') monitor-plug $action confirmed"
    if [ "$action" = on ]; then sleep "$TUYA_WAKE_DELAY"; fi
  else
    connector_state=unknown
    echo "$(date '+%F %T') monitor-plug $action failed; retry next poll" >&2
    return 1
  fi
}

turn_off_light_for_online() {
  [ "$TUYA_ENABLED" = 1 ] || return 0
  [ "$light_checked_for_online" = 0 ] || return 0
  if timeout 50 "$LIGHT_CONTROL" off "$TUYA_LIGHT_NAME"; then
    light_checked_for_online=1
    echo "$(date '+%F %T') $TUYA_LIGHT_NAME off confirmed for main pc online"
  else
    echo "$(date '+%F %T') $TUYA_LIGHT_NAME off failed; retry next poll" >&2
    return 1
  fi
}

turn_on_light_for_shutdown() {
  [ "$TUYA_ENABLED" = 1 ] || return 0
  [ "$light_on_pending" = 1 ] || return 0
  if timeout 50 "$LIGHT_CONTROL" on "$TUYA_LIGHT_NAME"; then
    light_on_pending=0
    echo "$(date '+%F %T') $TUYA_LIGHT_NAME on confirmed for evening main pc shutdown"
  else
    echo "$(date '+%F %T') $TUYA_LIGHT_NAME on failed; retry next poll" >&2
    return 1
  fi
}

turn_on_upper_ceiling_for_shutdown() {
  [ "$TUYA_ENABLED" = 1 ] || return 0
  [ "$ceiling_upper_on_pending" = 1 ] || return 0
  if timeout 50 "$LIGHT_CONTROL" on ceiling-upper; then
    ceiling_upper_on_pending=0
    echo "$(date '+%F %T') ceiling switch_1 on confirmed for daytime main pc shutdown"
  else
    echo "$(date '+%F %T') ceiling switch_1 on failed; retry next poll" >&2
    return 1
  fi
}

stop_ceiling_prompt() {
  if [ -n "$ceiling_prompt_pid" ]; then
    kill "$ceiling_prompt_pid" 2>/dev/null || true
    wait "$ceiling_prompt_pid" 2>/dev/null || true
    ceiling_prompt_pid=
  fi
}

start_ceiling_prompt() {
  [ "$TUYA_ENABLED" = 1 ] || return 0
  [ "$ceiling_checked_for_online" = 0 ] || return 0
  "$TUYA_CEILING_PYTHON" "$TUYA_CEILING_SCRIPT" &
  ceiling_prompt_pid=$!
  ceiling_checked_for_online=1
}

trap 'stop_ceiling_prompt' EXIT
trap 'exit 0' TERM INT

read_online() {
  curl -fsS --max-time 5 http://127.0.0.1:8088/status.json 2>/dev/null | python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)["main_pc"]["online"]
    if type(value) is not bool:
        raise ValueError()
    print(int(value))
except (ValueError, KeyError, TypeError):
    sys.exit(1)
'
}

set_display_power() {
  local mode=$1
  gdbus call --session \
    --dest org.gnome.Mutter.DisplayConfig \
    --object-path /org/gnome/Mutter/DisplayConfig \
    --method org.freedesktop.DBus.Properties.Set \
    org.gnome.Mutter.DisplayConfig PowerSaveMode \
    "<int32 $mode>" >/dev/null 2>&1
}

set_active_fallback() {
  busctl --user call org.gnome.ScreenSaver /org/gnome/ScreenSaver \
    org.gnome.ScreenSaver SetActive b "$1" >/dev/null 2>&1
}

list_audio_sinks() {
  timeout 3 pw-dump 2>/dev/null | python3 -c '
import json
import sys

try:
    objects = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(0)

for item in objects:
    props = item.get("info", {}).get("props", {})
    if props.get("media.class") != "Audio/Sink":
        continue
    name = props.get("node.name")
    node_id = item.get("id")
    if name and isinstance(node_id, int):
        print(f"{node_id}\t{name}")
'
}

pause_media_players() {
  local player
  while read -r player _; do
    case "$player" in
      org.mpris.MediaPlayer2.*)
        timeout 2 gdbus call --session \
          --dest "$player" \
          --object-path /org/mpris/MediaPlayer2 \
          --method org.mpris.MediaPlayer2.Player.Pause \
          >/dev/null 2>&1 || true
        ;;
    esac
  done < <(timeout 3 busctl --user --no-pager --no-legend list 2>/dev/null)
}

mute_audio_sinks() {
  local sink_id sink_name
  mkdir -p "$RUNTIME_DIR"
  touch "$MUTED_SINKS_FILE"

  while IFS=$'\t' read -r sink_id sink_name; do
    [ -n "$sink_id" ] && [ -n "$sink_name" ] || continue
    if timeout 2 wpctl get-volume "$sink_id" 2>/dev/null | grep -qw MUTED; then
      continue
    fi
    if timeout 2 wpctl set-mute "$sink_id" 1 >/dev/null 2>&1; then
      grep -Fxq "$sink_name" "$MUTED_SINKS_FILE" 2>/dev/null || \
        printf '%s\n' "$sink_name" >>"$MUTED_SINKS_FILE"
    fi
  done < <(list_audio_sinks)
}

restore_audio_sinks() {
  local sink_id sink_name muted_name restored
  local remaining_file="${MUTED_SINKS_FILE}.remaining"
  [ -s "$MUTED_SINKS_FILE" ] || return 0
  : >"$remaining_file"

  while IFS= read -r muted_name; do
    restored=0
    while IFS=$'\t' read -r sink_id sink_name; do
      if [ "$sink_name" = "$muted_name" ]; then
        if timeout 2 wpctl set-mute "$sink_id" 0 >/dev/null 2>&1; then
          restored=1
        fi
        break
      fi
    done < <(list_audio_sinks)
    [ "$restored" -eq 1 ] || printf '%s\n' "$muted_name" >>"$remaining_file"
  done <"$MUTED_SINKS_FILE"
  if [ -s "$remaining_file" ]; then
    mv "$remaining_file" "$MUTED_SINKS_FILE"
  else
    rm -f "$MUTED_SINKS_FILE" "$remaining_file"
  fi
}

while true; do
  if ! online=$(read_online); then
    stop_ceiling_prompt
    echo "$(date '+%F %T') PC status unavailable; keeping current power" >&2
    sleep "$POLL"
    continue
  fi
  if [ "$online" = 1 ]; then
    light_on_pending=0
    ceiling_upper_on_pending=0
    set_monitor_connector_power on || true
    turn_off_light_for_online || true
    # PowerSaveMode applies to every output managed by Mutter, so DP-1 and
    # HDMI-1 always wake together. Repeat it every poll in case the service
    # restarts while the physical displays are already asleep.
    gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
    set_display_power 0 || set_active_fallback false
    restore_audio_sinks
    if [ "$asleep" = 1 ] || [ "$was_online" = 0 ]; then
      echo "$(date '+%F %T') main pc online -> all displays wake"
    fi
    start_ceiling_prompt
    asleep=0
    was_online=1
  else
    stop_ceiling_prompt
    ceiling_checked_for_online=0
    # Only an observed online -> offline transition schedules a light.
    # A PC already offline at service start must not turn on a light.
    if [ "$was_online" = 1 ]; then
      hour=$(TZ="$TUYA_LIGHT_TIMEZONE" date +%H)
      if [ "$hour" -ge 21 ] || [ "$hour" -lt 6 ]; then
        light_on_pending=1
      else
        ceiling_upper_on_pending=1
      fi
    fi
    turn_on_light_for_shutdown || true
    turn_on_upper_ceiling_for_shutdown || true
    was_online=0
    light_checked_for_online=0
    # status.json turns offline after three missed 5-second reports. Reassert
    # display power, paused media, and muted sinks every poll so user activity
    # cannot leave them active while the main PC remains offline.
    set_display_power 3 || set_active_fallback true
    pause_media_players
    mute_audio_sinks
    set_monitor_connector_power off || true
    if [ "$asleep" = 0 ]; then
      echo "$(date '+%F %T') main pc missed 3 reports -> all displays sleep"
    fi
    asleep=1
  fi
  sleep "$POLL"
done

#!/bin/bash
# ensure_audio.sh — Keep PipeWire on a real speaker instead of its Dummy Output.
#
# If PipeWire can't open the speaker when it probes the card (seen at boot:
# "playback open failed: Device or resource busy"), it falls back to Dummy
# Output and everything plays into nothing, with no errors in Kaizen's logs.
# See docs/experiments/2026-11-03-dummy-output-after-boot.md.
#
# Runs as kaizen.service's ExecStartPre: waits for a real sink, restarting
# WirePlumber if none appears. This has to run before Kaizen opens the mic:
# WirePlumber can't set the card up while Kaizen holds it.
#
# Always exits 0 so a sound problem never stops Kaizen from starting.

set -uo pipefail

SINK_WAIT_SECONDS=10
WIREPLUMBER_ATTEMPTS=3

has_real_sink() {
    local name
    name="$(wpctl inspect @DEFAULT_AUDIO_SINK@ 2>/dev/null | sed -n 's/.*node\.name = "\(.*\)"/\1/p' | head -1)"
    [ -n "$name" ] && [ "$name" != "auto_null" ]
}

wait_for_real_sink() {
    local deadline=$((SECONDS + $1))
    until has_real_sink; do
        [ "$SECONDS" -ge "$deadline" ] && return 1
        sleep 1
    done
}

# Nothing to do without PipeWire or a USB sound card (the HDMI cards don't count).
command -v wpctl >/dev/null || exit 0
grep -q 'USB-Audio' /proc/asound/cards 2>/dev/null || exit 0

wait_for_real_sink "$SINK_WAIT_SECONDS" && exit 0

for attempt in $(seq 1 "$WIREPLUMBER_ATTEMPTS"); do
    echo "ensure_audio: no real speaker, restarting WirePlumber (attempt $attempt/$WIREPLUMBER_ATTEMPTS)"
    systemctl --user restart wireplumber.service
    if wait_for_real_sink "$SINK_WAIT_SECONDS"; then
        echo "ensure_audio: speaker recovered"
        exit 0
    fi
done

echo "ensure_audio: still no real speaker, starting Kaizen anyway"
exit 0

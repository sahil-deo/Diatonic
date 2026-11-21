#!/usr/bin/env bash
# A throwaway Diatonic for testing: its own port, its own data folder, and a headless Chrome to drive it.
# Never touches the real server (port 5005) or the real data in ~/.diatonic.
#
#   scripts/dev_scratch.sh start     server on http://localhost:5071, Chrome DevTools on port 9333
#   scripts/dev_scratch.sh stop
#   scripts/dev_scratch.sh restart   needed after changing Python; JS and CSS are served fresh on every load
#
# Override with PORT, CDP_PORT, SCRATCH (data folder) and CHROME (browser binary).
set -u
cd "$(dirname "$0")/.."
PORT="${PORT:-5071}"; CDP_PORT="${CDP_PORT:-9333}"; SCRATCH="${SCRATCH:-/tmp/diatonic-scratch}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

stop() {
  pkill -f "diatonic run --port $PORT" 2>/dev/null
  pkill -f "remote-debugging-port=$CDP_PORT" 2>/dev/null
  sleep 1
}
start() {
  stop
  rm -rf "$SCRATCH"; mkdir -p "$SCRATCH/home" "$SCRATCH/chrome"
  DIATONIC_HOME="$SCRATCH/home" DIATONIC_SECRETS_BACKEND=file DIATONIC_NO_NATIVE=1 \
    nohup .venv/bin/diatonic run --port "$PORT" > "$SCRATCH/server.log" 2>&1 &
  nohup "$CHROME" --headless=new --no-sandbox --disable-gpu --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
    --remote-debugging-port="$CDP_PORT" --user-data-dir="$SCRATCH/chrome" --no-first-run \
    --window-size=1400,900 about:blank > "$SCRATCH/chrome.log" 2>&1 &
  for _ in $(seq 1 40); do
    curl -sf "http://localhost:$PORT/api/state" > /dev/null && curl -sf "http://localhost:$CDP_PORT/json/version" > /dev/null && { echo "ready: http://localhost:$PORT  (devtools $CDP_PORT, data in $SCRATCH)"; return 0; }
    sleep 0.5
  done
  echo "did not start; see $SCRATCH/server.log and $SCRATCH/chrome.log" >&2; return 1
}
case "${1:-}" in
  start|restart) start ;;
  stop) stop; echo stopped ;;
  *) echo "usage: $0 start|stop|restart" >&2; exit 2 ;;
esac

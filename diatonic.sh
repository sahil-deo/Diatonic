#!/usr/bin/env bash
# Start the Diatonic server (http://localhost:5005).
cd "$(dirname "$0")" && exec .venv/bin/diatonic "$@"

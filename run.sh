#!/usr/bin/env bash
# Simple foreground runner for a VPS / Linux machine.
set -euo pipefail

cd "$(dirname "$0")"
exec python3 -u main.py

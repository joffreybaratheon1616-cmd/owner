#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p accstore_data
exec python main.py

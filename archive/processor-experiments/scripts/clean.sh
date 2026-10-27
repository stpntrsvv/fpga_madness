#!/bin/sh
set -eu
for path in build reports .pio _build; do
    if [ -e "$path" ]; then find "$path" -mindepth 1 -delete; rmdir "$path" 2>/dev/null || true; fi
done

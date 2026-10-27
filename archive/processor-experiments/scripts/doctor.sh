#!/bin/sh
set -eu
printf 'host: '; uname -srm
printf 'board part: xc7z020clg400-1\n'
for tool in git make openocd python3 ssh rsync; do
    if command -v "$tool" >/dev/null 2>&1; then printf '%-10s %s\n' "$tool" "$(command -v "$tool")"; else printf '%-10s missing\n' "$tool"; fi
done
if [ -x .venv/bin/python ]; then
    .venv/bin/python -m apio --version
else
    echo 'Не найден Python виртуальной среды' >&2
    exit 2
fi
if [ -n "${VIVADO_HOST:-}" ]; then
    printf 'vivado     remote via %s\n' "$VIVADO_HOST"
elif command -v "${VIVADO_BIN:-vivado}" >/dev/null 2>&1; then
    printf 'vivado     %s\n' "$(command -v "${VIVADO_BIN:-vivado}")"
else
    printf 'vivado     unconfigured (set VIVADO_HOST or VIVADO_BIN in .env)\n'
fi

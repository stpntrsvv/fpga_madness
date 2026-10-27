#!/bin/sh
set -eu
[ "$#" -eq 1 ] || { echo "usage: $0 scripts/file.tcl" >&2; exit 2; }
script=$1
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${VIVADO_HOST:-}" ]; then exec "$root/scripts/remote_vivado.sh" "$script"; fi
vivado=${VIVADO_BIN:-vivado}
command -v "$vivado" >/dev/null 2>&1 || { echo "Set VIVADO_BIN or VIVADO_HOST in .env" >&2; exit 2; }
exec "$vivado" -mode batch -nojournal -nolog -source "$root/$script"


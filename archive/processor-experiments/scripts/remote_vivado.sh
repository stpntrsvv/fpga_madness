#!/bin/sh
set -eu
script=$1
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${VIVADO_HOST:?set VIVADO_HOST in .env}"
remote_root=${VIVADO_REMOTE_ROOT:-~/fpga-madness}
remote_bin=${VIVADO_REMOTE_BIN:-vivado}
rsync -az --delete --exclude .git --exclude .venv --exclude .pio --exclude build --exclude reports "$root/" "$VIVADO_HOST:$remote_root/"
ssh "$VIVADO_HOST" "cd $remote_root && $remote_bin -mode batch -nojournal -nolog -source $script"
mkdir -p "$root/build" "$root/reports"
rsync -az "$VIVADO_HOST:$remote_root/build/" "$root/build/"
rsync -az "$VIVADO_HOST:$remote_root/reports/" "$root/reports/"


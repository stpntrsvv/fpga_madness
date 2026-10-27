#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec openocd -f interface/cmsis-dap.cfg -f "$root/scripts/jtag_chain.tcl" \
    -c 'init; scan_chain' -f "$root/scripts/jtag_sanity.tcl" -c shutdown

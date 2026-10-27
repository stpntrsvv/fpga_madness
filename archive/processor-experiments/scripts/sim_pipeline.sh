#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/dpu-pipeline.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM
tools_dir=${OSS_CAD_BIN:-$HOME/.apio/packages/oss-cad-suite/bin}
PATH=$tools_dir:$PATH
export PATH
case "${1:-}" in
    '') set -- rtl/dpu_link.sv rtl/dpu_product_pipe.sv ;;
    --после-синтеза)
        yosys -Q -q -p "read_json ${2:-_build/default/hardware.json}; write_verilog -noattr \"$tmp_dir/netlist.v\""
        set -- "$tmp_dir/netlist.v" ;;
    *) echo 'Неизвестный режим проверки конвейера' >&2; exit 2 ;;
esac
set -- "$@" tb/pipeline_tb.sv "$tools_dir/../share/yosys/xilinx/cells_sim.v" \
    "$tools_dir/../share/yosys/xilinx/cells_xtra.v"
if command -v iverilog >/dev/null 2>&1; then
    iverilog -g2012 -s pipeline_tb -o "$tmp_dir/test" "$@"
    vvp "$tmp_dir/test" >"$tmp_dir/result.log" 2>&1 || { cat "$tmp_dir/result.log"; exit 1; }
else
    verilator --binary --timing -Wno-fatal --top-module pipeline_tb -Mdir "$tmp_dir/obj" "$@"
    "$tmp_dir/obj/Vpipeline_tb" >"$tmp_dir/result.log" 2>&1 || { cat "$tmp_dir/result.log"; exit 1; }
fi
cat "$tmp_dir/result.log"
grep -q '^УСПЕХ:' "$tmp_dir/result.log"

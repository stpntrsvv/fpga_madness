#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
task_tmp=$(mktemp -d "${TMPDIR:-/tmp}/grid-sim.XXXXXX")
trap 'rm -rf "$task_tmp"' EXIT HUP INT TERM
tools_dir=${OSS_CAD_BIN:-$HOME/.apio/packages/oss-cad-suite/bin}
PATH=$tools_dir:$PATH
export PATH
if [ "${GRID_PROGRAMMABLE:-0}" = 1 ]; then
    .venv/bin/python scripts/field_vectors.py "$task_tmp/vectors.txt"
    core_bench=field_tb
    default_profile=field
    test_define=-DFIELD_PROGRAMMABLE
else
    .venv/bin/python scripts/grid_vectors.py "$task_tmp/vectors.txt" "${GRID_LANES:-4}"
    core_bench=grid_tb
    default_profile=grid-safe
    test_define=
    if [ "${GRID_LANES:-4}" = 8 ]; then
        test_define=-DGRID_WIDE
        default_profile=grid-fclk8
    fi
fi
cells="$tools_dir/../share/yosys/xilinx/cells_sim.v"
profile=${2:-$default_profile}
case "${1:-}" in
    '') set -- rtl/*.sv ;;
    --после-синтеза)
        .venv/bin/python - "$profile" <<'PY'
from pathlib import Path
import sys
p=Path('_build')/sys.argv[1]/'hardware.json'
if not p.exists() or any(f.stat().st_mtime_ns>p.stat().st_mtime_ns for f in Path('rtl').glob('*.sv')):
    raise SystemExit('Для проверки нужна актуальная схема профиля grid после синтеза')
PY
        yosys -Q -q -p "read_json _build/$profile/hardware.json; write_verilog -noattr \"$task_tmp/netlist.v\""
        set -- -DGRID_NETLIST "$task_tmp/netlist.v" ;;
    *) echo 'Неизвестный режим проверки сетки' >&2; exit 2 ;;
esac
if [ "${1:-}" = -DGRID_NETLIST ]; then
    .venv/bin/python scripts/grid_bram_sim.py "_build/$profile/hardware.json" "$cells" "$task_tmp/cells.v"
    cells="$task_tmp/cells.v"
fi
for bench in grid_link_tb "$core_bench"; do
if command -v verilator >/dev/null 2>&1; then
verilator --binary --timing -j 4 -Wno-fatal --top-module "$bench" --Mdir "$task_tmp/obj-$bench" \
    $test_define "$@" "tb/$bench.sv" "$cells" \
    "$tools_dir/../share/yosys/xilinx/cells_xtra.v" >"$task_tmp/compile.log" 2>&1 || {
    cat "$task_tmp/compile.log"; exit 1;
}
"$task_tmp/obj-$bench/V$bench" "+VECTORS=$task_tmp/vectors.txt" >"$task_tmp/result.log" 2>&1 || {
    cat "$task_tmp/result.log"; exit 1;
}
else
iverilog -g2012 -s "$bench" -o "$task_tmp/sim" $test_define "$@" "tb/$bench.sv" \
    "$cells" "$tools_dir/../share/yosys/xilinx/cells_xtra.v" 2>"$task_tmp/compile.log" || {
    cat "$task_tmp/compile.log"; exit 1;
}
vvp "$task_tmp/sim" "+VECTORS=$task_tmp/vectors.txt" >"$task_tmp/result.log" 2>&1 || {
    cat "$task_tmp/result.log"; exit 1;
}
fi
cat "$task_tmp/result.log"
grep -q '^УСПЕХ:' "$task_tmp/result.log"
done

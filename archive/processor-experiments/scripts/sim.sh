#!/bin/sh
set -eu
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/fpga-madness-sim.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
sh scripts/sim_pipeline.sh "${1:-}"
tools_dir=${OSS_CAD_BIN:-$HOME/.apio/packages/oss-cad-suite/bin}
PATH=$tools_dir:$PATH
export PATH
.venv/bin/python scripts/dpu.py проверить --векторы "$tmp_dir/vectors.txt"
case "${1:-}" in
    '') set -- rtl/*.sv "$tools_dir/../share/yosys/xilinx/cells_sim.v" \
        "$tools_dir/../share/yosys/xilinx/cells_xtra.v" ;;
    --после-синтеза)
        .venv/bin/python - <<'PY'
from pathlib import Path
p = Path('_build/default/hardware.json')
if not p.exists() or any(f.stat().st_mtime_ns > p.stat().st_mtime_ns for f in Path('rtl').glob('*.sv')):
    raise SystemExit('Сначала выполните make synth: нужна актуальная схема после синтеза')
PY
        yosys -Q -q -p "read_json _build/default/hardware.json; write_verilog -noattr \"$tmp_dir/netlist.v\""
        set -- "$tmp_dir/netlist.v" "$tools_dir/../share/yosys/xilinx/cells_sim.v" \
            "$tools_dir/../share/yosys/xilinx/cells_xtra.v"
        ;;
    *) echo 'Неизвестный режим моделирования' >&2; exit 2 ;;
esac
for bench in integrator_tb top_tb; do
if command -v iverilog >/dev/null 2>&1; then
    iverilog -g2012 -s "$bench" -o "$tmp_dir/$bench" "$@" "tb/$bench.sv"
    vvp "$tmp_dir/$bench" "+VECTORS=$tmp_dir/vectors.txt" >"$tmp_dir/result.log" 2>&1 || {
        cat "$tmp_dir/result.log"; exit 1;
    }
elif command -v verilator >/dev/null 2>&1; then
    verilator --binary --timing --top-module "$bench" -Mdir "$tmp_dir/obj-$bench" "$@" "tb/$bench.sv"
    "$tmp_dir/obj-$bench/V$bench" "+VECTORS=$tmp_dir/vectors.txt" >"$tmp_dir/result.log" 2>&1 || {
        cat "$tmp_dir/result.log"; exit 1;
    }
else
    echo "Не найдены Icarus Verilog и Verilator" >&2; exit 2
fi
cat "$tmp_dir/result.log"
grep -q '^УСПЕХ:' "$tmp_dir/result.log" || {
    echo 'Моделирование завершилось без подтверждения всех проверок' >&2; exit 1;
}
done


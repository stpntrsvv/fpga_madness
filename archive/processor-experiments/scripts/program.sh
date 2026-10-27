#!/bin/sh
set -eu
[ "${ALLOW_FPGA_PROGRAM:-}" = YES ] || { echo "ALLOW_FPGA_PROGRAM=YES is required" >&2; exit 2; }
[ "$#" -eq 1 ] || { echo "usage: $0 path/to/design.bit" >&2; exit 2; }
bitstream=$1
[ -f "$bitstream" ] || { echo "bitstream not found: $bitstream" >&2; exit 2; }
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# DONE может принадлежать прежней конфигурации при обрыве TDI.
"$root/scripts/probe.sh"
# На текущем соединении большой образ успешно загружен при 1 МГц.
frequency=${FPGA_PROGRAM_HZ:-1000000}
case "$frequency" in ''|*[!0-9]*) echo 'FPGA_PROGRAM_HZ должна быть целой частотой в Гц' >&2; exit 2;; esac
[ "$frequency" -gt 0 ] || { echo 'Частота программирования должна быть положительной' >&2; exit 2; }
case "${FPGA_PROGRAMMER:-openFPGALoader}" in
openFPGALoader)
loader=${OPENFPGALOADER:-$HOME/.apio/packages/oss-cad-suite/bin/openFPGALoader}
[ -x "$loader" ] || { echo "openFPGALoader не найден: $loader" >&2; exit 2; }
"$loader" \
    -c cmsisdap \
    --freq "$frequency" \
    --index-chain 1 \
    --write-sram \
    "$bitstream"
;;
openocd)
[ "$((frequency % 1000))" -eq 0 ] || { echo 'Для OpenOCD частота должна быть кратна 1000 Гц' >&2; exit 2; }
# Штатный драйвер Zynq-7000 в OpenOCD 0.12: без JSTART, только TAP PL.
OPENOCD_BITSTREAM="$bitstream" openocd -f interface/cmsis-dap.cfg \
    -f "$root/scripts/jtag_chain.tcl" -c "adapter speed $((frequency / 1000))" \
    -c 'pld device virtex2 zynq_pl.bs 1' -c init -f "$root/scripts/jtag_sanity.tcl" \
    -c 'pld load 0 $::env(OPENOCD_BITSTREAM)' -c shutdown
;;
*) echo 'FPGA_PROGRAMMER: ожидается openFPGALoader или openocd' >&2; exit 2;;
esac
# Нулевой код загрузчика не гарантирует завершения конфигурации.
"$root/.venv/bin/python" "$root/scripts/read_config.py" --проверить-запуск

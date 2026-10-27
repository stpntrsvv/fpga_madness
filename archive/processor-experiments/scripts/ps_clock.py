#!/usr/bin/env python3
"""Прочитать тактирование PS через MEM-AP без остановки ARM и записи памяти."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
REGISTERS = {
    'ARM_PLL_CTRL': 0xf8000100, 'DDR_PLL_CTRL': 0xf8000104,
    'IO_PLL_CTRL': 0xf8000108, 'PLL_STATUS': 0xf800010c,
    'FPGA0_CLK_CTRL': 0xf8000170, 'FPGA0_THR_CTRL': 0xf8000174,
    'FPGA0_THR_CNT': 0xf8000178, 'FPGA0_THR_STA': 0xf800017c,
    'FPGA_RST_CTRL': 0xf8000240, 'LVL_SHFTR_EN': 0xf8000900,
    'SLCR_LOCKSTA': 0xf800000c,
}


def decode(values, reference_hz=33_333_333):
    control = values['FPGA0_CLK_CTRL']
    selector = (control >> 4) & 3
    source = ('IO', 'IO', 'ARM', 'DDR')[selector]
    pll = values[source + '_PLL_CTRL']
    lock_bit = {'ARM': 0, 'DDR': 1, 'IO': 2}[source]
    divisor0, divisor1 = (control >> 8) & 63, (control >> 20) & 63
    if not divisor0 or not divisor1:
        raise ValueError('Нулевой делитель требует отдельного исследования режима')
    if pll & 3 or pll & 0x18 or not values['PLL_STATUS'] & (1 << lock_bit):
        raise ValueError('PLL сброшен, выключен, обойдён либо не захвачен')
    multiplier = (pll >> 12) & 127
    if not multiplier:
        raise ValueError('Нулевой множитель PLL')
    return dict(source=source, multiplier=multiplier, divisor0=divisor0, divisor1=divisor1,
                reference_nominal_hz=reference_hz,
                nominal_hz=reference_hz * multiplier / divisor0 / divisor1)


def read():
    lines = ['dap create zynq.dap -chain-position zynq.cpu',
             'target create zynq.mem mem_ap -dap zynq.dap -ap-num 0',
             'init', f'source {{{ROOT / "scripts/jtag_sanity.tcl"}}}']
    for name, address in REGISTERS.items():
        lines.append(f'echo [format "PS_CLOCK {name} %08x" '
                     f'[lindex [zynq.mem read_memory 0x{address:08x} 32 1] 0]]')
    lines.append('shutdown')
    with tempfile.TemporaryDirectory(prefix='ps-clocks-') as task_tmp:
        path = Path(task_tmp) / 'read.tcl'
        path.write_text('\n'.join(lines) + '\n')
        result = subprocess.run(['openocd', '-f', 'interface/cmsis-dap.cfg', '-f',
                                 str(ROOT / 'scripts/jtag_chain.tcl'), '-f', str(path)],
                                capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    reports = ROOT / 'reports'
    reports.mkdir(exist_ok=True)
    (reports / 'такты-PS-чтение.log').write_text(output)
    if result.returncode:
        raise RuntimeError(output[-5000:])
    values = {name: int(value, 16) for name, value in
              re.findall(r'^PS_CLOCK (\w+) ([0-9a-fA-F]+)$', output, re.M)}
    if set(values) != set(REGISTERS):
        raise ValueError('Не прочитан полный набор регистров тактирования')
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--проверить-образ', type=Path)
    args = parser.parse_args()
    values = read()
    clock = decode(values)
    report = dict(date=datetime.now(timezone.utc).isoformat(),
                  method='MEM-AP, только чтение; Cortex-A9 не останавливаются',
                  registers=values, fclk0=clock)
    (ROOT / 'reports/такты-PS.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.проверить_образ:
        metadata = json.loads(Path(str(args.проверить_образ) + '.meta.json').read_text())
        if metadata['profile'] not in ('grid-fclk', 'grid-fclk8'):
            raise ValueError('Проверка предназначена для профилей grid-fclk и grid-fclk8')
        clocks = [t for t in metadata['timing'].values() if t['constraint'] > 1]
        if len(clocks) != 1 or abs(clock['nominal_hz'] - clocks[0]['constraint'] * 1e6) > 100:
            raise ValueError('Делители FCLK0 не соответствуют проверенной частоте образа')
        if values['LVL_SHFTR_EN'] & 0xf != 0xf:
            raise ValueError('Не включены необходимые преобразователи уровня PS/PL')
    print(f'FCLK0: {clock["source"]} PLL ×{clock["multiplier"]} / '
          f'{clock["divisor0"]} / {clock["divisor1"]}, номинально '
          f'{clock["nominal_hz"]/1e6:.6f} МГц; записи не выполнялись')


if __name__ == '__main__':
    main()

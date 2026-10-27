#!/usr/bin/env python3
"""Подготовить или явно разрешить снятие удержания PL через devcfg.CTRL[30]."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

import ps_clock

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = 0x0e00e07f
RELEASED = EXPECTED | 0x40000000


def script():
    # Только один регистр PS записывается, без импульса сброса и без настройки PLL.
    return '\n'.join([
        'dap create zynq.dap -chain-position zynq.cpu',
        'target create zynq.mem mem_ap -dap zynq.dap -ap-num 0',
        'init', f'source {{{ROOT / "scripts/jtag_sanity.tcl"}}}',
        'set control [lindex [zynq.mem read_memory 0xf8007000 32 1] 0]',
        f'if {{$control != 0x{EXPECTED:08x}}} {{error "Состояние DEVCFG изменилось; запись отменена"}}',
        'set power [lindex [zynq.mem read_memory 0xf8007080 32 1] 0]',
        'if {($power & 0x100) == 0} {error "Нет подтверждения питания PL"}',
        f'zynq.mem write_memory 0xf8007000 32 {{0x{RELEASED:08x}}}',
        'set control [lindex [zynq.mem read_memory 0xf8007000 32 1] 0]',
        f'if {{$control != 0x{RELEASED:08x}}} {{error "Не подтверждена единственная запись DEVCFG"}}',
        'set deadline [expr {[ms]+2000}]',
        'while {1} {',
        '  set status [lindex [zynq.mem read_memory 0xf8007014 32 1] 0]',
        '  if {$status & 0x10} {break}',
        '  if {[ms] > $deadline} {error "INIT_B не поднялся после снятия удержания PL"}',
        '  sleep 10',
        '}',
        'echo [format "PL_RELEASE CTRL=%08x STATUS=%08x" $control $status]',
        'shutdown', ''])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--выполнить', action='store_true')
    mode.add_argument('--проверить-готовность', action='store_true')
    args = parser.parse_args()
    if args.проверить_готовность:
        ps_clock.REGISTERS = dict(DEVCFG_CTRL=0xf8007000, DEVCFG_STATUS=0xf8007014, DEVCFG_MCTRL=0xf8007080)
        values = ps_clock.read()
        if not values['DEVCFG_MCTRL'] & 0x100:
            raise SystemExit('Не подтверждено питание PL; загрузка отменена')
        if not values['DEVCFG_CTRL'] & 0x40000000:
            raise SystemExit('PCFG_PROG_B=0: PS удерживает PL в сбросе; загрузка отменена без записи PS')
        if not values['DEVCFG_STATUS'] & 0x10:
            raise SystemExit('INIT_B=0: PL не готова принять конфигурацию; загрузка отменена')
        print('Питание PL, снятие удержания сброса и INIT_B подтверждены чтением')
        return
    if not args.выполнить:
        print(f'План: одна запись 0xf8007000: 0x{EXPECTED:08x} → 0x{RELEASED:08x}; только бит PCFG_PROG_B.')
        print(script())
        return
    if os.environ.get('ALLOW_PS_PL_RELEASE') != 'YES':
        raise SystemExit('Нужно отдельное разрешение пользователя и ALLOW_PS_PL_RELEASE=YES')
    with tempfile.TemporaryDirectory(prefix='pl-release-') as directory:
        path = Path(directory)/'release.tcl'
        path.write_text(script())
        result = subprocess.run(['openocd', '-f', 'interface/cmsis-dap.cfg', '-f',
                                 str(ROOT/'scripts/jtag_chain.tcl'), '-f', str(path)],
                                capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    (ROOT/'reports/снятие-удержания-PL.log').write_text(output)
    if result.returncode or 'PL_RELEASE CTRL=' not in output:
        raise RuntimeError(output[-5000:])
    print(output)


if __name__ == '__main__':
    main()

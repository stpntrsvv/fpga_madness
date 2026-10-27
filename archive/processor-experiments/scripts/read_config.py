#!/usr/bin/env python3
"""Чтение регистров конфигурации PL через CFG_IN/CFG_OUT, AMD UG470 §6."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
REGISTERS = dict(STAT=7, COR0=9, COR1=14, CMD=4, IDCODE=12, BOOTSTS=22)


def reverse(value):
    return int(f'{value:032b}'[::-1], 2)


def read(names=('STAT',)):
    lines = ['init', f'source {{{ROOT / "scripts/jtag_sanity.tcl"}}}']
    for name in names:
        words = [0xaa995566, 0x20000000, 0x28000001 | (REGISTERS[name] << 13), 0x20000000, 0x20000000]
        fields = ' '.join(f'32 0x{reverse(w):08x}' for w in words)
        lines += ['irscan zynq_pl.bs 0x05', f'drscan zynq_pl.bs {fields}',
                  'irscan zynq_pl.bs 0x04', 'set value [drscan zynq_pl.bs 32 0]',
                  f'echo "CONFIG {name} $value"', 'irscan zynq_pl.bs 0x3f', 'runtest 32']
    # DESYNC завершает сеанс конфигурационного интерфейса (UG470).
    words = [0xaa995566, 0x20000000, 0x30008001, 13, 0x20000000, 0x20000000]
    fields = ' '.join(f'32 0x{reverse(w):08x}' for w in words)
    lines += ['irscan zynq_pl.bs 0x05', f'drscan zynq_pl.bs {fields}',
              'irscan zynq_pl.bs 0x3f', 'runtest 32', 'shutdown']
    with tempfile.TemporaryDirectory(prefix='grid-status-') as directory:
        path = Path(directory)/'read.tcl'
        path.write_text('\n'.join(lines)+'\n')
        result = subprocess.run(['openocd','-f','interface/cmsis-dap.cfg','-f',str(ROOT/'scripts/jtag_chain.tcl'),
                                 '-f',str(path)],capture_output=True,text=True)
    output = result.stdout+result.stderr
    if result.returncode:
        raise RuntimeError(output)
    values = {name:reverse(int(value,16)) for name,value in re.findall(r'^CONFIG (\w+) ([0-9a-fA-F]+)$',output,re.M)}
    if set(values) != set(names):
        raise RuntimeError(output)
    return values, output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--регистр', choices=REGISTERS, action='append')
    parser.add_argument('--проверить-запуск', action='store_true',
                        help='Требовать DONE, EOS, GWE, GTS и отсутствие ошибок конфигурации')
    args=parser.parse_args()
    names = args.регистр or ['STAT']
    if args.проверить_запуск and 'STAT' not in names:
        names = ['STAT', *names]
    values,output=read(names)
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/конфигурационные-регистры.log').write_text(output)
    print(json.dumps({k:f'0x{v:08x}' for k,v in values.items()},ensure_ascii=False))
    if args.проверить_запуск:
        status = values.get('STAT')
        required = (1 << 14) | (1 << 13) | (1 << 12) | (1 << 11) | 0xf0
        errors = (1 << 17) | (1 << 16) | (1 << 15) | 1
        if status is None or status & required != required or status & errors:
            raise SystemExit('Конфигурация PL не завершила запуск либо сообщила ошибку')

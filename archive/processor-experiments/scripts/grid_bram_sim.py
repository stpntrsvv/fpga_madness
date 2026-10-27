#!/usr/bin/env python3
"""Дополнить пустые модели BRAM Yosys только проверенными режимами опыта."""
import json
from pathlib import Path
import re
import sys

netlist, library, output = map(Path, sys.argv[1:])
counts = {'RAMB36E1': 0, 'RAMB18E1': 0}
for module in json.loads(netlist.read_text())['modules'].values():
    for cell in module.get('cells', {}).values():
        kind = cell['type']
        if kind not in counts:
            continue
        p = cell['parameters']
        required = dict(DOA_REG=0, DOB_REG=0, READ_WIDTH_A=36,
                        READ_WIDTH_B=36 if kind == 'RAMB36E1' else 0,
                        WRITE_WIDTH_A=36 if kind == 'RAMB36E1' else 0,
                        WRITE_WIDTH_B=0 if kind == 'RAMB36E1' else 36)
        strings = dict(RAM_MODE='TDP' if kind == 'RAMB36E1' else 'SDP',
                       WRITE_MODE_A='READ_FIRST', WRITE_MODE_B='READ_FIRST')
        if any(int(p[k], 2) != v for k, v in required.items()) or any(p[k] != v for k,v in strings.items()):
            raise SystemExit('Неподдерживаемый режим BRAM: нужна отдельная модель')
        if any('1' in v for k,v in p.items() if k.startswith(('INIT_', 'INITP_', 'IS_'))):
            raise SystemExit('Инициализация единицами или инверсия BRAM не поддержана моделью')
        counts[kind] += 1
# Одна схема памяти в иерархии: один TDP либо две копии SDP с общими записями.
if counts not in ({'RAMB36E1': 1, 'RAMB18E1': 0}, {'RAMB36E1': 0, 'RAMB18E1': 2}):
    raise SystemExit(f'Неожиданный набор описаний BRAM: {counts}')
source = library.read_text()
for kind, body_name in (('RAMB36E1', 'grid_bram_body.vh'), ('RAMB18E1', 'grid_bram18_body.vh')):
    if not counts[kind]:
        continue
    body = (Path(__file__).resolve().parent.parent / 'tb' / body_name).read_text()
    pattern = rf'(module {kind}\b.*?)(\n\s*specify\b.*?\n\s*endspecify)(\nendmodule)'
    source, replaced = re.subn(pattern, lambda m: m[1] + '\n' + body + m[3], source, flags=re.S)
    if replaced != 1:
        raise SystemExit(f'Не найдена ожидаемая пустая модель Yosys {kind}')
output.write_text(source)
print(f'BRAM: проверены режимы {counts}, READ_FIRST, без выходных регистров; подключены ограниченные модели')

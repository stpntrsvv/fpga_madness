#!/usr/bin/env python3
"""Отклонить прошивку с пропущенными маршрутами данных DSP после nextpnr."""
import json
from pathlib import Path
import re
import sys


def check(netlist):
    modules = netlist['modules']
    tops = [m for m in modules.values() if int(m.get('attributes', {}).get('top', '0'), 2)]
    if len(tops) != 1:
        raise ValueError('Не найден единственный верхний модуль трассированной схемы')
    top = tops[0]
    routes = {}
    for net in top['netnames'].values():
        if len(net['bits']) == 1 and 'ROUTING' in net.get('attributes', {}):
            routes.setdefault(net['bits'][0], set()).update(re.findall(
                r'SITEWIRE/([^/;]+)/([A-Za-z0-9_]+)', net['attributes']['ROUTING']))
    count = pins = 0
    missing = []
    for name, cell in top['cells'].items():
        if cell['type'] != 'DSP48E1_DSP48E1':
            continue
        count += 1
        site = cell['attributes']['NEXTPNR_BEL'].split('/')[0]
        # Умножитель использует A[24:0] и B[17:0]; A[29:25] в него не входят.
        for port in [*(f'A{i}' for i in range(25)), *(f'B{i}' for i in range(18))]:
            bits = cell['connections'].get(port, [])
            if len(bits) != 1 or (site, port) not in routes.get(bits[0], set()):
                missing.append(f'{name}: {site}/{port}')
            pins += 1
    if not count:
        raise ValueError('В трассированной схеме нет DSP ожидаемого типа')
    if missing:
        raise ValueError('Отсутствуют маршруты данных DSP:\n' + '\n'.join(missing))
    return count, pins


if __name__ == '__main__':
    try:
        count, pins = check(json.loads(Path(sys.argv[1]).read_text()))
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(f'Проверены маршруты данных: {count} DSP, {pins} входов')

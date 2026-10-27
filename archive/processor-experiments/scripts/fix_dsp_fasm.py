#!/usr/bin/env python3
"""Проверяемый обход ошибок констант DSP и AREG/BREG в nextpnr 68aeeb3.

Основание: https://github.com/openXC7/nextpnr-xilinx/pull/159
Применяется только к явно заданному зарегистрированному умножителю этого проекта.
Исходный FASM сохраняется; новые признаки проверяются по установленной базе prjxray.
"""
import argparse
import json
from pathlib import Path
import re


def corrected(netlist, original, database):
    modules = netlist['modules']
    top = [name for name, module in modules.items()
           if int(module.get('attributes', {}).get('top', '0'), 2)]
    if len(top) != 1:
        raise ValueError('Не найден единственный верхний модуль')

    def integer(cell, name, default=0):
        value = cell.get('parameters', {}).get(name)
        return default if value is None else int(value, 2)

    def count(module):
        total = 0
        for cell in modules[module].get('cells', {}).values():
            if cell['type'] == 'DSP48E1':
                expected = {'AREG': 1, 'ACASCREG': 1, 'BREG': 1, 'BCASCREG': 1, 'MREG': 1, 'PREG': 0}
                if any(integer(cell, name) != value for name, value in expected.items()):
                    raise ValueError('Неизвестный профиль регистров DSP: обход к нему неприменим')
                if any(cell['parameters'].get(name) != 'DIRECT' for name in ('A_INPUT', 'B_INPUT')):
                    raise ValueError('Каскадный вход DSP не поддерживается этим обходом')
                for name in ('IS_INMODE_INVERTED', 'IS_ALUMODE_INVERTED', 'IS_OPMODE_INVERTED'):
                    if integer(cell, name):
                        raise ValueError('Инверсия управления DSP требует отдельной реализации')
                connections = cell['connections']
                for port, bits in (('INMODE', range(5)), ('ALUMODE', (2, 3)), ('OPMODE', (6,))):
                    if any(connections[port][bit] != '0' for bit in bits):
                        raise ValueError('Ожидался постоянный нуль на управляющих входах DSP')
                total += 1
            elif cell['type'] in modules:
                total += count(cell['type'])
        return total

    expected_count = count(top[0])
    sites = sorted(set(re.findall(r'^(DSP_[LR]_X\d+Y\d+)\.DSP48\.(DSP_[01])\.MASK', original, re.MULTILINE)))
    if len(sites) != expected_count:
        raise ValueError(f'Число DSP в FASM {len(sites)} не совпадает со схемой {expected_count}')
    features = {line.split('=')[0].strip().removesuffix('[0]') for line in original.splitlines()}
    additions = []
    for tile, dsp in sites:
        side = tile[4]
        tile_type = f'DSP_{side}'
        available = {line.split()[0] for line in (database / f'segbits_dsp_{side.lower()}.db').read_text().splitlines()}
        required = [f'{dsp}_{pin}.DSP_GND_{side}' for pin in (
            'INMODE0', 'INMODE1', 'INMODE2', 'INMODE3', 'INMODE4', 'ALUMODE2', 'ALUMODE3', 'OPMODE6')]
        required += [f'DSP48.{dsp}.ZAREG_2_ACASCREG_1', f'DSP48.{dsp}.ZBREG_2_BCASCREG_1']
        for feature in required:
            if f'{tile_type}.{feature}' not in available:
                raise ValueError(f'Признак отсутствует в базе: {tile_type}.{feature}')
            if 'DSP_GND' in feature and f'{tile}.{feature.replace("DSP_GND", "DSP_VCC")}' in features:
                raise ValueError(f'Противоречивое управление DSP: {feature}')
            full_name = f'{tile}.{feature}'
            if full_name not in features:
                additions.append(full_name)
    return original.rstrip() + '\n' + ('\n'.join(additions) + '\n' if additions else ''), expected_count, len(additions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('схема', type=Path)
    parser.add_argument('вход', type=Path)
    parser.add_argument('база', type=Path)
    parser.add_argument('выход', type=Path)
    args = parser.parse_args()
    result, count, added = corrected(json.loads(args.схема.read_text()), args.вход.read_text(), args.база)
    args.выход.write_text(result)
    print(f'Проверено DSP: {count}; добавлено документированных признаков: {added}')


if __name__ == '__main__':
    main()

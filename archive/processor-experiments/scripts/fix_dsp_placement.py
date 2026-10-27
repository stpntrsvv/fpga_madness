#!/usr/bin/env python3
"""Снять привязку независимых DSP к нижней половине плитки nextpnr 68aeeb3.

В XC7Z020 есть 110 мест с Z=6 и 110 с Z=25. Упаковщик задаёт Z=6
также одиночным DSP. Каскадные связи и неизвестные режимы не исправляются.
Исходная упакованная схема сохраняется отдельно.
"""
import argparse
import json
from pathlib import Path


def corrected(design, expected=128):
    cells=[c for m in design['modules'].values() for c in m.get('cells',{}).values()
           if c['type']=='DSP48E1_DSP48E1']
    if len(cells)!=expected:
        raise ValueError(f'Ожидалось {expected} независимых DSP, получено {len(cells)}')
    # Сначала проверяется весь набор; при отказе ничего не меняется.
    for cell in cells:
        attrs=cell['attributes']
        constraints={k:v for k,v in attrs.items() if k.startswith('CONSTR')}
        if constraints!={'CONSTR_ABS_Z':f'{1:032b}','CONSTR_Z':f'{6:032b}'}:
            raise ValueError('Неизвестные ограничения DSP; автоматическое снятие запрещено')
        if 'BEL' in attrs or 'LOC' in attrs or attrs.get('X_ORIG_TYPE')!='DSP48E1':
            raise ValueError('DSP уже размещён или имеет неизвестный исходный тип')
        params=cell['parameters']
        expected_regs={'AREG':1,'ACASCREG':1,'BREG':1,'BCASCREG':1,'MREG':1,'PREG':0}
        if any(int(params.get(k,'-1'),2)!=v for k,v in expected_regs.items()):
            raise ValueError('Неизвестный профиль регистров DSP')
        if any(params.get(k)!='DIRECT' for k in ('A_INPUT','B_INPUT')):
            raise ValueError('Каскадный режим DSP не поддерживается')
        cascades=('ACIN','BCIN','PCIN','ACOUT','BCOUT','PCOUT','CARRYCASC','MULTSIGN')
        if any(bits for name,bits in cell['connections'].items() if name.startswith(cascades)):
            raise ValueError('Есть каскадное соединение DSP; его ограничения сохраняются')
    for cell in cells:
        del cell['attributes']['CONSTR_ABS_Z']
        del cell['attributes']['CONSTR_Z']
    return len(cells)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('вход',type=Path)
    parser.add_argument('выход',type=Path)
    args=parser.parse_args()
    if args.вход.resolve()==args.выход.resolve():
        raise SystemExit('Исходная упакованная схема должна сохраняться отдельно')
    design=json.loads(args.вход.read_text())
    count=corrected(design)
    args.выход.write_text(json.dumps(design,separators=(',',':'))+'\n')
    print(f'Проверено {count} независимых DSP: снято только ограничение Z=6')

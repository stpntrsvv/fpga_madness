#!/usr/bin/env python3
"""Собрать GRD2 с отдельной проверкой ограничений упакованных DSP."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import subprocess

from fix_dsp_placement import corrected

ROOT=Path(__file__).resolve().parent.parent


def sources():
    return sorted([*Path('rtl').glob('*.sv'),*Path('constraints').glob('*.xdc'),
        Path('apio.ini'),Path('scripts/build_profile.sh'),Path('scripts/build_field.py'),
        Path('scripts/fix_dsp_placement.py'),Path('scripts/fix_dsp_fasm.py'),Path('scripts/check_dsp_routes.py')])


def hashes():
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources()}


def main():
    os.chdir(ROOT)
    config=configparser.ConfigParser();config.read('apio.ini')
    name=sys.argv[1] if len(sys.argv)>1 else 'field'
    allowed={'field':'field_top','grid-fclk8':'grid_fclk8_top','grid8-safe':'grid8_top'}
    if name not in allowed:
        raise ValueError('Неизвестный профиль независимых DSP')
    profile=config['env:'+name]
    top=allowed[name]
    if profile['top-module']!=top or profile['board']!='pynq-z2':
        raise ValueError('Сценарий ограничен текущим профилем GRD2 для XC7Z020')
    synth_flags=shlex.split(profile['yosys-extra-options'])
    if set(synth_flags)!={'-nodsp','-nowidelut','-nosrl'}:
        raise ValueError('Для этого опыта требуется проверенный набор параметров синтеза')
    pnr_flags=shlex.split(profile['nextpnr-extra-options'])
    if any(k in pnr_flags for k in ('--json','--no-pack','--chipdb','--fasm','--report')):
        raise ValueError('Входы и этапы размещения задаются проверяемым сценарием')
    # Настройки из упакованного JSON восстанавливаются при следующем запуске.
    # Поэтому цель, начальное зерно и алгоритмы задаются уже при упаковке.
    pack_flags=[];i=0
    while i<len(pnr_flags):
        flag=pnr_flags[i]
        if flag in ('--log','--write'):i+=2;continue
        if flag in ('--freq','--seed','--placer','--router','--starttemp','--cstrweight'):
            pack_flags.extend(pnr_flags[i:i+2]);i+=2
        elif flag in ('--no-tmdriv','--placer-budgets','--verbose','--debug','--timing-allow-fail'):
            pack_flags.append(flag);i+=1
        else:raise ValueError(f'Параметр требует явного переноса в этап упаковки: {flag}')
    target_hz=float(pnr_flags[pnr_flags.index('--freq')+1])*1e6
    tools=Path(os.environ.get('OPENXC7_BIN',Path.home()/'.apio/packages/openxc7/bin'))
    oss=Path(os.environ.get('OSS_CAD_BIN',Path.home()/'.apio/packages/oss-cad-suite/bin'))
    nextpnr=tools/'nextpnr-xilinx'
    version=subprocess.check_output([str(nextpnr),'--version'],stderr=subprocess.STDOUT,text=True).strip()
    if 'Version 68aeeb3' not in version:
        raise ValueError('Обход ограничения DSP требует отдельной проверки для другой версии nextpnr')
    before=hashes()
    directory=Path('_build')/name;directory.mkdir(parents=True,exist_ok=True)
    prefix=directory/'hardware'
    def run(args):
        print(shlex.join(map(str,args)),flush=True)
        subprocess.run(list(map(str,args)),check=True)
    run([oss/'yosys','-p',f'synth_xilinx -arch xc7 -top {top} {" ".join(synth_flags)}; write_json {prefix}.json',
         '-q','-DSYNTHESIZE',*sorted(Path('rtl').glob('*.sv'))])
    chipdb=tools.parent/'chipdb/xc7z020clg400.bin'
    packed=Path(str(prefix)+'.packed.json')
    patched=Path(str(prefix)+'.dsp-placement.json')
    run([nextpnr,'--chipdb',chipdb,'--xdc',profile['constraint-file'],'--json',str(prefix)+'.json',
         '--pack-only','--write',packed,'-q','--log',directory/'packing.log',*pack_flags])
    design=json.loads(packed.read_text())
    saved=[float(m['settings']['target_freq']) for m in design['modules'].values()
           if 'target_freq' in m.get('settings',{})]
    if saved!=[target_hz]:
        raise ValueError(f'Упаковка сохранила неверную цель частоты: {saved}, ожидалось {target_hz}')
    count=corrected(design)
    patched.write_text(json.dumps(design,separators=(',',':'))+'\n')
    del design
    print(f'Проверено {count} независимых DSP: снята только принудительная привязка Z=6',flush=True)
    manifest=dict(version=version,count=count,target_frequency_hz=target_hz,packed_sha256=hashlib.sha256(packed.read_bytes()).hexdigest(),
                  patched_sha256=hashlib.sha256(patched.read_bytes()).hexdigest(),sources=before)
    run([nextpnr,'--chipdb',chipdb,'--xdc',profile['constraint-file'],'--json',patched,'--no-pack',
         '--fasm',str(prefix)+'.fasm','--report',str(prefix)+'.pnr','-q',*pnr_flags])
    if before!=hashes():
        raise ValueError('Исходники изменились во время сборки; результат не допускается')
    (directory/'dsp-placement.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Собрать две реализации для N6; официальные CMSIS закреплены по коммитам."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build/n6'
PACKAGES = (
    ('device', 'STMicroelectronics/cmsis-device-n6', '81fe2fe8d576ec4c55be308ac4504bd580e498e6', 'Include/'),
    ('core', 'ARM-software/CMSIS_6', 'b0bbb0423b278ca632cfe1474eb227961d835fd2', 'CMSIS/Core/Include/'),
)

def compiler():
    if os.environ.get('ARM_GCC'):
        return os.environ['ARM_GCC']
    found = shutil.which('arm-none-eabi-gcc')
    if found:
        return found
    found = Path.home() / '.platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-gcc'
    if found.exists():
        return str(found)
    raise SystemExit('Нужен GCC для Cortex-M55; укажите ARM_GCC')

def fetch():
    for name, repo, sha, prefix in PACKAGES:
        destination = OUT / 'vendor' / name
        marker = destination / '.commit'
        if marker.exists() and marker.read_text().strip() == sha:
            continue
        tree = json.load(urllib.request.urlopen(f'https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1'))
        for item in tree['tree']:
            p = item['path']
            if item['type'] != 'blob' or not p.startswith(prefix) or not p.endswith('.h'):
                continue
            target = destination / p[len(prefix):]
            target.parent.mkdir(parents=True, exist_ok=True)
            data = urllib.request.urlopen(f'https://raw.githubusercontent.com/{repo}/{sha}/{p}').read()
            target.write_bytes(data)
        marker.write_text(sha + '\n')

def build(mhz=800, dtcm=True):
    OUT.mkdir(parents=True, exist_ok=True)
    fetch()
    cc = compiler()
    prefix = cc.removesuffix('gcc')
    flags = ['-std=c11', '-O3', '-mcpu=cortex-m55', '-mthumb', '-mfloat-abi=hard',
             '-Wall', '-Wextra', '-Werror', '-ffunction-sections', '-fdata-sections', '-fno-lto',
             '-Iscripts', '-Ibuild/n6/vendor/core', '-Ibuild/n6/vendor/device']
    sources = [('scalar', 'scripts/grid_cpu.c', ['-fno-tree-vectorize', '-fno-tree-slp-vectorize']),
               ('helium', 'scripts/n6/grid_helium.c', []),
               ('firmware', 'scripts/n6/firmware.c', ['-mcmse', '-DSTM32N657xx', f'-DN6_CPU_MHZ={mhz}', f'-DN6_DTCM={int(dtcm)}']),
               ('startup', 'scripts/n6/startup.S', [])]
    for name, source, extra in sources:
        subprocess.run([cc, *flags, *extra, '-c', source, '-o', str(OUT / f'{name}.o')], cwd=ROOT, check=True)
    elf = OUT / 'grid.elf'
    subprocess.run([cc, *flags, '-nostartfiles', '-T', 'scripts/n6/memory.ld',
                    '-Wl,--gc-sections,-Map=build/n6/grid.map', '--specs=nosys.specs',
                    *(str(OUT / f'{x[0]}.o') for x in sources), '-o', str(elf)], cwd=ROOT, check=True)
    code = {}
    for variant in ('scalar', 'helium'):
        code[variant] = subprocess.check_output([prefix + 'objdump', '-d', '-marmv8.1-m.main',
                                                str(OUT / f'{variant}.o')], text=True)
        (OUT / f'{variant}.asm').write_text(code[variant])
    for required in ('vmulh.s32', 'vmul.i32', 'vldrw', 'vstrw'):
        if required not in code['helium']:
            raise AssertionError(f'Нет команды Helium {required}')
    if re.search(r'\bq[0-7]\b|\bvldr[bhw]|\bvstr[bhw]', code['scalar']):
        raise AssertionError('В скалярный решатель попали векторные команды')
    symbols = subprocess.check_output([prefix + 'nm', '-n', str(elf)], text=True)
    (OUT / 'symbols.txt').write_text(symbols)
    paths = [ROOT / x[1] for x in sources] + [ROOT / 'scripts/grid_cpu.h', Path(__file__), ROOT / 'scripts/n6/memory.ld', ROOT / 'scripts/n6/debug.cfg', ROOT / 'scripts/n6/benchmark.py']
    manifest = {'cpu_mhz': mhz, 'work_memory': 'DTCM' if dtcm else 'AXISRAM2', 'compiler': subprocess.check_output([cc, '--version'], text=True).splitlines()[0],
                'flags': flags, 'scalar_extra_flags': sources[0][2], 'cmsis': PACKAGES,
                'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest(),
                'helium_machine_code_checked': True, 'scalar_machine_code_checked': True}
    (OUT / 'build.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    subprocess.run([prefix + 'size', str(elf)], check=True)
    return elf

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--мгц', type=int, choices=(600, 800), default=800)
    p.add_argument('--память', choices=('DTCM', 'AXISRAM2'), default='DTCM')
    args = p.parse_args()
    build(args.мгц, args.память == 'DTCM')

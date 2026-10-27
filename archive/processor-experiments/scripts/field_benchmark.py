#!/usr/bin/env python3
"""Обычные C/NEON для двух законов; контрольные исходники тепла сохраняются."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import struct
import subprocess

from benchmark import machine
from dpu import ONE, ROOT
from field_program import reference_reaction
from grid_benchmark import cases, compile_c, digest, fixture, run_c as run_heat
from grid_model import example

SOURCES=('scripts/grid_cpu.c','scripts/grid_cpu.h','scripts/grid_cpu_main.c',
         'scripts/grid_benchmark.py','scripts/field_benchmark.py','scripts/field_reaction_scalar.inc',
         'scripts/field_reaction_neon.inc','scripts/field_program.py')


def substitute(source, old, new):
    if source.count(old)!=1:
        raise ValueError('Контрольный C изменён: нужно явно обновить место добавления реакции')
    return source.replace(old,new)


def build(directory,sanitizer=False):
    directory.mkdir(parents=True,exist_ok=True)
    code=(ROOT/'scripts/grid_cpu.c').read_text()
    code=substitute(code,'static inline int32_t update(',
                    (ROOT/'scripts/field_reaction_scalar.inc').read_text()+'\nstatic inline int32_t update(')
    code=substitute(code,'static inline int32x4_t transition4(',
                    (ROOT/'scripts/field_reaction_neon.inc').read_text()+'\nstatic inline int32x4_t transition4(')
    code='extern int field_a, field_b;\n'+code
    old='    return narrow((int64_t)work->current[i] + floor16((int64_t)g->dt * derivative), bad);'
    code=substitute(code,old,'    derivative = reaction(work->current[i], derivative, bad);\n'+old)
    old='                vst1q_s32(work->trial + i, transition4('
    code=substitute(code,old,'                derivative = reaction4(vld1q_s32(work->current + i), derivative, &bad4);\n'+old)
    (directory/'reaction.c').write_text(code)
    main=(ROOT/'scripts/grid_cpu_main.c').read_text()
    main=substitute(main,'int main(int argc, char **argv) {','int field_a, field_b;\nint main(int argc, char **argv) {')
    main=substitute(main,'require(argc == 5,','require(argc == 7,')
    main=substitute(main,'    FILE *input = fopen',
                    '    char *end_a, *end_b;\n    long a=strtol(argv[5],&end_a,10), b=strtol(argv[6],&end_b,10);\n'
                    '    require(*end_a==0 && *end_b==0 && a>=INT32_MIN && a<=INT32_MAX && b>=INT32_MIN && b<=INT32_MAX,"Неверные a/b");\n'
                    '    field_a=(int)a; field_b=(int)b;\n    FILE *input = fopen')
    (directory/'main.c').write_text(main)
    flags=['-std=c11','-O3','-mcpu=native','-fno-lto','-Wall','-Wextra','-Werror','-I',str(ROOT/'scripts')]
    if sanitizer:flags+=['-fsanitize=undefined','-fno-sanitize-recover=all']
    objects=[]
    for name,extra in (('scalar',['-DGRID_RUN=grid_scalar','-fno-vectorize','-fno-slp-vectorize']),
                       ('neon',['-DGRID_RUN=grid_neon','-DGRID_NEON=1']),('main',[])):
        source=directory/('main.c' if name=='main' else 'reaction.c')
        obj=directory/(name+'.o')
        subprocess.run(['clang',*flags,*extra,'-c',str(source),'-o',str(obj)],check=True)
        objects.append(str(obj))
        if name=='neon' and not sanitizer:
            assembly=directory/'neon.s'
            subprocess.run(['clang',*flags,*extra,'-S',str(source),'-o',str(assembly)],check=True)
            if not all(s in assembly.read_text() for s in ('smull.2d','smull2.2d')):
                raise AssertionError('Нет фактических знаковых векторных произведений')
    binary=directory/'field_benchmark'
    subprocess.run(['clang',*flags,*objects,'-o',str(binary)],check=True)
    return binary


def answer(grid,steps,a,b):
    state,completed,fault=grid.state,0,0
    for _ in range(steps):
        state,fault=reference_reaction(replace(grid,state=state),a,b)
        if fault:break
        completed+=1
    return state,completed,fault


def run(binary,grid,steps,variant,a,b,timing=False,expected=None):
    raw=fixture(grid,steps)
    inp,out=binary.parent/'input.bin',binary.parent/'output.bin'
    inp.write_bytes(raw)
    result=json.loads(subprocess.check_output([str(binary),str(inp),str(out),str(variant),str(int(timing)),str(a),str(b)],text=True))
    data=out.read_bytes()
    if len(data)!=8+4*len(grid.state):raise AssertionError('Неполный ответ C')
    completed,fault=struct.unpack_from('<2I',data)
    state=struct.unpack_from(f'<{len(grid.state)}i',data,8)
    if (state,completed,fault)!=(expected if expected is not None else answer(grid,steps,a,b)):
        raise AssertionError('C реакции–диффузии расходится с эталоном')
    if (completed,fault)!=(result['completed'],result['fault']):raise AssertionError('Разошлись отчёты C')
    result.update(input_sha256=hashlib.sha256(raw+struct.pack('<2i',a,b)).hexdigest(),
                  state_sha256=hashlib.sha256(data[8:]).hexdigest())
    if timing:result['median_ns_per_step']=statistics.median(result['samples_ns'])
    return result


def verify(binary):
    count=0
    for grid,steps in cases():
        a,b=ONE//8,ONE//16
        expected=answer(grid,steps,a,b)
        for variant in (0,1):
            run(binary,grid,steps,variant,a,b,expected=expected);count+=1
    for a,b in ((0,0),(-ONE,ONE),(ONE,-ONE),((1<<31)-1,-(1<<31))):
        grid=example(9,3)
        expected=answer(grid,3,a,b)
        for variant in (0,1):
            run(binary,grid,3,variant,a,b,expected=expected);count+=1
    return count


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--только-проверка',action='store_true')
    parser.add_argument('--отчёт',type=Path,default=ROOT/'reports/сравнение-программируемого-поля.json')
    args=parser.parse_args()
    directory=ROOT/'build/field_cpu'
    binary=build(directory)
    sanitized=build(directory/'ubsan',True)
    checks=dict(release=verify(binary),ubsan=verify(sanitized))
    print(f'УСПЕХ: C/NEON реакции–диффузии: {checks}',flush=True)
    report=dict(date=datetime.now(timezone.utc).isoformat(),kind='Измерение CPU; аппаратного времени GRD2 нет',
        machine=machine(),source_sha256=digest(SOURCES),source_files=SOURCES,checks=checks,cpu_threads=1,
        method='8 прогревов, 9 серий >=30 мс, медиана, CLOCK_MONOTONIC_RAW; ввод, копирование и контрольные суммы вне интервала',experiments=[])
    if not args.только_проверка:
        heat_binary,flags=compile_c(directory/'heat_control')
        report['compiler_flags']=flags
        for size in (64,128):
            grid=example(size,size)
            for law in ('тепло','реакция–диффузия'):
                row=dict(width=size,height=size,dt_raw=grid.dt,steps=64,seed=18,law=law,cpu={})
                if law=='реакция–диффузия':row.update(a_raw=ONE//8,b_raw=ONE//16)
                for variant,name in ((0,'scalar'),(1,'neon')):
                    result=run_heat(heat_binary,grid,64,variant,True) if law=='тепло' else run(binary,grid,64,variant,ONE//8,ONE//16,True)
                    row['cpu'][name]=result
                    print(f'{size}×{size}, {law}, {name}: {result["median_ns_per_step"]/1000:.3f} мкс/шаг',flush=True)
                report['experiments'].append(row)
    args.отчёт.parent.mkdir(parents=True,exist_ok=True)
    args.отчёт.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f'Отчёт: {args.отчёт}')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Предварительно оценить программируемые профили без синтеза и трассировки.

Результат различает проверенные старые модели, модель идеальной выдачи с общими
умножителями и аналитический кандидат двух проходов. LUT/частота не предсказываются.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import grid_model as gm
import field_program as fp
from dpu import ONE, checked, ROOT

BASE_HZ = 64433267.83847035
M1_HEAT_US = 8.392878434
# Историческая отдельная серия реакции, не свежий предел M1.
M1_REACTION_US = 18.30

@contextmanager
def graph_operations(reaction):
    old_operations, old_consumers = gm.OPERATIONS, gm.CONSUMERS
    try:
        if reaction:
            operations = list(old_operations[:8])
            operations += [('diffusion', *old_operations[8][1:]),
                ('u2', 'mul', ('center', 'center'), lambda a,b: (a*b)>>16),
                ('u3', 'mul', ('u2', 'center'), lambda a,b: (a*b)>>16),
                ('au', 'mul', ('center',), lambda a: ((ONE//8)*a)>>16),
                ('bu3', 'mul', ('u3',), lambda a: ((ONE//16)*a)>>16),
                ('r0', 'add', ('diffusion', 'au'), lambda a,b: a+b),
                ('derivative', 'add', ('r0', 'bu3'), lambda a,b: a-b)]
            gm.OPERATIONS = tuple(operations) + old_operations[9:]
            gm.CONSUMERS = {name: frozenset(op for op,_,inputs,_ in gm.OPERATIONS if name in inputs)
                            for _,_,inputs,_ in gm.OPERATIONS for name in inputs}
        yield
    finally:
        gm.OPERATIONS, gm.CONSUMERS = old_operations, old_consumers


def timing(cycles):
    return {'cycles': cycles, 'us_at_baseline_clock': cycles/BASE_HZ*1e6,
            'us_at_hypothetical_100mhz': cycles/100,
            'us_at_hypothetical_125mhz': cycles/125,
            'heat_parity_mhz': cycles/M1_HEAT_US,
            'reaction_parity_mhz_historical': cycles/M1_REACTION_US}


def two_pass_reference(grid):
    """Проверить точную декомпозицию: scratch=(diffusion,u2), затем реакция/Эйлер.

    Это проверка арифметики проходов; выдачу BRAM и управление она не моделирует.
    """
    w,h = grid.width,grid.height
    n=w*h
    e,s=[0]*n,[0]*n
    try:
        for y in range(h):
            for x in range(w):
                i=y*w+x
                if x+1<w:e[i]=checked(grid.east[i]*checked(grid.state[i+1]-grid.state[i])>>16)
                if y+1<h:s[i]=checked(grid.south[i]*checked(grid.state[i+w]-grid.state[i])>>16)
        diffusion,u2=[],[]
        for y in range(h):
            for x in range(w):
                i=y*w+x
                b=checked(e[i]+s[i]);b=checked(b-(e[i-1] if x else 0))
                b=checked(b-(s[i-w] if y else 0));b=checked(b+grid.source[i])
                diffusion.append(checked(grid.capacity_inverse[i]*b>>16))
                u2.append(checked(grid.state[i]*grid.state[i]>>16))
        trial=[]
        for i,t in enumerate(grid.state):
            u3=checked(u2[i]*t>>16)
            au=checked((ONE//8)*t>>16);bu3=checked((ONE//16)*u3>>16)
            derivative=checked(checked(diffusion[i]+au)-bu3)
            trial.append(checked(t+((grid.dt*derivative)>>16)))
        return tuple(trial),0
    except OverflowError:
        return grid.state,3


def main():
    grid=gm.example(64,64)
    report={'date':datetime.now(timezone.utc).isoformat(), 'shape':[64,64],
            'baseline_hz':BASE_HZ,'baseline_cycles':535,'full_product_dsp':4,'device_dsp_limit':220,
            'operation_counts':{'heat':{'mul':4,'simple':7},'reaction':{'mul':8,'simple':9}},
            'notes':['Все новые времена условны; частота базового FPGA не переносится на новую схему.',
                     'Глобальная выдача общих умножителей предполагает идеальный доступ к операндам и отдельный простой блок на каждое имя узла.',
                     'Модель прежнего grid_model имеет свой контроллер памяти и не воспроизводит 535 тактов RTL.',
                     'Аналитические 32 служебных такта нового двухпроходного профиля — проектное допущение, не потактовая проверка контроллера.'],
            'streaming':[], 'shared_pool':[]}
    # Существующая потоковая модель: два разных закона, с верификацией полного поля.
    for lanes in (4,8):
        p=replace(fp.profiles()[1],lanes=lanes,banks=lanes)
        for reaction in (False,True):
            graph=fp.reaction_graph() if reaction else fp.heat_graph()
            image,_=fp.compile_image(fp.flux_graph(),graph,p,constants=(ONE//8,ONE//16)+(0,)*6)
            result=fp.simulate(grid,image,p)
            answer=fp.reference_reaction(grid,ONE//8,ONE//16) if reaction else gm.reference(grid)
            assert (result['state'],result['fault'])==answer
            report['streaming'].append({'lanes':lanes,'law':'reaction' if reaction else 'heat',
                **timing(result['cycles']),**fp.budget(p), 'dsp_fits':fp.budget(p)['dsp48']<=220})
    # У общего пула нет заявленной цены широкого доступа к контекстам/маршрутам.
    for multipliers in (2,4):
        for slots in (16,32):
            for reaction in (False,True):
                for dt in (4096,8191):
                    g=replace(grid,dt=dt)
                    f=gm.Fabric(lanes=8,banks=8,multipliers=multipliers,slots=slots,shift_dt=False)
                    with graph_operations(reaction):result=gm.simulate(g,f)
                    answer=fp.reference_reaction(g,ONE//8,ONE//16) if reaction else gm.reference(g)
                    assert (result['state'],result['fault'])==answer
                    report['shared_pool'].append({'multipliers_per_lane':multipliers,'lanes':8,
                        'slots_per_lane':slots,'law':'reaction' if reaction else 'heat','dt':dt,
                        'dsp':8*multipliers*4,**timing(result['cycles']),
                        'stats':result['frames'][0]['stats']})
    # Новый ограниченный тракт: A2 -> M4||M4 -> обмен1 -> 4*A2 -> M4 -> 2*A2 -> M4 -> A1.
    # Доставка4 включена, общая глубина/завершение для оценки — 32 такта.
    overhead=4+2+4+1+8+4+4+4+1
    assert overhead==32
    report['narrow_pipeline']={'dsp':128,'lanes':8,'overhead_assumed':overhead,
        'heat':timing(512+overhead),
        'reaction_two_pass_without_switch':timing(2*(512+overhead)),
        'reaction_two_pass_with_16_switch_cycles':timing(2*(512+overhead)+16),
        'scratch_fields':2,'scratch_bytes':2*4096*4,'scratch_ramb18_one_read_one_write':16,
        'total_ramb18_from_current_96':112,'projected_ramb18_after_separate_map_optimization':80,
        'status':'Аналитическая оценка. Нет RTL, проверенной доставки нового окна или размещения.'}
    # 230 прежних ошибок/краёв плюс несколько эпох для точного разбиения на проходы.
    from grid_benchmark import cases
    checks=0
    for g,steps in cases():
        # Сравнивается одна эпоха каждого входа независимо от числа шагов CPU-случая.
        assert two_pass_reference(g)==fp.reference_reaction(g,ONE//8,ONE//16)
        checks+=1
    g=grid
    for _ in range(64):
        state,fault=two_pass_reference(g)
        assert (state,fault)==fp.reference_reaction(g,ONE//8,ONE//16)
        assert not fault
        g=replace(g,state=state);checks+=1
    report['two_pass_arithmetic_comparisons']=checks
    report['source_sha256']={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
        ('scripts/programmability_estimate.py','scripts/grid_model.py','scripts/field_program.py')}
    out=ROOT/'reports/оценка-программируемости-2026-10-22.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    for r in report['streaming']: print('Конвейер',r['lanes'],r['law'],r['cycles'],r['dsp48'],'DSP')
    for r in report['shared_pool']:
        if r['slots_per_lane']==32 and r['dt']==8191:
            print('Общий пул',r['multipliers_per_lane'],r['law'],r['cycles'],r['dsp'],'DSP',
                  'живых слов',r['stats'].get('max_live_words'))
    print('Узкий двухпроходный:',report['narrow_pipeline'])
    print('Сравнения арифметики двух проходов:',checks)

if __name__=='__main__':main()

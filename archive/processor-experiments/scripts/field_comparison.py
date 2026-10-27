#!/usr/bin/env python3
"""Сопоставить сохранённый полный опыт GRD2 с актуальной серией C/NEON."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import struct

from dpu import ONE,ROOT
from field_benchmark import SOURCES
from field_vectors import checks
from grid_benchmark import digest,fixture
from grid_model import example


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--плата',type=Path,default=ROOT/'reports/поле-плата.json')
    parser.add_argument('--процессор',type=Path,default=ROOT/'reports/сравнение-программируемого-поля.json')
    parser.add_argument('--отчёт',type=Path,default=ROOT/'reports/сопоставление-поля.json')
    args=parser.parse_args()
    board=json.loads(args.плата.read_text());cpu=json.loads(args.процессор.read_text())
    vectors=checks()
    expected_packets=len(vectors)+3*sum(bool(r[4]) for r in vectors)
    if board.get('profile')!='field' or board.get('shape')!=[64,64] or board.get('checked_packets')!=expected_packets:
        raise ValueError('Нужен полный проверенный опыт программируемого поля 64×64')
    if board['vectors_sha256']!=hashlib.sha256(repr(vectors).encode()).hexdigest():
        raise ValueError('Образы графов или аппаратные проверки изменены после опыта')
    if cpu['source_sha256']!=digest(SOURCES) or cpu['cpu_threads']!=1:
        raise ValueError('Нужен актуальный однопоточный опыт C/NEON')
    report=dict(date=datetime.now(timezone.utc).isoformat(),
        board_report_sha256=hashlib.sha256(args.плата.read_bytes()).hexdigest(),
        cpu_report_sha256=hashlib.sha256(args.процессор.read_bytes()).hexdigest(),
        bitstream_sha256=board['bitstream_sha256'],laws={})
    raw=fixture(example(64,64),64)
    for name,run in board['laws'].items():
        row=next(r for r in cpu['experiments'] if r['width']==r['height']==64 and r['law']==name
                 and r['steps']==64 and r['dt_raw']==4096 and r['seed']==18)
        reaction=name=='реакция–диффузия'
        if reaction and (row.get('a_raw'),row.get('b_raw'))!=(ONE//8,ONE//16):
            raise ValueError('Разные параметры реакции')
        expected=hashlib.sha256(raw+(struct.pack('<2i',ONE//8,ONE//16) if reaction else b'')).hexdigest()
        if any(r['input_sha256']!=expected or r['completed']!=64 or r['fault'] for r in row['cpu'].values()):
            raise ValueError('Разные исходные поля или незавершённый CPU-расчёт')
        if run['completed']!=64 or run['elapsed_cycles']!=64*1072 or run['last_epoch_cycles']!=1072:
            raise ValueError('Неверные аппаратные счётчики контрольного запуска')
        fpga=run['elapsed_cycles']*1e9/64/board['clock_before']['midpoint_hz']
        neon=row['cpu']['neon']['median_ns_per_step']
        report['laws'][name]=dict(cycles_per_step=1072,fpga_ns_per_step=fpga,
            neon_ns_per_step=neon,scalar_ns_per_step=row['cpu']['scalar']['median_ns_per_step'],
            fpga_over_cpu_time=fpga/neon,neon_samples_ns=row['cpu']['neon']['samples_ns'])
        print(f'{name}: FPGA {fpga/1000:.3f} мкс, NEON {neon/1000:.3f} мкс, '
              f'отношение времён FPGA/CPU {fpga/neon:.3f}',flush=True)
    if set(report['laws'])!={'тепло','реакция–диффузия'}:
        raise ValueError('Нет двух проверенных законов')
    args.отчёт.parent.mkdir(parents=True,exist_ok=True)
    args.отчёт.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f'Отчёт: {args.отчёт}')


if __name__=='__main__':main()

#!/usr/bin/env python3
"""Проверить GRD2: загрузить разные графы в одну конфигурацию PL и измерить такты."""
import argparse
from datetime import datetime, timezone
import hashlib
import json

from dpu import ROOT
from field_vectors import checks
from grid_board import exchange, clock_sample
from grid_vectors import packet

SIGNATURE=0x47524432


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--только-чтение',action='store_true')
    args=parser.parse_args()
    exchange([(packet(3),SIGNATURE,0,0,0)],ROOT/'reports/поле-версия.log',signature=SIGNATURE)
    report=dict(date=datetime.now(timezone.utc).isoformat(),profile='field',shape=[64,64])
    report['clock_before']=clock_sample(ROOT/'reports/поле-частота-до.log',SIGNATURE)
    print(f'Частота GRD2: {report["clock_before"]["midpoint_hz"]/1e6:.3f} МГц',flush=True)
    if not args.только_чтение:
        rows=checks();augmented=[];indices=[]
        for row in rows:
            augmented.append(row)
            if row[4]:
                indices.append(len(augmented))
                augmented.extend((packet(3,a),None,0,0,0) for a in (2,3,4))
        records=exchange(augmented,ROOT/'reports/поле-плата.log',signature=SIGNATURE)
        report['checked_packets']=len(augmented)
        report['runs']=[dict(completed=records[i][1],elapsed_cycles=records[i+1][1],last_epoch_cycles=records[i+2][1]) for i in indices]
        report['clock_after']=clock_sample(ROOT/'reports/поле-частота-после.log',SIGNATURE)
        report['vectors_sha256']=hashlib.sha256(repr(rows).encode()).hexdigest()
        report['bitstream_sha256']=hashlib.sha256((ROOT/'build/поле-программируемое.bit').read_bytes()).hexdigest()
        long_runs=[r for r in report['runs'] if r['completed']==64]
        if len(long_runs)!=2:raise AssertionError('Нет двух проверенных длинных запусков')
        report['laws']={name:dict(run,ns_per_step=run['elapsed_cycles']*1e9/64/report['clock_before']['midpoint_hz'])
                        for name,run in zip(('тепло','реакция–диффузия'),long_runs)}
        cpu_path=ROOT/'reports/сравнение-программируемого-поля.json'
        if cpu_path.exists():
            from field_benchmark import SOURCES
            from grid_benchmark import digest
            cpu=json.loads(cpu_path.read_text())
            if cpu['source_sha256']!=digest(SOURCES):raise AssertionError('Исходники CPU изменены после замера')
            for name,row in report['laws'].items():
                measured=next(r for r in cpu['experiments'] if r['width']==64 and r['law']==name)
                row['cpu_neon_ns_per_step']=measured['cpu']['neon']['median_ns_per_step']
                row['fpga_over_cpu_time']=row['ns_per_step']/row['cpu_neon_ns_per_step']
            report['cpu_report_sha256']=hashlib.sha256(cpu_path.read_bytes()).hexdigest()
        # Подтверждение конечной очистки чтением регистров.
        exchange([(packet(3,a),v,0,0,0) for a,v in ((0,SIGNATURE),(1,0),(2,0),(3,0),(16,0))],
                 ROOT/'reports/поле-итоговое-состояние.log',signature=SIGNATURE)
        print(f'УСПЕХ: {len(augmented)} пакетов, разные графы, все поля и ошибки; ядро очищено',flush=True)
    path=ROOT/'reports/поле-плата.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f'Отчёт: {path}')


if __name__=='__main__':main()

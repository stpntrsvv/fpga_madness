#!/usr/bin/env python3
"""Проверить пакетный тепловой RTL 64×64 через USER1, без настройки PS."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from dpu import ROOT
from grid_vectors import checks, identity_checks, packet


def exchange(rows, path, pauses=None, capture_payload=False, signature=0x47524431):
    pauses = pauses or {}
    zero = ' '.join(['32 0'] * 34)
    script = ['init', f'source {{{ROOT / "scripts/jtag_sanity.tcl"}}}',
              'irscan zynq_pl.bs 0x02', 'runtest 32',
              'proc exchange {request} {',
              '  drscan zynq_pl.bs {*}$request', '  runtest 8',
              '  for {set attempt 0} {$attempt < 100} {incr attempt} {',
              f'    set response [drscan zynq_pl.bs {zero}]',
              '    set signature [expr "0x[lindex $response 33]"]', '    runtest 8',
              f'    if {{$signature == 0x{signature:08x}}} {{ return $response }}',
              '    if {$signature != 0x42555359} { error "Неверный пакетный протокол: $response" }',
              '  }', '  error "Нет ответа пакетного ядра"', '}']
    for index, (request, expected, data, compare, wait) in enumerate(rows):
        if index in pauses:
            script += [f'sleep {pauses[index]}']
        fields = ' '.join(f'32 0x{(request >> (32*i)) & 0xffffffff:08x}' for i in range(34))
        script += ['set before [ms]', f'set response [exchange {{{fields}}}]',
                   'set after [ms]', 'set result [expr "0x[lindex $response 32]"]']
        if capture_payload:
            script += [f'echo "WORDS {index} $response"']
        if expected is not None:
            script += [f'if {{$result != 0x{expected:08x}}} {{ error "Ответ {index}: $response" }}']
        if wait:
            # Команда RUN подтверждается до завершения; опрос не гоняет поля через хост.
            read_status = ' '.join(f'32 0x{(packet(3, 1) >> (32*i)) & 0xffffffff:08x}' for i in range(34))
            script += ['set deadline [expr {[ms]+60000}]', 'while {1} {',
                       f'  set status [exchange {{{read_status}}}]',
                       '  if {([expr "0x[lindex $status 32]"] & 1) == 0} { break }',
                       '  if {[ms] > $deadline} { error "Истекло время расчёта сетки" }', '  sleep 1', '}']
        if compare:
            for word in range(32):
                expected_word = (data >> (32*word)) & 0xffffffff
                script += [f'if {{[expr "0x[lindex $response {word}]"] != 0x{expected_word:08x}}} '
                           f'{{ error "Поле {index}, слово {word}: $response" }}']
        script += [f'echo "GRID {index} $result $before $after"']
        if index % 256 == 0:
            script += [f'echo "Проверено пакетов: {index}"']
    script += [f'echo "УСПЕХ: {len(rows)} пакетов сетки 64×64"', 'shutdown']
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='grid-jtag-') as directory:
        tcl = Path(directory) / 'exchange.tcl'
        tcl.write_text('\n'.join(script) + '\n')
        with path.open('w') as log:
            result = subprocess.run(['openocd', '-f', 'interface/cmsis-dap.cfg', '-f',
                                     str(ROOT / 'scripts/jtag_chain.tcl'), '-f', str(tcl)],
                                    stdout=log, stderr=subprocess.STDOUT, text=True)
    output = path.read_text()
    if result.returncode or 'УСПЕХ:' not in output:
        raise RuntimeError(output[-7000:])
    records = [tuple(map(int, row)) for row in re.findall(r'^GRID (\d+) (\d+) (\d+) (\d+)$', output, re.M)]
    if len(records) != len(rows):
        raise AssertionError('Неполный журнал пакетов')
    return records


def clock_sample(path, signature=0x47524431, pause_ms=2000):
    rows = [(packet(3, address), None, 0, 0, 0) for address in (5, 6, 5, 6)]
    records = exchange(rows, path, pauses={2: pause_ms}, signature=signature)
    first = records[0][1] | (records[1][1] << 32)
    second = records[2][1] | (records[3][1] << 32)
    ticks = (second - first) & ((1 << 64) - 1)
    minimum_ms = records[2][2] - records[0][3]
    maximum_ms = records[2][3] - records[0][2]
    if minimum_ms <= 0 or ticks == 0:
        raise AssertionError('Счётчик генератора сетки не продвигается')
    return dict(ticks=ticks, minimum_hz=ticks*1000/maximum_ms,
                maximum_hz=ticks*1000/minimum_ms, midpoint_hz=ticks*2000/(minimum_ms+maximum_ms))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--профиль', choices=('grid-safe', 'grid-fclk', 'grid-fclk8', 'grid8-safe'), default='grid-safe')
    parser.add_argument('--процессор', type=Path,
                        default=ROOT / 'reports/сравнение-сетки-2026-10-18.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--только-чтение', action='store_true', help='Прочитать версию и частоту без записи полей')
    mode.add_argument('--проверить-перенос', action='store_true', help='Проверить нулевую производную и оба снимка')
    args = parser.parse_args()
    lanes = 8 if args.профиль in ('grid-fclk8', 'grid8-safe') else 4
    def log_path(name):
        return ROOT / 'reports' / (name + ('-8' if lanes == 8 else '') + '.log')
    report = dict(lanes=lanes, date=datetime.now(timezone.utc).isoformat(), profile=args.профиль, shape=[64,64])
    exchange([(packet(3), 0x47524431, 0, 0, 0)], log_path('сетка-версия'))
    if lanes == 8:
        exchange([(packet(3, 18), 8, 0, 0, 0)], ROOT / 'reports/сетка8-тракты.log')
    report['clock_pause_ms'] = 10000 if lanes == 8 else 2000
    report['clock_before'] = clock_sample(log_path('сетка-частота'), pause_ms=report['clock_pause_ms'])
    print(f'Частота сетки: {report["clock_before"]["midpoint_hz"]/1e6:.3f} МГц', flush=True)
    if not args.только_чтение:
        rows = identity_checks(lanes) if args.проверить_перенос else checks(lanes)
        # Добавляем чтение аппаратных счётчиков после каждого проверяемого RUN.
        augmented, measured = [], []
        for row in rows:
            augmented.append(row)
            if row[4]:
                measured.append(len(augmented))
                augmented += [(packet(3, address), None, 0, 0, 0) for address in (2,3,4)]
        log_name = 'сетка-перенос-плата.log' if args.проверить_перенос else 'сетка-плата.log'
        records = exchange(augmented, log_path(log_name.removesuffix('.log')))
        report['checked_packets'] = len(augmented)
        report['runs'] = [dict(completed=records[i][1], elapsed_cycles=records[i+1][1],
                               last_epoch_cycles=records[i+2][1]) for i in measured]
        report['clock_after'] = clock_sample(log_path('сетка-частота-после-опыта'), pause_ms=report['clock_pause_ms'])
        report['vectors_sha256'] = hashlib.sha256(repr(rows).encode()).hexdigest()
        bit = ROOT / ('build/сетка8-64.bit' if args.профиль == 'grid8-safe' else ('build/сетка-fclk8.bit' if lanes == 8 else ('build/сетка-fclk.bit' if args.профиль == 'grid-fclk' else 'build/сетка-64.bit')))
        if bit.exists():
            report['bitstream_sha256'] = hashlib.sha256(bit.read_bytes()).hexdigest()
        # Эта строка относится к проверенному выше полю example(64,64), seed=18.
        comparable = next((run for run in report['runs'] if run['completed'] == 64), None)
        if comparable:
            report['ns_per_step'] = comparable['elapsed_cycles'] * 1e9 / 64 / report['clock_before']['midpoint_hz']
        cpu_path = args.процессор
        if cpu_path.exists() and comparable:
            cpu = json.loads(cpu_path.read_text())
            from grid_benchmark import SOURCES, digest
            if cpu['source_sha256'] != digest(SOURCES):
                raise AssertionError('C изменён: прежний отчёт нельзя использовать для сравнения')
            measured = next(row for row in cpu['experiments'] if row['width'] == row['height'] == 64 and
                            row['dt_raw'] == 4096 and row['steps'] == 64)
            report['cpu_neon_ns_per_step'] = measured['cpu']['neon']['median_ns_per_step']
            report['fpga_over_cpu_time'] = report['ns_per_step'] / report['cpu_neon_ns_per_step']
            report['cpu_report_sha256'] = hashlib.sha256(cpu_path.read_bytes()).hexdigest()
            print(f'FPGA {report["ns_per_step"]/1000:.3f} мкс/шаг; '
                  f'NEON {report["cpu_neon_ns_per_step"]/1000:.3f} мкс/шаг из сохранённого опыта', flush=True)
        print(f'УСПЕХ: {len(augmented)} пакетов, полные поля совпали; ядро очищено', flush=True)
    report['verification'] = 'перенос' if args.проверить_перенос else ('чтение' if args.только_чтение else 'полная')
    name = 'сетка-перенос-плата' if args.проверить_перенос else 'сетка-плата'
    if args.профиль in ('grid-fclk', 'grid-fclk8'):
        name += '-fclk8' if lanes == 8 else '-fclk'
    if args.профиль == 'grid8-safe':
        name += '-8'
    path = ROOT / 'reports' / (name + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'Отчёт: {path}')


if __name__ == '__main__':
    main()

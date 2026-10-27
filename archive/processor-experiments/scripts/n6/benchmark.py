#!/usr/bin/env python3
"""Проверить N6 и измерить скалярный C/Helium с независимым эталоном полей."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import statistics
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from grid_benchmark import cases, expected, fixture
from grid_model import example

OUT = ROOT / 'build/n6'
REPORT = ROOT / 'reports/сетка-N6.json'

class Debugger:
    def __init__(self, run=False, serial=None):
        self.log = open(ROOT / 'reports/сетка-N6-отладчик.log', 'w')
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        cmd = ['openocd', '-c', f'tcl_port {port}; set N6_RUN {int(run)}']
        if serial:
            if not serial.isalnum():
                raise ValueError('Неверный серийный номер STLINK')
            cmd += ['-c', f'set N6_SERIAL {serial}']
        cmd += ['-f', str(ROOT / 'scripts/n6/debug.cfg')]
        self.process = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 10
        while True:
            try:
                self.sock = socket.create_connection(('127.0.0.1', port), timeout=1)
                self.sock.settimeout(15)
                break
            except OSError:
                if self.process.poll() is not None or time.monotonic() > deadline:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                    self.log.close()
                    raise RuntimeError('OpenOCD не запустился; см. reports/сетка-N6-отладчик.log')
                time.sleep(.1)
    def command(self, command):
        self.sock.sendall(command.encode() + b'\x1a')
        answer = b''
        while not answer.endswith(b'\x1a'):
            part = self.sock.recv(65536)
            if not part:
                raise RuntimeError('Отладчик разорвал соединение')
            answer += part
        text = answer[:-1].decode()
        if any(x in text.lower() for x in ('error', 'invalid', 'not found')):
            raise RuntimeError(f'{command}: {text}')
        return text
    def read(self, address, count=1, ap='n6.ap1'):
        answer = self.command(f'{ap} read_memory {address:#x} 32 {count}')
        values = [int(x, 0) for x in answer.split()]
        if len(values) != count:
            raise RuntimeError(f'Неполное чтение: {answer[:120]}')
        return values
    def write(self, address, values):
        for start in range(0, len(values), 512):
            words = ' '.join(hex(v & 0xffffffff) for v in values[start:start+512])
            self.command(f'n6.ap1 write_memory {address+4*start:#x} 32 {{{words}}}')
    def close(self):
        try:
            self.command('shutdown')
        except (OSError, RuntimeError):
            pass
        self.sock.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.log.close()

def save(report):
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

def symbols():
    return {line.split()[2]: int(line.split()[0], 16)
            for line in (OUT / 'symbols.txt').read_text().splitlines() if len(line.split()) == 3}

def wait_state(debug, box, allowed, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = debug.read(box + 8)[0]
        if state == 0xdead:
            line = debug.read(box + 18*4)[0]
            raise RuntimeError(f'N6 остановил опыт: строка firmware.c {line}')
        if state in allowed:
            return state
        time.sleep(.025)
    raise TimeoutError(f'N6 не завершил команду; состояние {state:#x}')

def run_case(debug, box, grid, steps, variant, timing=False):
    wait_state(debug, box, (1, 3))
    if debug.read(box + 4)[0] != 2:
        raise RuntimeError('Нужен ABI N6 версии 2')
    input_address, capacity = debug.read(box + 19*4, 2)
    addresses = debug.read(input_address, 5)
    for p, plane in enumerate((grid.state, grid.east, grid.south, grid.capacity_inverse, grid.source)):
        debug.write(addresses[p], plane)
    debug.write(box + 4*4, [grid.width, grid.height, grid.dt, steps, variant, int(timing)])
    debug.write(box + 8, [1])
    debug.write(box + 12, [1])
    wait_state(debug, box, (3,), timeout=45)
    words = debug.read(box, 62)
    completed, fault, result_address = words[10:13]
    raw = struct.pack(f'<{len(grid.state)}I', *debug.read(result_address, len(grid.state)))
    state = struct.unpack(f'<{len(grid.state)}i', raw)
    answer = expected(grid, steps)
    if (state, completed, fault) != answer:
        mismatch = next((i for i, (a, b) in enumerate(zip(state, answer[0])) if a != b), None)
        raise AssertionError(f'N6 отличается от эталона: вариант {variant}, '
                             f'{grid.width}×{grid.height}, dt={grid.dt}, шагов={steps}, '
                             f'получено completed/fault={completed}/{fault}, '
                             f'ожидалось {answer[1:]}, первая точка {mismatch}')
    totals = [words[34+2*i] | words[35+2*i] << 32 for i in range(9)]
    iterations = words[52:61]
    report = {'variant': ['скалярный C', 'Helium'][variant], 'width': grid.width, 'height': grid.height,
              'dt': grid.dt, 'steps': steps, 'completed': completed, 'fault': fault,
              'input_sha256': hashlib.sha256(fixture(grid, steps)).hexdigest(),
              'state_sha256': hashlib.sha256(raw).hexdigest(), 'cpu_hz_nominal': words[13],
              'axi_hz_nominal': words[14], 'ccr_during_solver': hex(words[15]),
              'cpuid': hex(words[16]), 'dwt_ctrl': hex(words[17]),
              'clock_registers': [hex(x) for x in words[21:33]]}
    if timing:
        if not steps or fault or not all(iterations):
            raise AssertionError('Нельзя измерять ошибочный или пустой запуск')
        samples = [total / count / steps for total, count in zip(totals, iterations)]
        report.update(total_cycles=totals, iterations=iterations, samples_cycles_per_step=samples,
                      median_cycles_per_step=statistics.median(samples),
                      median_ns_per_step=statistics.median(samples)*1e9/words[13])
    return report

def clock_sample(debug):
    before = time.monotonic_ns()
    a = debug.read(0xE0001004, ap='n6.ap1')[0]
    after = time.monotonic_ns()
    time.sleep(1)
    before2 = time.monotonic_ns()
    b = debug.read(0xE0001004, ap='n6.ap1')[0]
    after2 = time.monotonic_ns()
    ticks = (b-a) & 0xffffffff
    return {'ticks': ticks, 'lower_hz': ticks*1e9/(after2-before),
            'upper_hz': ticks*1e9/(before2-after),
            'host_times_ns': [before, after, before2, after2]}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--выполнить', action='store_true')
    p.add_argument('--серийный-номер')
    p.add_argument('--коротко', action='store_true', help='Только основной случай; полная проверка по умолчанию')
    args = p.parse_args()
    if args.выполнить and os.environ.get('ALLOW_N6_RUN') != 'YES':
        raise SystemExit('Для разрешённого опыта нужен ALLOW_N6_RUN=YES')
    (ROOT / 'reports').mkdir(exist_ok=True)
    d = Debugger(args.выполнить, args.серийный_номер)
    report = {'date': datetime.now(timezone.utc).isoformat(), 'board': 'NUCLEO-N657X0-Q',
              'complete': False, 'validation_complete': False,
              'full_validation_requested': not args.коротко, 'checks': [], 'benchmarks': []}
    try:
        report['idcode'] = hex(d.read(0x44001000, ap='n6.ap0')[0])
        report['cpuid'] = hex(d.read(0xE000ED00, ap='n6.ap1')[0])
        report['dscsr_before'] = hex(d.read(0xE000EE08, ap='n6.ap1')[0])
        if (int(report['idcode'], 16) & 0xfff) != 0x486 or report['cpuid'] != '0x411fd221':
            raise RuntimeError('Неожиданный STM32N6/CPUID, запуск отклонён')
        if not args.выполнить:
            (ROOT / 'reports/сетка-N6-связь.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        elf = OUT / 'grid.elf'
        build = json.loads((OUT / 'build.json').read_text())
        if hashlib.sha256(elf.read_bytes()).hexdigest() != build['elf_sha256']:
            raise RuntimeError('ELF отличается от свидетельства сборки')
        for path, checksum in build['source_sha256'].items():
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != checksum:
                raise RuntimeError(f'Исходник изменился после сборки: {path}')
        report['build'] = build
        names = symbols()
        box = names['n6_mailbox']
        d.command('targets n6.cpu')
        d.command('halt')
        d.command('wait_halt 2000')
        # У BootROM MPU может запрещать исполнение AXISRAM2.
        d.write(0xe000ed94, [0])
        ccr = d.read(0xe000ed14)[0]
        if ccr & (1 << 16):
            raise RuntimeError('D-cache включён перед загрузкой; требуется согласованное завершение прежней программы')
        d.write(0xe000ed14, [ccr & ~(1 << 17)])
        d.write(0xe000ef50, [0])
        d.write(0xe000ed28, [0xffffffff, 0xffffffff])
        d.command(f'load_image {{{elf}}}')
        d.command(f'verify_image {{{elf}}}')
        if d.read(names['vectors'], 2) != [names['_estack'], names['Reset_Handler'] | 1]:
            raise RuntimeError('Таблица векторов в SRAM отличается от ELF')
        d.command(f'reg msp {names["_estack"]:#x}')
        d.command('reg control 0')
        d.command('reg xPSR 0x01000000')
        d.command(f'resume {names["Reset_Handler"]:#x}')
        wait_state(d, box, (1,), timeout=10)
        report['memory'] = {'mpu': [hex(x) for x in d.read(0xe000ed90, 5)],
                            'mair': [hex(x) for x in d.read(0xe000edc0, 2)],
                            'memsysctl': [hex(x) for x in d.read(0xe001e000, 6)]}
        report['clock_before'] = clock_sample(d)
        save(report)
        if not args.коротко:
            for i, (grid, steps) in enumerate(cases()):
                for variant in (0, 1):
                    report['checks'].append(run_case(d, box, grid, steps, variant))
                if (i+1) % 20 == 0:
                    save(report)
                    print(f'Проверено {2*(i+1)} сравнений N6', flush=True)
        for dt in (4096, 8191):
            for steps in (1, 64):
                grid = replace(example(64, 64), dt=dt)
                for variant in (0, 1):
                    row = run_case(d, box, grid, steps, variant, True)
                    report['benchmarks'].append(row)
                    save(report)
                    print(f'{row["variant"]}: dt={dt}, {steps} шагов, '
                          f'{row["median_cycles_per_step"]:.1f} тактов/шаг, '
                          f'{row["median_ns_per_step"]/1000:.3f} мкс/шаг', flush=True)
        report['clock_after'] = clock_sample(d)
        report['validation_complete'] = len(report['checks']) == 460
        report['complete'] = True
        report['final_mailbox'] = d.read(box, 21)
        report['power_gpio'] = {'gpiob_registers': [hex(x) for x in d.read(0x56020400, 7)]}
        report['final_state'] = 'Собственная программа ожидает команду, D-cache выключен, процессор работает'
        save(report)
    except Exception as error:
        report['error'] = str(error)
        save(report)
        raise
    finally:
        d.close()

if __name__ == '__main__':
    main()

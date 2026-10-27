#!/usr/bin/env python3
"""Измерить обычный C и NEON на сетке; сравнить с моделью, не с большим RTL."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import struct
import subprocess

from benchmark import machine
from dpu import ONE, ROOT, frame
from grid_model import Fabric, Grid, example, reference, simulate
from jtag_runtime import clock_sample, exchange

SOURCES = ('scripts/grid_cpu.h', 'scripts/grid_cpu.c', 'scripts/grid_cpu_main.c',
           'scripts/grid_benchmark.py')


def digest(paths):
    return hashlib.sha256(b''.join(p.encode() + b'\0' + (ROOT / p).read_bytes() for p in paths)).hexdigest()


def compile_c(directory, sanitizer=False):
    directory.mkdir(parents=True, exist_ok=True)
    flags = ['-std=c11', '-O3', '-mcpu=native', '-fno-lto', '-Wall', '-Wextra', '-Werror']
    if sanitizer:
        flags += ['-fsanitize=undefined', '-fno-sanitize-recover=all']
    objects = []
    for name, definitions in (('scalar', ['-DGRID_RUN=grid_scalar', '-fno-vectorize', '-fno-slp-vectorize']),
                              ('neon', ['-DGRID_RUN=grid_neon', '-DGRID_NEON=1']), ('main', [])):
        source = ROOT / ('scripts/grid_cpu_main.c' if name == 'main' else 'scripts/grid_cpu.c')
        obj = directory / (name + '.o')
        subprocess.run(['clang', *flags, *definitions, '-c', str(source), '-o', str(obj)], check=True)
        if name == 'neon' and not sanitizer:
            assembly = directory / 'neon.s'
            subprocess.run(['clang', *flags, *definitions, '-S', str(source), '-o', str(assembly)], check=True)
            # Умножения 32×32 с расширением и верхние половины векторных регистров.
            code = assembly.read_text()
            if 'smull.2d' not in code or 'smull2.2d' not in code:
                raise AssertionError('В машинном коде не найдены векторные знаковые произведения')
        objects.append(str(obj))
    binary = directory / 'grid_benchmark'
    subprocess.run(['clang', *flags, *objects, '-o', str(binary)], check=True)
    return binary, flags


def fixture(grid, steps):
    grid.validate()
    planes = (*grid.state, *grid.east, *grid.south, *grid.capacity_inverse, *grid.source)
    return struct.pack('<5I', 0x47524944, grid.width, grid.height, steps, grid.dt) + struct.pack(
        f'<{len(planes)}i', *planes)


def expected(grid, steps):
    state, completed, fault = grid.state, 0, 0
    for _ in range(steps):
        state, fault = reference(replace(grid, state=state))
        if fault:
            break
        completed += 1
    return state, completed, fault


def run_c(binary, grid, steps, variant, timing=False, expectation=None):
    raw = fixture(grid, steps)
    input_path, output_path = binary.parent / 'input.bin', binary.parent / 'output.bin'
    input_path.write_bytes(raw)
    report = json.loads(subprocess.check_output([str(binary), str(input_path), str(output_path),
                                                str(variant), str(int(timing))], text=True))
    data = output_path.read_bytes()
    if len(data) != 8 + len(grid.state) * 4:
        raise AssertionError('Неполный ответ C')
    completed, fault = struct.unpack_from('<2I', data)
    state = struct.unpack_from(f'<{len(grid.state)}i', data, 8)
    reference_result = expectation if expectation is not None else expected(grid, steps)
    if (state, completed, fault) != reference_result or (completed, fault) != (report['completed'], report['fault']):
        raise AssertionError(f'C расходится с эталоном: {grid.width}×{grid.height}, '
                             f'dt={grid.dt}, {steps} шагов, вариант {variant}')
    report.update(input_sha256=hashlib.sha256(raw).hexdigest(),
                  state_sha256=hashlib.sha256(data[8:]).hexdigest())
    if timing:
        report['median_ns_per_step'] = statistics.median(report['samples_ns'])
        report['ns_per_state_step'] = report['median_ns_per_step'] / len(state)
        report['state_updates_per_second'] = len(state) * 1e9 / report['median_ns_per_step']
    return report


def cases():
    for w, h in ((1, 1), (1, 7), (9, 1), (2, 3), (5, 7), (11, 5), (16, 9)):
        for dt in (1, 4096, 8191, 65536, 65537, 1 << 30):
            for steps in (0, 1, 4):
                yield replace(example(w, h), dt=dt), steps
    for p in range(31):
        yield replace(example(9, 3), dt=1 << p), 3
    maximum, minimum = (1 << 31) - 1, -(1 << 31)
    rng = random.Random(20261018)
    for k in range(48):
        n = 27
        limit = maximum if k % 2 else 8 * ONE
        fields = [tuple(rng.randint(-limit, limit) for _ in range(n)) for _ in range(5)]
        yield Grid(9, 3, *fields, rng.randrange(1, 1 << 31)), 3
    # Ошибка в каждой позиции, включая все четыре векторные полосы и скалярные края.
    for i in range(18):
        q = [0] * 18
        q[i] = maximum
        yield Grid(9, 2, (ONE,) * 18, (0,) * 18, (0,) * 18, (ONE,) * 18, tuple(q), ONE), 3
    yield Grid(9, 1, (minimum,) * 9, (0,) * 9, (0,) * 9,
               (ONE,) * 9, (maximum,) * 9, 2 * ONE), 1
    yield Grid(9, 1, (maximum - 2 * ONE,) * 9, (0,) * 9, (0,) * 9,
               (ONE,) * 9, (ONE,) * 9, ONE), 4
    yield Grid(2, 1, (1, 0), (ONE // 4, 0), (0, 0), (ONE, ONE), (0, 0), ONE), 1
    yield Grid(9, 1, (minimum, maximum) + (0,) * 7, (0,) * 9, (0,) * 9,
               (ONE,) * 9, (0,) * 9, ONE), 1
    yield Grid(9, 1, tuple((i % 2) * 2 * ONE for i in range(9)), (maximum,) * 9,
               (0,) * 9, (ONE,) * 9, (0,) * 9, ONE), 1
    yield Grid(5, 3, tuple((x + y) * ONE for y in range(3) for x in range(5)),
               (maximum,) * 15, (maximum,) * 15, (ONE,) * 15, (0,) * 15, ONE), 1
    yield Grid(9, 1, (0,) * 9, (0,) * 9, (0,) * 9,
               (maximum,) * 9, (2 * ONE,) * 9, ONE), 1


def verify(binary):
    count = 0
    for grid, steps in cases():
        answer = expected(grid, steps)
        for variant in (0, 1):
            run_c(binary, grid, steps, variant, expectation=answer)
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--плата', action='store_true', help='Прочитать частоту малого ядра для условного пересчёта')
    parser.add_argument('--только-проверка', action='store_true')
    parser.add_argument('--отчёт', type=Path, default=ROOT / 'reports/сравнение-сетки.json')
    args = parser.parse_args()
    directory = ROOT / 'build/grid_cpu'
    binary, flags = compile_c(directory)
    sanitized, _ = compile_c(directory / 'ubsan', True)
    checks = {'release': verify(binary), 'undefined_behavior_sanitizer': verify(sanitized)}
    print(f'УСПЕХ: побитовые проверки C/NEON: {checks}', flush=True)
    report = dict(kind='Измерение CPU и прогноз по программной модели; большого RTL нет',
                  date=datetime.now(timezone.utc).isoformat(), machine=machine(), compiler_flags=flags,
                  scalar_extra_flags=['-fno-vectorize', '-fno-slp-vectorize'], cpu_threads=1,
                  vectorization='Явные NEON, четыре int32; smull.2d/smull2.2d проверены в ассемблере',
                  source_sha256=digest(SOURCES), source_files=list(SOURCES), checks=checks,
                  timing='CLOCK_MONOTONIC_RAW; 8 прогревов; 9 серий >=30 мс; медиана; '
                         'копирование исходного поля, выделение памяти, контрольные суммы и ввод-вывод вне интервала',
                  experiments=[])
    if args.плата:
        exchange([(frame(3, 0), 0x44505532)], log_path=ROOT / 'reports/сетка-проверка-ядра.log')
        report['small_core_clock_before'] = clock_sample(ROOT / 'reports/сетка-частота-до.log')
        print('Частота малого ядра прочитана. Конфигурация и состояния не записывались.', flush=True)
    if not args.только_проверка:
        fabric = Fabric(lanes=4, banks=4, multipliers=3, slots=32)
        for size, dt in ((64, 4096), (128, 4096), (128, 8191)):
            grid = replace(example(size, size), dt=dt)
            modeled = simulate(grid, fabric)
            assert (modeled['state'], modeled['completed'], modeled['fault']) == expected(grid, 1)
            for steps in (1, 64):
                answer = expected(grid, steps)
                row = dict(width=size, height=size, dt_raw=dt, steps=steps, seed=18,
                           model_cycles_per_step=modeled['cycles'], fabric=asdict(fabric), cpu={})
                for variant, name in ((0, 'scalar'), (1, 'neon')):
                    row['cpu'][name] = run_c(binary, grid, steps, variant, True, answer)
                    print(f'{size}×{size}, dt={dt}, {steps} шагов, {name}: '
                          f'{row["cpu"][name]["median_ns_per_step"] / 1000:.3f} мкс/шаг', flush=True)
                report['experiments'].append(row)
    if args.плата:
        report['small_core_clock_after'] = clock_sample(ROOT / 'reports/сетка-частота-после.log')
        frequency = report['small_core_clock_before']['midpoint_hz']
        for row in report['experiments']:
            row['projected_fpga_ns_per_step'] = row['model_cycles_per_step'] * 1e9 / frequency
            row['cpu_neon_over_projected_fpga_time'] = (
                row['cpu']['neon']['median_ns_per_step'] / row['projected_fpga_ns_per_step'])
            row['break_even_fpga_hz'] = row['model_cycles_per_step'] * 1e9 / row['cpu']['neon']['median_ns_per_step']
    report['model_sha256'] = digest(('scripts/grid_model.py', 'scripts/dpu.py'))
    args.отчёт.parent.mkdir(parents=True, exist_ok=True)
    args.отчёт.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'Отчёт: {args.отчёт}')


if __name__ == '__main__':
    main()

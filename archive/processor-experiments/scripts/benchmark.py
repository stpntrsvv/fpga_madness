#!/usr/bin/env python3
"""Сравнение оптимизированного C и автономного ядра с одинаковой арифметикой."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import platform
from pathlib import Path
import random
import statistics
import subprocess

from dpu import MODELS, ROOT, SIGNATURE, frame, hardware, image
from jtag_runtime import clock_sample


HEADER = """#include <stdint.h>
typedef struct { int32_t x[4], p[8], dt; } Input;
typedef struct { int32_t x[4]; uint32_t completed, fault; } Result;
typedef Result (*Runner)(const Input *, uint32_t);
#define MODEL_COUNT 4
extern Runner const runners[MODEL_COUNT];
"""


def generate(models):
    """Развернуть граф в выражения C; выбор операции отсутствует внутри шага."""
    code = ['#include "models.h"', '#include <limits.h>',
            'static int fits(int64_t x) { return x >= INT32_MIN && x <= INT32_MAX; }',
            # Деление и остаток определены стандартом C и для отрицательных значений.
            'static int64_t floor16(int64_t x) { return x / 65536 - (x < 0 && x % 65536 != 0); }',
            # Без сдвига отрицательного знакового числа и без переполнения при смене знака.
            'static int64_t floor_shift32(int32_t x, unsigned shift) {',
            '    return x >= 0 ? (int64_t)((uint32_t)x >> shift)',
            '        : -INT64_C(1) - (int64_t)((uint32_t)(-(int64_t)x - 1) >> shift);',
            '}']
    for index, model in enumerate(models):
        model.validate()
        def source(s):
            if s < 4:
                return f'(int64_t)r.x[{s}]'
            if s < 12:
                return f'(int64_t)input->p[{s - 4}]'
            return f'n{s - 12}'

        # Выбор трёх вариантов до цикла; внутри шага нет проверки вида dt.
        for mode in ('generic', 'right', 'left'):
            code += [f'static Result model_{index}_{mode}(const Input *input, uint32_t steps, unsigned shift) {{',
                     '    (void)shift;',
                     '    Result r = {{input->x[0], input->x[1], input->x[2], input->x[3]}, 0, 0};',
                     '    for (; r.completed < steps; ++r.completed) {']
            for i, (op, a, b) in enumerate(model.nodes):
                x, y = source(a), source(b)
                expr = (x, f'{x} + {y}', f'{x} - {y}', f'floor16({x} * {y})')[op]
                code += [f'        int64_t n{i} = {expr};',
                         f'        if (!fits(n{i})) {{ r.fault = 3; return r; }}']
            for i, s in enumerate(model.derivatives):
                increment = {'generic': f'floor16((int64_t)input->dt * {source(s)})',
                             'right': f'floor_shift32((int32_t)({source(s)}), shift)',
                             # Произведение определено C и для отрицательного операнда.
                             'left': f'{source(s)} * (INT64_C(1) << shift)'}[mode]
                code += [f'        int64_t next{i} = (int64_t)r.x[{i}] + ({increment});',
                         f'        if (!fits(next{i})) {{ r.fault = 3; return r; }}']
            for i in range(len(model.states)):
                code += [f'        r.x[{i}] = (int32_t)next{i};']
            code += ['    }', '    return r;', '}']
        code += [f'Result model_{index}(const Input *input, uint32_t steps) {{',
                 '    uint32_t bits = (uint32_t)input->dt;',
                 '    if (input->dt > 0 && (bits & (bits - 1)) == 0) {',
                 '        unsigned power = (unsigned)__builtin_ctz(bits);',
                 f'        if (power <= 16) return model_{index}_right(input, steps, 16 - power);',
                 f'        return model_{index}_left(input, steps, power - 16);',
                 '    }', f'    return model_{index}_generic(input, steps, 0);', '}']
    code += ['Runner const runners[MODEL_COUNT] = {' + ', '.join(
        f'model_{i}' for i in range(len(models))) + '};']
    return '\n'.join(code) + '\n'


def compile_c(directory, models, sanitizer=False):
    directory.mkdir(parents=True, exist_ok=True)
    header, source = directory / 'models.h', directory / 'models.c'
    header.write_text(HEADER.replace('#define MODEL_COUNT 4', f'#define MODEL_COUNT {len(models)}'))
    source.write_text(generate(models))
    flags = ['-std=c11', '-O3', '-mcpu=native', '-fno-lto', '-Wall', '-Wextra', '-Werror']
    if sanitizer:
        flags += ['-fsanitize=undefined', '-fno-sanitize-recover=all']
    objects = []
    # Отдельные единицы трансляции: повторный вызов решателя нельзя удалить как константу.
    for i, path in enumerate((ROOT / 'scripts/benchmark.c', source)):
        obj = directory / f'{i}.o'
        subprocess.run(['clang', *flags, '-I', str(directory), '-c', str(path), '-o', str(obj)], check=True)
        objects.append(str(obj))
    binary = directory / 'benchmark'
    subprocess.run(['clang', *flags, *objects, '-o', str(binary)], check=True)
    return binary, flags, hashlib.sha256((header.read_text() + source.read_text() +
        (ROOT / 'scripts/benchmark.c').read_text()).encode()).hexdigest()


def run_c(binary, index, model, steps, check_only=True):
    args = [index, steps, model.dt, *model.states, *([0] * (4 - len(model.states))),
            *model.parameters, *([0] * (8 - len(model.parameters))), int(check_only)]
    output = subprocess.check_output([str(binary), *map(str, args)], text=True)
    result = next(line.split()[1:] for line in output.splitlines() if line.startswith('RESULT '))
    completed, fault, *states = map(int, result)
    expected, expected_steps, expected_fault = model.evolve(steps)
    if (states[:len(expected)], completed, fault) != (expected, expected_steps, expected_fault):
        raise AssertionError(f'C расходится с Python: {model.name}, {steps}, {output}')
    samples = [float(line.split()[2]) for line in output.splitlines() if line.startswith('SAMPLE ')]
    repeats = [int(line.split()[1]) for line in output.splitlines() if line.startswith('REPEATS ')]
    return {'states_raw': states[:len(expected)], 'completed': completed, 'fault': fault,
            'samples_ns': samples, 'repeats_per_sample': repeats[0] if repeats else None, 'output': output}


def verify(binary, models):
    rng = random.Random(20261017)
    count = 0
    for index, model in enumerate(models):
        for steps in (0, 1, 16, 256, 1024):
            run_c(binary, index, model, steps)
            count += 1
        for power in range(31):
            run_c(binary, index, replace(model, dt=1 << power), 8)
            count += 1
            if power:
                run_c(binary, index, replace(model, dt=(1 << power) + 1), 8)
                count += 1
        # Проверяется порядок округления, в том числе на отрицательных входах,
        # и отмена переходов при переполнении с разными исходными данными.
        for i in range(24):
            limit = (1 << 31) - 1 if i % 3 == 0 else 4 * 65536
            changed = replace(model, states=tuple(rng.randint(-limit, limit) for _ in model.states),
                              parameters=tuple(rng.randint(-limit, limit) for _ in model.parameters),
                              dt=rng.randint(1, 65536))
            run_c(binary, index, changed, 8)
            count += 1
    return count


def machine():
    def command(*args):
        result = subprocess.run(args, text=True, capture_output=True)
        return result.stdout.strip() if result.returncode == 0 else 'Недоступно в песочнице'
    return {'chip': command('sysctl', '-n', 'machdep.cpu.brand_string'),
            'model': command('sysctl', '-n', 'hw.model'),
            'logical_cpus': command('sysctl', '-n', 'hw.ncpu'),
            'memory_bytes': command('sysctl', '-n', 'hw.memsize'),
            'os': platform.platform(), 'compiler': command('clang', '--version')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--плата', action='store_true', help='Также загрузить модели в уже настроенное ядро и измерить JTAG')
    parser.add_argument('--отчёт', type=Path, default=ROOT / 'reports/сравнение-автономное-ядро.json')
    args = parser.parse_args()
    print('Сравнение с автономным ядром FPGA и зарегистрированными DSP; TCK используется для обмена.', flush=True)
    models = list(MODELS.values())
    directory = ROOT / 'build/benchmark'
    # Отдельная сборка с проверкой неопределённого поведения; её время не сравнивается.
    checked_binary, _, _ = compile_c(directory / 'checked', models, sanitizer=True)
    verified = verify(checked_binary, models)
    binary, flags, digest = compile_c(directory / 'release', models)
    verified += verify(binary, models)
    print(f'Проверки C: {verified}, включая сборку с проверкой неопределённого поведения', flush=True)
    report = {'date_utc': datetime.now(timezone.utc).isoformat(), 'machine': machine(),
              'flags': flags, 'source_sha256': digest, 'verification_cases': verified,
              'validation_seed': 20261017, 'threads': 1, 'steps': 256,
              'models': {}}
    bitstream = ROOT / 'build/fpga_madness.bit'
    if bitstream.exists():
        report['local_bitstream_sha256'] = hashlib.sha256(bitstream.read_bytes()).hexdigest()
    calibration = None
    if args.плата:
        calibration = clock_sample(ROOT / 'reports/частота-ядра.log')
        print(f'Частота ядра: {calibration["minimum_hz"] / 1e6:.3f}–'
              f'{calibration["maximum_hz"] / 1e6:.3f} МГц', flush=True)
    elif args.отчёт.exists():
        previous = json.loads(args.отчёт.read_text())
        if previous.get('local_bitstream_sha256') == report.get('local_bitstream_sha256'):
            calibration = previous.get('fpga_clock')
    report['fpga_clock'] = calibration
    for index, model in enumerate(models):
        result = run_c(binary, index, model, 256, check_only=False)
        median = statistics.median(result['samples_ns'])
        result.update(input=asdict(model), cpu_median_ns=median, cycles_per_step=model.cycles_per_step())
        report['models'][model.name] = result
        if calibration:
            nominal = model.cycles_per_step() * 256 / calibration['midpoint_hz'] * 1e9
            result.update(fpga_nominal_ns=nominal, nominal_ratio=nominal / median,
                          fpga_minimum_ns=model.cycles_per_step() * 256 / calibration['maximum_hz'] * 1e9,
                          fpga_maximum_ns=model.cycles_per_step() * 256 / calibration['minimum_hz'] * 1e9)
            print(f'{model.name}: C {median / 256:.2f} нс/шаг; FPGA по счётчику и частоте '
                  f'{nominal / 256:.1f} нс/шаг; отношение {nominal / median:.1f}', flush=True)
        else:
            print(f'{model.name}: C {median / 256:.2f} нс/шаг; FPGA '
                  f'{model.cycles_per_step()} тактов/шаг, частота ещё не измерена', flush=True)
        if args.плата:
            vectors = [(cmd, 0, 4) for cmd in image(model)]
            load_count = len(vectors)
            vectors += [(frame(3, 0), SIGNATURE, 4),
                        (frame(4, data=256), 0, model.cycles_per_step() * 256 + 8),
                        (frame(3, 1), 2, 4), (frame(3, 2), 256, 4),
                        (frame(3, 3), model.cycles_per_step() * 256, 4)]
            vectors += [(frame(3, 0x100 + i), x & 0xFFFFFFFF, 4)
                        for i, x in enumerate(result['states_raw'])]
            measured = hardware(vectors, measure=True)
            measured['load_exchanges'] = load_count
            durations = [after - before for _, _, before, after in measured['records']]
            measured['load_ms'] = sum(durations[:load_count])
            measured['run_exchange_ms'] = durations[load_count + 1]
            measured['read_ms'] = sum(durations[load_count + 2:])
            result['jtag'] = measured
            print(f'  JTAG: процесс {measured["wall_seconds"]:.3f} с; '
                  f'RUN с опросом {measured["run_exchange_ms"]} мс; результаты совпали', flush=True)
    args.отчёт.parent.mkdir(parents=True, exist_ok=True)
    args.отчёт.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'Отчёт: {args.отчёт}')


if __name__ == '__main__':
    main()

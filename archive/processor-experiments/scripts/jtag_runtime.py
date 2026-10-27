"""Обмен DPU2 с автономной тактовой областью; ожидание ответа по признаку готовности."""

from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
SIGNATURE = 0x44505532


def exchange(vectors, *, pauses=None, wait_runs=None, log_path=None):
    """Паузы задаются в мс перед обменом: команда sleep не выдаёт TCK."""
    pauses = pauses or {}
    wait_runs = set(wait_runs or ())
    script = ['init', f'source {{{ROOT / "scripts/jtag_sanity.tcl"}}}',
              'irscan zynq_pl.bs 0x02', 'runtest 32',
              'proc exchange {low high} {',
              '  drscan zynq_pl.bs 32 $low 32 $high',
              '  runtest 8',
              '  for {set attempt 0} {$attempt < 100} {incr attempt} {',
              '    set response [drscan zynq_pl.bs 32 0 32 0]',
              '    set signature [expr "0x[lindex $response 1]"]',
              '    runtest 8',
              f'    if {{$signature == 0x{SIGNATURE:08x}}} {{ return $response }}',
              '    if {$signature != 0x42555359} { error "Неверная версия протокола DPU2: $response" }',
              '  }', '  error "Нет ответа независимой тактовой области"', '}']
    for index, (request, expected) in enumerate(vectors):
        if index in pauses:
            script += [f'sleep {int(pauses[index])}']
        script += ['set before [ms]',
                   f'set result [exchange 0x{request & 0xffffffff:08x} 0x{request >> 32:08x}]',
                   'set value [expr "0x[lindex $result 0]"]']
        if index in wait_runs:
            script += ['set deadline [expr {[ms] + 60000}]',
                       'while {1} {', '  set status [exchange 0 0xd5030001]',
                       '  set status_value [expr "0x[lindex $status 0]"]',
                       '  if {($status_value & 1) == 0} { break }',
                       '  if {[ms] > $deadline} { error "Превышено время расчёта" }',
                       '  sleep 1', '}']
        script += ['set after [ms]']
        if expected is not None:
            script += [f'if {{$value != 0x{expected & 0xffffffff:08x}}} {{ error "Обмен {index}: $result" }}']
        script += [f'echo "DPU2 {index} $value $before $after"']
    script += [f'echo "УСПЕХ: {len(vectors)} обменов DPU2"', 'shutdown']
    with tempfile.TemporaryDirectory(prefix='dpu2-jtag-') as directory:
        path = Path(directory) / 'exchange.tcl'
        path.write_text('\n'.join(script) + '\n')
        started = time.perf_counter()
        result = subprocess.run(['openocd', '-f', 'interface/cmsis-dap.cfg', '-f',
                                 str(ROOT / 'scripts/jtag_chain.tcl'), '-f', str(path)],
                                capture_output=True, text=True)
        elapsed = time.perf_counter() - started
    output = result.stdout + result.stderr
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(output)
    if result.returncode:
        raise RuntimeError(output)
    records = [tuple(map(int, match)) for match in re.findall(
        r'^DPU2 (\d+) (\d+) (\d+) (\d+)$', output, re.MULTILINE)]
    if len(records) != len(vectors) or 'УСПЕХ:' not in output:
        raise RuntimeError(f'Неполный обмен DPU2:\n{output}')
    return {'records': records, 'wall_seconds': elapsed, 'output': output}


def clock_sample(log_path=None):
    from dpu import frame
    vectors = [(frame(3, 5), None), (frame(3, 6), None)] * 2
    result = exchange(vectors, pauses={2: 2000}, log_path=log_path)
    records = result['records']
    first = records[0][1] | (records[1][1] << 32)
    second = records[2][1] | (records[3][1] << 32)
    ticks = (second - first) & ((1 << 64) - 1)
    minimum_ms = records[2][2] - records[0][3]
    maximum_ms = records[2][3] - records[0][2]
    if minimum_ms <= 0 or not ticks:
        raise AssertionError('Независимый счётчик не продвигается')
    return {'ticks': ticks, 'minimum_interval_ms': minimum_ms, 'maximum_interval_ms': maximum_ms,
            'minimum_hz': ticks * 1000 / maximum_ms, 'maximum_hz': ticks * 1000 / minimum_ms,
            'midpoint_hz': ticks * 2000 / (minimum_ms + maximum_ms)}

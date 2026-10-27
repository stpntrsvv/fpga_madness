#!/usr/bin/env python3
"""Потактовый кандидат тепловой сетки; не RTL и не замер FPGA."""
import argparse
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
import heapq
import hashlib
from itertools import product
import json
from pathlib import Path
import random

from dpu import ONE, ROOT, checked


@dataclass(frozen=True)
class Grid:
    width: int
    height: int
    state: tuple
    east: tuple
    south: tuple
    capacity_inverse: tuple
    source: tuple
    dt: int

    def validate(self):
        if self.width < 1 or self.height < 1 or not 0 < self.dt < (1 << 31):
            raise ValueError('Недопустимые размеры или dt')
        for values in (self.state, self.east, self.south, self.capacity_inverse, self.source):
            if len(values) != self.width * self.height:
                raise ValueError('Длина поля не совпадает с размером сетки')
            for value in values:
                checked(value)


def example(width, height, seed=18):
    rng = random.Random(seed)
    n = width * height
    return Grid(width, height, tuple(rng.randrange(ONE) for _ in range(n)),
                tuple(rng.randrange(ONE // 4) for _ in range(n)),
                tuple(rng.randrange(ONE // 4) for _ in range(n)),
                tuple(rng.randrange(ONE // 2, ONE) for _ in range(n)),
                (0,) * n, ONE // 16)


def reference(grid):
    """Независимый целочисленный расчёт: полные массивы связей, затем балансы."""
    grid.validate()
    w, h = grid.width, grid.height
    e, s = [0] * (w * h), [0] * (w * h)
    try:
        for y in range(h):
            for x in range(w):
                i = y * w + x
                if x + 1 < w:
                    delta = checked(grid.state[i + 1] - grid.state[i])
                    e[i] = checked((grid.east[i] * delta) >> 16)
                if y + 1 < h:
                    delta = checked(grid.state[i + w] - grid.state[i])
                    s[i] = checked((grid.south[i] * delta) >> 16)
        trial = []
        for y in range(h):
            for x in range(w):
                i = y * w + x
                value = checked(e[i] + s[i])
                value = checked(value - (e[i - 1] if x else 0))
                value = checked(value - (s[i - w] if y else 0))
                value = checked(value + grid.source[i])
                derivative = checked((grid.capacity_inverse[i] * value) >> 16)
                # Как в малом ядре, проверяется конечная сумма, не отдельное приращение.
                trial.append(checked(grid.state[i] + ((grid.dt * derivative) >> 16)))
    except OverflowError:
        return grid.state, 3
    return tuple(trial), 0


@dataclass(frozen=True)
class Fabric:
    lanes: int = 4
    multipliers: int = 4
    banks: int = 4
    slots: int = 16
    memory_latency: int = 1
    multiply_latency: int = 4
    add_latency: int = 2
    shift_dt: bool = True
    write_pause_period: int = 0
    write_pause_length: int = 0

    def validate(self):
        if min(self.lanes, self.multipliers, self.banks, self.slots,
               self.memory_latency, self.multiply_latency, self.add_latency) < 1:
            raise ValueError('Число ресурсов и задержки должны быть положительными')
        if self.write_pause_period < 0 or not 0 <= self.write_pause_length <= max(0, self.write_pause_period - 1):
            raise ValueError('Пауза записи должна оставлять хотя бы один рабочий такт')


@dataclass
class Token:
    index: int
    x: int
    y: int
    lane: int
    values: dict
    started: set = field(default_factory=set)
    east_stored: bool = False
    south_stored: bool = False
    written: bool = False


# У каждого простого узла свой конвейер; умножители совместно используются внутри тракта.
OPERATIONS = (
    ('de', 'add', ('east_t', 'center'), lambda a, b: a - b),
    ('ds', 'add', ('south_t', 'center'), lambda a, b: a - b),
    ('e', 'mul', ('ge', 'de'), lambda a, b: (a * b) >> 16),
    ('s', 'mul', ('gs', 'ds'), lambda a, b: (a * b) >> 16),
    ('b0', 'add', ('e', 's'), lambda a, b: a + b),
    ('b1', 'add', ('b0', 'west'), lambda a, b: a - b),
    ('b2', 'add', ('b1', 'north'), lambda a, b: a - b),
    ('b3', 'add', ('b2', 'q'), lambda a, b: a + b),
    ('derivative', 'mul', ('ci', 'b3'), lambda a, b: (a * b) >> 16),
    # Полное приращение может не помещаться в 32 бита до сложения с состоянием.
    ('increment', 'wide_mul', ('dt', 'derivative'), lambda a, b: (a * b) >> 16),
    ('next', 'add', ('center', 'increment'), lambda a, b: a + b),
)
CONSUMERS = {name: frozenset(operation for operation, _, inputs, _ in OPERATIONS if name in inputs)
             for _, _, inputs, _ in OPERATIONS for name in inputs}


class Epoch:
    """Одна эпоха с ограниченными буферами, портами и событиями завершения стадий."""
    def __init__(self, grid, fabric):
        grid.validate()
        fabric.validate()
        self.grid, self.fabric = grid, fabric
        self.clock = self.serial = self.read_position = self.position = 0
        self.events, self.active = [], []
        self.temperature, self.horizontal, self.vertical = {}, {}, {}
        self.vertical_reading = set()
        self.trial = [None] * len(grid.state)
        self.fault = None
        self.stats = Counter()
        self.used = Counter()
        self.field_peaks = Counter()
        self.line_reads = set()
        self.line_written = set()

    def later(self, delay, action):
        self.serial += 1
        heapq.heappush(self.events, (self.clock + delay, self.serial, action))

    def port(self, plane, x, operation):
        key = plane, x % self.fabric.banks, operation
        if self.used[key]:
            return False
        self.used[key] += 1
        return True

    def result(self, token, name, value, narrow=True):
        try:
            token.values[name] = checked(value) if narrow else value
        except OverflowError:
            self.fault = (token.index, name)

    def receive_temperature(self, index, value):
        y, x = divmod(index, self.grid.width)
        key = 'line', y % 2, x % self.fabric.banks
        self.used[key] += 1
        assert self.used[key] <= 2
        self.stats['line_writes'] += 1
        self.line_written.add(index)
        self.temperature[index] = value
        assert len(self.temperature) <= 2 * self.grid.width
        self.stats['max_temperature_words'] = max(self.stats['max_temperature_words'], len(self.temperature))

    def window_ports(self, indices):
        # У двух строчных банков по два порта; общий отсчёт рассылается соседним точкам.
        # Только что поступивший отсчёт можно подать напрямую, без коллизии чтения/записи BRAM.
        forwarded = set(indices) & self.line_written
        new_reads = set(indices) - self.line_reads - self.line_written
        needed = Counter(('line', (i // self.grid.width) % 2, (i % self.grid.width) % self.fabric.banks)
                         for i in new_reads)
        if any(self.used[key] + count > 2 for key, count in needed.items()):
            self.stats['line_port_denials'] += 1
            return False
        self.used.update(needed)
        self.line_reads.update(new_reads)
        self.stats['line_reads'] += len(new_reads)
        self.stats['line_forward_words'] += len(forwarded)
        return True

    def input_reads(self):
        w = self.grid.width
        for _ in range(self.fabric.lanes):
            if self.read_position == len(self.grid.state):
                break
            i = self.read_position
            y, x = divmod(i, w)
            if y > min(self.grid.height - 1, self.position // w + 1):
                break
            if not self.port('state', x, 'read'):
                self.stats['input_port_denials'] += 1
                break
            value = self.grid.state[i]
            self.later(self.fabric.memory_latency, lambda i=i, value=value: self.receive_temperature(i, value))
            self.read_position += 1
            self.stats['state_reads'] += 1
            if x == w - 1:
                break

    def issue_windows(self):
        g, f = self.grid, self.fabric
        for _ in range(f.lanes):
            if self.position == len(g.state):
                break
            i = self.position
            y, x = divmod(i, g.width)
            lane = x % f.lanes
            if sum(t.lane == lane for t in self.active) >= f.slots:
                self.stats['window_slot_stalls'] += 1
                break
            east = i + 1 if x + 1 < g.width else i
            south = i + g.width if y + 1 < g.height else i
            if not all(j in self.temperature for j in (i, east, south)):
                self.stats['window_data_stalls'] += 1
                break
            # Четыре карты лежат в отдельных массивах с одинаковой раскладкой банков.
            if self.used[('ge', x % f.banks, 'read')]:
                self.stats['parameter_port_denials'] += 1
                break
            if not self.window_ports((i, east, south)):
                break
            for plane in ('ge', 'gs', 'ci', 'q'):
                assert self.port(plane, x, 'read')
            self.stats['parameter_reads'] += 4
            token = Token(i, x, y, lane, dict(dt=g.dt))
            if x == g.width - 1:
                token.values['e'] = 0
                token.started.update(('de', 'e'))
                token.east_stored = True
            if y == g.height - 1:
                token.values['s'] = 0
                token.started.update(('ds', 's'))
                token.south_stored = True
            if x == 0:
                token.values['west'] = 0
            if y == 0:
                token.values['north'] = 0
            params = dict(ge=g.east[i], gs=g.south[i], ci=g.capacity_inverse[i], q=g.source[i],
                          center=self.temperature[i], east_t=self.temperature[east], south_t=self.temperature[south])
            self.later(f.memory_latency, lambda token=token, params=params: token.values.update(params))
            self.active.append(token)
            self.stats['max_active'] = max(self.stats['max_active'], len(self.active))
            self.position += 1
            first_kept = (self.position // g.width) * g.width
            if x == g.width - 1:
                self.temperature = {j: v for j, v in self.temperature.items() if j >= first_kept}
                break

    def links(self, token):
        f = self.fabric
        v = token.values
        if 'e' in v and not token.east_stored:
            if len(self.horizontal) < f.lanes * (f.slots + 1):
                self.horizontal[token.index] = v['e']
                token.east_stored = True
        if 'west' not in v and token.index - 1 in self.horizontal:
            west = self.horizontal.pop(token.index - 1)
            token.started.add('west')
            self.later(1, lambda token=token, west=west: token.values.update(west=west))
        slot = token.y % 2, token.x
        if 's' in v and not token.south_stored and 'store_s' not in token.started:
            if slot not in self.vertical and self.port(('flux', token.y % 2), token.x, 'write'):
                token.started.add('store_s')
                value = v['s']
                def store(token=token, slot=slot, value=value):
                    assert slot not in self.vertical
                    self.vertical[slot] = token.y, value
                    token.south_stored = True
                self.later(1, store)
                self.stats['flux_writes'] += 1
        if token.y and 'north' not in v and 'north' not in token.started:
            previous = (token.y - 1) % 2, token.x
            saved = self.vertical.get(previous)
            if (saved is not None and saved[0] == token.y - 1 and previous not in self.vertical_reading
                    and self.port(('flux', previous[0]), token.x, 'read')):
                self.vertical_reading.add(previous)
                token.started.add('north')
                def read(token=token, previous=previous, saved=saved):
                    assert self.vertical.pop(previous) == saved
                    self.vertical_reading.remove(previous)
                    token.values['north'] = saved[1]
                self.later(f.memory_latency, read)
                self.stats['flux_reads'] += 1
        self.stats['max_horizontal_words'] = max(self.stats['max_horizontal_words'], len(self.horizontal))
        self.stats['max_vertical_words'] = max(self.stats['max_vertical_words'], len(self.vertical))
        assert len(self.vertical) <= 2 * self.grid.width

    def arithmetic(self, token):
        f, v = self.fabric, token.values
        for name, kind, inputs, operation in OPERATIONS:
            if name in token.started or not all(source in v for source in inputs):
                continue
            if (name == 'increment' and f.shift_dt and self.grid.dt & (self.grid.dt - 1) == 0):
                kind = 'shift'
            key = token.lane, 'multiply' if kind.endswith('mul') else name
            capacity = f.multipliers if kind.endswith('mul') else 1
            if self.used[key] == capacity:
                counter = 'multiply_denials' if kind.endswith('mul') else 'shift_denials' if kind == 'shift' else 'add_denials'
                self.stats[counter] += 1
                continue
            self.used[key] += 1
            token.started.add(name)
            value = operation(*(v[source] for source in inputs))
            latency = f.multiply_latency if kind.endswith('mul') else 1 if kind == 'shift' else f.add_latency
            self.later(latency, lambda token=token, name=name, value=value, kind=kind:
                       self.result(token, name, value, name != 'increment'))
            counter = 'multiply_issued' if kind.endswith('mul') else 'shift_issued' if kind == 'shift' else 'add_issued'
            self.stats[counter] += 1

    def write(self, token):
        if 'next' not in token.values or token.written or 'write' in token.started:
            return
        f = self.fabric
        paused = f.write_pause_period and self.clock % f.write_pause_period < f.write_pause_length
        if paused or not self.port('trial', token.x, 'write'):
            self.stats['output_stalls'] += 1
            return
        token.started.add('write')
        value = token.values['next']
        def finish(token=token, value=value):
            assert self.trial[token.index] is None
            self.trial[token.index] = value
            token.written = True
            self.stats['trial_writes'] += 1
        self.later(1, finish)

    def release_consumed(self):
        # Освобождение после последнего потребителя, без накопления всех результатов узлов.
        resident = Counter()
        for token in self.active:
            for name in tuple(token.values):
                needed = not CONSUMERS.get(name, frozenset()).issubset(token.started)
                needed |= name == 'e' and not token.east_stored
                needed |= name == 's' and not (token.south_stored or 'store_s' in token.started)
                needed |= name == 'next' and 'write' not in token.started
                if not needed:
                    del token.values[name]
                elif name != 'dt':
                    resident[name] += 2 if name == 'increment' else 1
        self.stats['max_live_words'] = max(self.stats['max_live_words'], sum(resident.values()))
        for name, count in resident.items():
            self.field_peaks[name] = max(self.field_peaks[name], count)

    def run(self):
        limit = len(self.grid.state) * (200 + self.fabric.write_pause_period) + 1000
        while self.clock < limit:
            self.used.clear()
            self.line_reads.clear()
            self.line_written.clear()
            while self.events and self.events[0][0] == self.clock:
                _, _, action = heapq.heappop(self.events)
                action()
            if self.fault:
                return self.finish(self.grid.state, 3)
            for token in self.active:
                self.links(token)
                self.arithmetic(token)
                self.write(token)
            self.active = [t for t in self.active if not (t.written and t.east_stored and t.south_stored)]
            self.issue_windows()
            self.input_reads()
            self.release_consumed()
            if self.stats['trial_writes'] == len(self.grid.state) and not self.active and not self.events:
                assert not self.horizontal and not self.vertical and not self.vertical_reading
                assert all(value is not None for value in self.trial)
                return self.finish(tuple(self.trial), 0)
            self.clock += 1
        raise RuntimeError(f'Модель не завершилась: позиция {self.position}, активных {len(self.active)}')

    def finish(self, state, fault):
        return dict(state=state, fault=fault, fault_location=self.fault, cycles=self.clock + 1,
                    stats=dict(self.stats), field_peaks=dict(self.field_peaks))


def simulate(grid, fabric, steps=1):
    if steps < 0:
        raise ValueError('Число шагов не может быть отрицательным')
    grid.validate()
    fabric.validate()
    frames = []
    completed = 0
    for _ in range(steps):
        result = Epoch(grid, fabric).run()
        frames.append({key: value for key, value in result.items() if key != 'state'})
        if result['fault']:
            break
        grid = replace(grid, state=result['state'])
        completed += 1
    return dict(state=grid.state, completed=completed, fault=frames[-1]['fault'] if frames else 0,
                cycles=sum(frame['cycles'] for frame in frames), frames=frames)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--размер', type=int, default=128)
    parser.add_argument('--шаги', type=int, default=1)
    parser.add_argument('--отчёт', type=Path, default=ROOT / 'reports/модель-сетки.json')
    args = parser.parse_args()
    grid = example(args.размер, args.размер)
    reference_state = grid.state
    reference_completed = 0
    reference_fault = 0
    for _ in range(args.шаги):
        reference_state, reference_fault = reference(replace(grid, state=reference_state))
        if reference_fault:
            break
        reference_completed += 1
    report = dict(kind='Потактовое моделирование, не измерение FPGA', width=grid.width,
                  height=grid.height, steps=args.шаги, seed=18, configurations=[],
                  model_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  arithmetic_sha256=hashlib.sha256((ROOT / 'scripts/dpu.py').read_bytes()).hexdigest())
    configurations = [('основная', Fabric(lanes=lanes, banks=lanes, multipliers=multipliers, slots=slots))
                      for lanes, multipliers, slots in product((1, 2, 4), (2, 3, 4), (16, 32))]
    configurations += [('узкие банки', Fabric(banks=1, slots=32)),
                       ('длинное чтение', Fabric(slots=32, memory_latency=3)),
                       ('запас мест', Fabric(slots=64)),
                       ('общий dt', Fabric(multipliers=3, slots=32, shift_dt=False))]
    for label, fabric in configurations:
        result = simulate(grid, fabric, args.шаги)
        assert (result['state'], result['completed'], result['fault']) == (
            reference_state, reference_completed, reference_fault)
        report['configurations'].append(dict(label=label, fabric=asdict(fabric), **result))
        print(f'{label}: {fabric.lanes} трактов, {fabric.multipliers} умножителей/тракт, '
              f'{fabric.slots} мест: {result["cycles"]} тактов; '
              f'побитовое совпадение', flush=True)
    args.отчёт.parent.mkdir(parents=True, exist_ok=True)
    args.отчёт.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'Отчёт: {args.отчёт}')


if __name__ == '__main__':
    main()

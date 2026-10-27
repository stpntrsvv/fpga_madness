"""Проверки пакетного управления и полных полей 64×64 для RTL и платы."""
from dataclasses import replace
from pathlib import Path
import random
import sys

from dpu import ONE
from grid_model import Grid, example, reference


def packet(command, address=0, data=0, words=()):
    value = sum((word & 0xffffffff) << (32 * i) for i, word in enumerate(words))
    return ((0xd5000000 | (command << 16) | address) << 1056) | ((data & 0xffffffff) << 1024) | value


def epoch_cycles(lanes):
    if lanes not in (4, 8):
        raise ValueError('Поддерживаются четыре или восемь трактов')
    return 4096//lanes + 23


def identity_checks(lanes=4):
    cycles = epoch_cycles(lanes)
    """Нулевая производная: сохранить номера точек через оба пробных снимка."""
    n = 4096
    state = tuple(range(1, n + 1))
    rows = [(packet(1), 0, 0, 0, 0), (packet(2, 16, 4096), 0, 0, 0, 0)]
    for plane, values in enumerate((state, (0,)*n, (0,)*n, (ONE,)*n, (0,)*n)):
        for start in range(0, n, 32):
            rows.append((packet(5, plane*4096 + start, 32, values[start:start+32]), 0, 0, 0, 0))
    for epoch in range(3):
        if epoch:
            rows.append((packet(4, data=1), 0, 0, 0, 1200))
            rows.extend((packet(3, a), value, 0, 0, 0) for a, value in
                        ((1, 2), (2, 1), (3, cycles), (4, cycles)))
        for start in range(0, n, 32):
            data = sum(v << (32*i) for i, v in enumerate(state[start:start+32]))
            rows.append((packet(6, start, 32), 0, data, 1, 0))
    rows.append((packet(1), 0, 0, 0, 0))
    return rows


def checks(lanes=4):
    cycles = epoch_cycles(lanes)
    rows = []

    def add(command, address=0, data=0, words=(), reply=0, output=None, wait=0):
        rows.append((packet(command, address, data, words), reply,
                     sum((v & 0xffffffff) << (32*i) for i, v in enumerate(output or ())),
                     int(output is not None), wait))

    def read_field(plane, values):
        for start in range(0, len(values), 32):
            add(6, plane * 4096 + start, 32, output=values[start:start+32])

    def load(grid):
        add(1)
        add(4, data=1, reply=0xbad00003)
        add(5, 1, 1, words=(0,), reply=0xbad00003)
        add(6, 0, 1, reply=0xbad00003)
        add(2, 16, grid.dt)
        for plane, values in enumerate((grid.state, grid.east, grid.south, grid.capacity_inverse, grid.source)):
            for start in range(0, len(values), 32):
                add(5, plane * 4096 + start, 32, values[start:start+32])
        for plane, values in enumerate((grid.state, grid.east, grid.south, grid.capacity_inverse, grid.source)):
            read_field(plane, values)

    def run(grid, steps):
        state, completed, fault = grid.state, 0, 0
        for _ in range(steps):
            state, fault = reference(replace(grid, state=state))
            if fault:
                break
            completed += 1
        add(4, data=steps, wait=1200 * (completed + bool(fault)))
        add(3, 1, reply=(2 if completed == steps else 0) | (4 if fault else 0) | (fault << 8))
        add(3, 2, reply=completed)
        add(3, 3, reply=cycles * (completed + bool(fault)))
        add(3, 4, reply=cycles if completed or fault else 0)
        read_field(0, state)
        return replace(grid, state=state)

    add(3, reply=0x47524431)
    add(3, 17, reply=4096)
    add(2, 0xffff, reply=0xbad00002)
    add(42, reply=0xbad00004)
    add(5, 0, 0, reply=0xbad00002)
    add(5, 0xfff, 2, reply=0xbad00002)
    add(6, 0x5000, 1, reply=0xbad00002)
    if lanes == 8:
        add(3, 18, reply=8)
    grid = example(64, 64)
    load(grid)
    add(5, 1, 1, (grid.state[1],))
    add(6, 1, 1, output=(grid.state[1],))
    add(2, 16, 0)
    add(4, data=1, reply=0xbad00003)
    add(2, 16, grid.dt)
    grid = run(grid, 0)
    grid = run(grid, 1)
    grid = run(grid, 3)
    # Точно исходное поле и 64 шага из C/NEON-опыта; карты уже загружены.
    grid = example(64, 64)
    for start in range(0, 4096, 32):
        add(5, start, 32, grid.state[start:start+32])
    grid = run(grid, 64)
    # Запреты управления во время длинного автономного запуска, затем отмена.
    add(4, data=100000)
    add(2, 16, 8191, reply=0xbad00001)
    add(4, data=1, reply=0xbad00001)
    add(5, 0, 1, (0,), reply=0xbad00001)
    add(6, 0, 1, reply=0xbad00001)
    add(1)
    add(3, 1)
    grid = replace(example(64, 64, seed=19), dt=8191)
    load(grid)
    run(grid, 2)
    n, maximum, minimum = 4096, (1 << 31) - 1, -(1 << 31)
    # Отказ самой последней точки после записи почти всего пробного поля.
    grid = Grid(64, 64, (0,) * (n-1) + (ONE,), (0,) * n, (0,) * n,
                (ONE,) * n, (0,) * (n-1) + (maximum,), ONE)
    load(grid)
    run(grid, 1)
    # Два принятых шага, затем переполнение третьего; все четыре полосы.
    grid = Grid(64, 64, (maximum - 2 * ONE,) * n, (0,) * n, (0,) * n,
                (ONE,) * n, (ONE,) * n, ONE)
    load(grid)
    run(grid, 4)
    # Приращение шире 32 бит допустимо после сложения с отрицательным T.
    grid = Grid(64, 64, (minimum,) * n, (0,) * n, (0,) * n,
                (ONE,) * n, (maximum,) * n, 2 * ONE)
    load(grid)
    run(grid, 1)
    # Переполнение разности в четырёх векторных полосах.
    grid = Grid(64, 64, tuple(minimum if i % 2 else maximum for i in range(n)),
                (0,) * n, (0,) * n, (ONE,) * n, (0,) * n, ONE)
    load(grid)
    run(grid, 1)
    # Отдельные отказы произведения потока, баланса и произведения invC.
    for state, ge, gs, ci, source in (
        (tuple((i % 2) * 2 * ONE for i in range(n)), (maximum,) * n, (0,) * n, (ONE,) * n, (0,) * n),
        (tuple((x+y) * ONE for y in range(64) for x in range(64)), (maximum,) * n, (maximum,) * n, (ONE,) * n, (0,) * n),
        ((0,) * n, (0,) * n, (0,) * n, (maximum,) * n, (2*ONE,) * n),
    ):
        grid = Grid(64, 64, state, ge, gs, ci, source, ONE)
        load(grid)
        run(grid, 1)
    rng = random.Random(20261018)
    grid = Grid(64, 64, tuple(rng.randrange(-ONE, ONE) for _ in range(n)),
                tuple(rng.randrange(-ONE//4, ONE//4) for _ in range(n)),
                tuple(rng.randrange(-ONE//4, ONE//4) for _ in range(n)),
                tuple(rng.randrange(-ONE, ONE) for _ in range(n)),
                tuple(rng.randrange(-ONE//64, ONE//64) for _ in range(n)), 513)
    load(grid)
    run(grid, 3)
    add(1)
    return rows


if __name__ == '__main__':
    rows = checks(int(sys.argv[2]) if len(sys.argv)>2 else 4)
    Path(sys.argv[1]).write_text(''.join(f'{p:0272x} {r:08x} {v:0256x} {c} {w}\n' for p,r,v,c,w in rows))
    print(f'Подготовлено {len(rows)} проверок пакетного ядра 64×64')

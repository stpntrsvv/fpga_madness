#!/usr/bin/env python3
"""Загружаемые локальные графы: размещение, потактовая модель и цена хранения."""
import argparse
from collections import Counter, deque
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path

from dpu import ONE, ROOT, checked
from grid_model import example, reference

EDGE_INPUTS = ('center', 'neighbor', 'coefficient')
LOCAL_INPUTS = ('center', 'ci', 'q', 'east', 'south', 'west', 'north')
OPERATIONS = {'pass': 1, 'add': 2, 'sub': 3, 'mul': 4}
NAMES = {v: k for k, v in OPERATIONS.items()}


@dataclass(frozen=True)
class Node:
    name: str
    operation: str
    a: str
    b: str = 'zero'


@dataclass(frozen=True)
class Graph:
    inputs: tuple
    nodes: tuple
    output: str

    def validate(self):
        known = set(self.inputs) | {'zero'} | {f'c{i}' for i in range(8)}
        if len(known) != len(self.inputs) + 9:
            raise ValueError('Имена входов повторяются или заняты константами')
        for n in self.nodes:
            if n.operation not in OPERATIONS or n.name in known:
                raise ValueError('Недопустимая операция или повторное имя узла')
            if n.a not in known or (n.operation != 'pass' and n.b not in known):
                raise ValueError('Источник ещё не определён: цикл, прямая ссылка или неизвестное имя')
            known.add(n.name)
        if self.output not in known:
            raise ValueError('Не определён выход графа')


@dataclass(frozen=True)
class Stage:
    kind: str
    latency: int

    def accepts(self, op):
        return self.kind == 'G' or (self.kind == 'M' and op == 'mul') or (
            self.kind == 'A' and op in ('pass', 'add', 'sub'))


@dataclass(frozen=True)
class Profile:
    name: str
    local: tuple
    edge: tuple = (Stage('A', 2), Stage('M', 4))
    lanes: int = 4
    banks: int = 4
    tracks: int = 7
    tail_tracks: int = 7
    preserve_center: bool = False
    memory_latency: int = 1
    multiply_latency: int = 4
    write_pause_period: int = 0
    write_pause_length: int = 0

    def validate(self):
        if not 3 <= self.tracks <= 7 or min(self.lanes, self.banks, self.memory_latency,
                                           self.multiply_latency) < 1:
            raise ValueError('Недопустимая вместимость, число портов или задержка')
        if self.tracks < len(LOCAL_INPUTS):
            raise ValueError('Недостаточно дорожек для входного окна')
        if not 1 <= self.tail_tracks <= self.tracks:
            raise ValueError('Недопустимая ширина остатка графа')
        for stage in (*self.edge, *self.local):
            if stage.kind not in ('A', 'M', 'G') or stage.latency < 1:
                raise ValueError('Недопустимая физическая стадия')
        if self.write_pause_period < 0 or not 0 <= self.write_pause_length <= max(0, self.write_pause_period-1):
            raise ValueError('Пауза записи должна оставлять рабочие такты')


def profiles():
    return (
        # Задержки контрольной конфигурации взяты из проверенного фиксированного RTL.
        Profile('фиксированный контроль', (Stage('A', 1),)*4 + (Stage('M', 4),),
                edge=(Stage('A', 1), Stage('M', 4))),
        Profile('раздельные блоки', (Stage('A', 2),)*4 + (Stage('M', 4),)*5 + (Stage('A', 2),)*2,
                tail_tracks=4,preserve_center=True),
        Profile('универсальные ячейки', (Stage('G', 4),)*11),
        Profile('раздельные, два тракта', (Stage('A', 2),)*4 + (Stage('M', 4),)*5 + (Stage('A', 2),)*2,
                lanes=2, banks=2,tail_tracks=4,preserve_center=True),
        Profile('исходные семь дорожек', (Stage('A', 2),)*4 + (Stage('M', 4),)*5 + (Stage('A', 2),)*2),
    )


def flux_graph():
    return Graph(EDGE_INPUTS, (Node('delta', 'sub', 'neighbor', 'center'),
                              Node('flux', 'mul', 'coefficient', 'delta')), 'flux')


def heat_graph():
    return Graph(LOCAL_INPUTS, (
        Node('b0', 'add', 'east', 'south'), Node('b1', 'sub', 'b0', 'west'),
        Node('b2', 'sub', 'b1', 'north'), Node('b3', 'add', 'b2', 'q'),
        Node('diffusion', 'mul', 'ci', 'b3')), 'diffusion')


def reaction_graph():
    h = heat_graph()
    return Graph(LOCAL_INPUTS, h.nodes + (
        Node('u2', 'mul', 'center', 'center'), Node('u3', 'mul', 'u2', 'center'),
        Node('au', 'mul', 'c0', 'center'), Node('bu3', 'mul', 'c1', 'u3'),
        Node('r0', 'add', 'diffusion', 'au'), Node('rhs', 'sub', 'r0', 'bu3')), 'rhs')


def encode(op, a, b, destination):
    return op | (a << 3) | (b << 7) | (destination << 11)


def decode(word):
    return word & 7, (word >> 3) & 15, (word >> 7) & 15, (word >> 11) & 7


def compile_graph(graph, stages, tracks, tail_tracks=None, preserve_center=False):
    """Назначить узлы физическим стадиям и переиспользовать дорожки после последнего чтения."""
    graph.validate()
    if len(graph.inputs) > tracks:
        raise ValueError('Входное окно не помещается')
    binding = dict(zip(graph.inputs, range(tracks)))
    constants = {'zero': 15, **{f'c{i}': 7+i for i in range(8)}}
    pending = list(graph.nodes)
    words, placement = [], []
    peak = 0
    for index, stage in enumerate(stages):
        used = {s for n in pending for s in ((n.a,) if n.operation == 'pass' else (n.a, n.b))}
        used.add(graph.output)
        if preserve_center:used.add(graph.inputs[0])
        binding = {k: v for k, v in binding.items() if k in used}
        limit=tail_tracks if tail_tracks is not None and index>=4 else tracks
        if any(v>=limit for v in binding.values()):
            raise ValueError('Живое значение не помещается после сужения дорожек')
        peak = max(peak, len(binding))
        chosen = None
        for n in pending:
            inputs = (n.a,) if n.operation == 'pass' else (n.a, n.b)
            if stage.accepts(n.operation) and all(s in binding or s in constants for s in inputs):
                remaining = [m for m in pending if m is not n]
                needed = {s for m in remaining for s in ((m.a,) if m.operation == 'pass' else (m.a, m.b))}
                needed.add(graph.output)
                if preserve_center:needed.add(graph.inputs[0])
                occupied = {v for k, v in binding.items() if k in needed}
                free = [v for v in range(limit) if v not in occupied]
                if free:
                    chosen = n, remaining, needed, free[0]
                    break
        if chosen is None:
            words.append(0)
            continue
        n, remaining, needed, destination = chosen
        def source(name):
            return constants[name] if name in constants else binding[name]
        a, b = source(n.a), 15 if n.operation == 'pass' else source(n.b)
        words.append(encode(OPERATIONS[n.operation], a, b, destination))
        placement.append(dict(node=n.name, stage=index, destination=destination, a=a, b=b))
        binding = {k: v for k, v in binding.items() if k in needed}
        binding[n.name] = destination
        peak = max(peak, len(binding))
        pending = remaining
    if pending:
        raise ValueError('Граф не размещён: не хватает подходящих стадий или дорожек; ' +
                         ', '.join(n.name for n in pending))
    output = constants[graph.output] if graph.output in constants else binding[graph.output]
    return dict(words=words, output=output, placement=placement, peak_live_tracks=peak)


@dataclass(frozen=True)
class Image:
    edge: tuple
    local: tuple
    edge_output: int
    local_output: int
    constants: tuple = (0,)*8

    def to_bytes(self):
        # Промежуточный исследовательский формат; обещания совместимости нет.
        return json.dumps(dict(version=1, **asdict(self)), sort_keys=True, separators=(',', ':')).encode()

    @classmethod
    def from_bytes(cls, data, profile):
        d = json.loads(data)
        if set(d) != {'version', 'edge', 'local', 'edge_output', 'local_output', 'constants'} or d.pop('version') != 1:
            raise ValueError('Недопустимый образ или версия')
        for name in ('edge', 'local', 'constants'):
            d[name] = tuple(d[name])
        image = cls(**d)
        image.validate(profile)
        return image

    def validate(self, profile):
        profile.validate()
        if len(self.constants) != 8:
            raise ValueError('Нужны восемь констант')
        for c in self.constants:
            checked(c)
        for words, stages, tracks, output in (
            (self.edge, profile.edge, len(EDGE_INPUTS), self.edge_output),
            (self.local, profile.local, profile.tracks, self.local_output),
        ):
            output_tracks=tracks if words is self.edge else profile.tail_tracks
            if len(words) != len(stages) or type(output) is not int or not 0 <= output < 16 or output_tracks <= output < 7:
                raise ValueError('Недопустимое число стадий или источник выхода')
            for index,(word, stage) in enumerate(zip(words, stages)):
                limit=profile.tail_tracks if words is self.local and index>=4 else tracks
                if type(word) is not int or not 0 <= word < (1 << 14):
                    raise ValueError('Лишние разряды конфигурации')
                op, a, b, destination = decode(word)
                if op == 0:
                    if word != 0:
                        raise ValueError('Пустая стадия должна быть нулевым словом')
                    continue
                if op not in NAMES or not stage.accepts(NAMES[op]) or destination >= limit or (profile.preserve_center and destination==0):
                    raise ValueError('Операция не поддерживается этой стадией')
                if limit <= a < 7 or (op != 1 and limit <= b < 7):
                    raise ValueError('Недопустимый источник стадии')


def compile_image(edge, local, profile, constants=(0,)*8):
    profile.validate()
    if edge.inputs!=EDGE_INPUTS or local.inputs!=LOCAL_INPUTS:
        raise ValueError('Входы графа должны соответствовать окну этого профиля')
    e = compile_graph(edge, profile.edge, len(EDGE_INPUTS),preserve_center=profile.preserve_center)
    l = compile_graph(local, profile.local, profile.tracks,profile.tail_tracks,profile.preserve_center)
    image = Image(tuple(e['words']), tuple(l['words']), e['output'], l['output'], tuple(constants))
    image.validate(profile)
    return image, dict(edge=e, local=l)


def value(source, tracks, constants):
    return tracks[source] if source < 7 else constants[source-7] if source < 15 else 0


def execute_word(word, tracks, constants):
    """Одна настроенная стадия: узкое значение и локальный признак отказа."""
    op, a, b, destination = decode(word)
    if not op:
        return tracks, False
    av, bv = value(a, tracks, constants), value(b, tracks, constants)
    result = av if op == 1 else av+bv if op == 2 else av-bv if op == 3 else (av*bv) >> 16
    fault = not -(1 << 31) <= result < (1 << 31)
    result = ((result + (1 << 31)) & 0xffffffff) - (1 << 31)
    return tuple(result if i == destination else v for i, v in enumerate(tracks)), fault


def evaluate_graph(graph, inputs, constants):
    """Последовательный эталон исходного графа, без размещения по дорожкам."""
    graph.validate()
    values = dict(inputs, zero=0, **{f'c{i}': v for i, v in enumerate(constants)})
    for n in graph.nodes:
        a = values[n.a]
        b = 0 if n.operation == 'pass' else values[n.b]
        values[n.name] = checked(a if n.operation == 'pass' else a+b if n.operation == 'add'
                                 else a-b if n.operation == 'sub' else (a*b) >> 16)
    return values[graph.output]


def reference_graph(grid, edge, local, constants=(0,)*8):
    grid.validate()
    w, h, n = grid.width, grid.height, len(grid.state)
    e, s = [0]*n, [0]*n
    try:
        for i, center in enumerate(grid.state):
            y, x = divmod(i, w)
            for array, offset, coefficient, present in ((e, 1, grid.east[i], x+1 < w),
                                                       (s, w, grid.south[i], y+1 < h)):
                if present:
                    array[i] = evaluate_graph(edge, dict(center=center, neighbor=grid.state[i+offset],
                                                        coefficient=coefficient), constants)
        trial = []
        for i, center in enumerate(grid.state):
            y, x = divmod(i, w)
            derivative = evaluate_graph(local, dict(center=center, ci=grid.capacity_inverse[i], q=grid.source[i],
                east=e[i], south=s[i], west=e[i-1] if x else 0, north=s[i-w] if y else 0), constants)
            trial.append(checked(center + ((grid.dt * derivative) >> 16)))
    except OverflowError:
        return grid.state, 3
    return tuple(trial), 0


def reference_reaction(grid, a, b):
    """Обычный расчёт реакции–диффузии по массивам, без описания графа."""
    grid.validate()
    w, h, n = grid.width, grid.height, len(grid.state)
    east, south = [0]*n, [0]*n
    try:
        for y in range(h):
            for x in range(w):
                i = y*w+x
                if x+1 < w:
                    east[i] = checked((grid.east[i]*checked(grid.state[i+1]-grid.state[i])) >> 16)
                if y+1 < h:
                    south[i] = checked((grid.south[i]*checked(grid.state[i+w]-grid.state[i])) >> 16)
        trial = []
        for y in range(h):
            for x in range(w):
                i = y*w+x
                u = grid.state[i]
                balance = checked(east[i]+south[i])
                balance = checked(balance-(east[i-1] if x else 0))
                balance = checked(balance-(south[i-w] if y else 0))
                balance = checked(balance+grid.source[i])
                diffusion = checked((grid.capacity_inverse[i]*balance) >> 16)
                u2 = checked((u*u) >> 16)
                u3 = checked((u2*u) >> 16)
                au = checked((a*u) >> 16)
                bu3 = checked((b*u3) >> 16)
                derivative = checked(checked(diffusion+au)-bu3)
                trial.append(checked(u+((grid.dt*derivative) >> 16)))
    except OverflowError:
        return grid.state, 3
    return tuple(trial), 0


class Pipeline:
    """Физические стадии с фиксированными задержками и общей остановкой потока."""
    def __init__(self, stages, words, transform):
        self.stages, self.words, self.transform = stages, words, transform
        self.queues = [deque([None]*s.latency) for s in stages]

    def advance(self, incoming):
        for index,(queue, word) in enumerate(zip(self.queues, self.words)):
            outgoing = queue.popleft()
            queue.append(None if incoming is None else self.transform(incoming, word,index))
            incoming = outgoing
        return incoming

    def empty(self):
        return all(all(v is None for v in q) for q in self.queues)


def epoch(grid, image, profile):
    """Окно → граф связи → запад/север → локальный граф → переход → общий COMMIT."""
    grid.validate()
    image.validate(profile)
    w, n = grid.width, len(grid.state)
    if w % profile.lanes:
        raise ValueError('В этом кандидате ширина должна делиться на число трактов')
    if profile.banks < profile.lanes:
        raise ValueError('У этой потоковой организации нужен банк на тракт; скрытых дополнительных портов нет')
    stats = Counter()
    line = [None]*w
    previous_east = None
    trial = [None]*n
    position, written, fault = 0, 0, False
    # Такт начала эпохи, синхронное чтение, регистрация и взгляд на следующую четвёрку.
    delivery = deque([None]*(profile.memory_latency+3))

    def edge_stage(batch, word, index):
        output = []
        for token in batch:
            t = dict(token)
            for name in ('e', 's'):
                t[name], bad = execute_word(word, token[name], image.constants)
                t[name+'bad'] = token[name+'bad'] or bad
            output.append(t)
        return output

    def local_stage(batch, word, index):
        output = []
        for token in batch:
            t = dict(token)
            width=profile.tracks if index<4 else profile.tail_tracks
            t['v'], bad = execute_word(word, token['v'][:width], image.constants)
            t['bad'] = token['bad'] or bad
            output.append(t)
        return output

    edge_pipe = Pipeline(profile.edge, image.edge, edge_stage)
    local_pipe = Pipeline(profile.local, image.local, local_stage)
    exchange = deque([None])
    integrate = deque([None]*(profile.multiply_latency+1))
    clock = 0
    while written < n:
        clock += 1
        paused = profile.write_pause_period and clock % profile.write_pause_period < profile.write_pause_length
        if paused:
            stats['global_stalls'] += 1
            continue
        # Публикация пробных слов. Даже при ошибке эпоха полностью просматривается.
        output = integrate.popleft()
        if output is not None:
            for t in output:
                assert trial[t['i']] is None
                trial[t['i']] = t['next']
                fault |= t['bad']
                written += 1
                stats['trial_writes'] += 1
        output = local_pipe.advance(exchange.popleft())
        integrated = None
        if output is not None:
            integrated = []
            for t in output:
                derivative = value(image.local_output, t['v'], image.constants)
                current=t['v'][0] if profile.preserve_center else t['center']
                assert current==t['center']
                next_value = current + ((grid.dt * derivative) >> 16)
                integrated.append(dict(t, next=next_value, bad=t['bad'] or not -(1 << 31) <= next_value < (1 << 31)))
        integrate.append(integrated)
        output = edge_pipe.advance(delivery.popleft())
        linked = None
        if output is not None:
            linked = []
            for t in output:
                i = t['i']
                y, x = divmod(i, w)
                e = value(image.edge_output, t['e'], image.constants) if x+1 < w else 0
                s = value(image.edge_output, t['s'], image.constants) if y+1 < grid.height else 0
                west = previous_east if x else 0
                north = line[x] if y else 0
                assert west is not None and north is not None
                previous_east = e
                line[x] = s
                stats['flux_line_reads'] += bool(y)
                stats['flux_line_writes'] += 1
                bad = (t['ebad'] and x+1 < w) or (t['sbad'] and y+1 < grid.height)
                current=t['e'][0] if profile.preserve_center else t['center']
                assert current==t['center']
                tracks = (current, grid.capacity_inverse[i], grid.source[i], e, s, west, north)
                linked.append(dict(i=i, center=t['center'], v=tracks, bad=bool(bad)))
        exchange.append(linked)
        incoming = None
        if position < n:
            incoming = []
            for i in range(position, position+profile.lanes):
                y, x = divmod(i, w)
                center = grid.state[i]
                incoming.append(dict(i=i, center=center, e=(center, grid.state[i+1] if x+1 < w else center, grid.east[i]),
                    s=(center, grid.state[i+w] if y+1 < grid.height else center, grid.south[i]), ebad=False, sbad=False))
            position += profile.lanes
            stats['input_groups'] += 1
            stats['state_reads_a'] += profile.lanes
            stats['state_reads_b'] += profile.lanes
            stats['map_reads'] += 4*profile.lanes
        delivery.append(incoming)
        if clock > n*(10+profile.write_pause_period)+10000:
            raise RuntimeError('Поток не завершился')
    assert edge_pipe.empty() and local_pipe.empty()
    assert all(v is not None for v in trial)
    # Последняя запись и общее принятие выполняются на одном фронте, как в grid_core.
    return dict(state=grid.state if fault else tuple(trial), fault=3 if fault else 0,
                cycles=clock, stats=dict(stats))


def simulate(grid, image, profile, steps=1):
    if steps < 0:
        raise ValueError('Отрицательное число шагов')
    grid.validate()
    image.validate(profile)
    frames = []
    completed = 0
    for _ in range(steps):
        result = epoch(grid, image, profile)
        frames.append({k: v for k, v in result.items() if k != 'state'})
        if result['fault']:
            break
        grid = replace(grid, state=result['state'])
        completed += 1
    return dict(state=grid.state, completed=completed, fault=frames[-1]['fault'] if frames else 0,
                cycles=sum(f['cycles'] for f in frames), frames=frames)


def budget(profile):
    """Структурный бюджет задержек до оптимизации, без обещания полного размещения."""
    stages = (*profile.edge, *profile.local)
    multipliers = 2*sum(s.kind in ('M', 'G') for s in profile.edge) + sum(s.kind in ('M', 'G') for s in profile.local) + 1
    edge_bits = sum(s.latency*(2*3*32+(0 if profile.preserve_center else 32)+12) for s in profile.edge)
    local_bits = sum(s.latency*((profile.tracks if i<4 else profile.tail_tracks)*32+
                                (0 if profile.preserve_center else 32)+11) for i,s in enumerate(profile.local))
    # Умножители учитываются отдельно: здесь дорожки, метки и сохранённый исходный T.
    return dict(full_multipliers=profile.lanes*multipliers, dsp48=4*profile.lanes*multipliers,
                pipeline_payload_bits=profile.lanes*(edge_bits+local_bits),
                source_mux_inputs=profile.tracks+9, stages=len(stages),
                limitation='Без регистров DSP/суммирования, окон, JTAG, управления и оценки LUT/частоты')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--размер', type=int, default=64)
    parser.add_argument('--шаги', type=int, default=1)
    parser.add_argument('--отчёт', type=Path, default=ROOT/'reports/программируемое-поле.json')
    args = parser.parse_args()
    report = dict(date='2026-10-19', kind='Программная модель; без оценки аппаратной частоты', configurations=[])
    grid = example(args.размер, args.размер)
    for p in profiles():
        for label, graph, constants in (('тепло', heat_graph(), (0,)*8),
                                       ('реакция–диффузия', reaction_graph(), (ONE//8, ONE//16)+(0,)*6)):
            row = dict(profile=asdict(p), model=label, budget=budget(p))
            try:
                image, placement = compile_image(flux_graph(), graph, p, constants)
            except ValueError as error:
                row['rejected'] = str(error)
                print(f'{p.name}, {label}: отклонён — {error}', flush=True)
            else:
                # Проверяется именно повторно загруженный образ, а не исходный объект графа.
                loaded = Image.from_bytes(image.to_bytes(), p)
                result = simulate(grid, loaded, p, args.шаги)
                state, fault, completed = grid.state, 0, 0
                for _ in range(args.шаги):
                    state, fault = reference_graph(replace(grid, state=state), flux_graph(), graph, constants)
                    if fault:
                        break
                    completed += 1
                assert (result['state'], result['completed'], result['fault']) == (state, completed, fault)
                if label == 'тепло' and args.шаги == 1:
                    assert (state, fault) == reference(grid)
                if label == 'реакция–диффузия' and args.шаги == 1:
                    assert (state, fault) == reference_reaction(grid, constants[0], constants[1])
                row.update(result=result, placement=placement, image_sha256=hashlib.sha256(image.to_bytes()).hexdigest())
                print(f'{p.name}, {label}: {result["cycles"]} тактов; '
                      f'{row["budget"]["dsp48"]} DSP по структуре; побитовое совпадение', flush=True)
            report['configurations'].append(row)
    report['source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.отчёт.parent.mkdir(parents=True, exist_ok=True)
    args.отчёт.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(f'Отчёт: {args.отчёт}')


if __name__ == '__main__':
    main()

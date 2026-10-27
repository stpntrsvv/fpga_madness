#!/usr/bin/env python3
"""Модели, точный расчёт Q16.16 и обмен с автономным ядром через JTAG."""

import argparse
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ONE = 1 << 16
MINIMUM, MAXIMUM = -(1 << 31), (1 << 31) - 1
SIGNATURE = 0x44505532


def q(value):
    return round(value * ONE)


def checked(value):
    if not MINIMUM <= value <= MAXIMUM:
        raise OverflowError("Переполнение Q16.16")
    return value


@dataclass(frozen=True)
class Model:
    name: str
    states: tuple
    parameters: tuple
    nodes: tuple
    derivatives: tuple
    dt: int

    def validate(self):
        if not 1 <= len(self.states) <= 4 or len(self.parameters) > 8:
            raise ValueError("Превышена вместимость состояний или параметров")
        if len(self.nodes) > 9 or len(self.derivatives) != len(self.states):
            raise ValueError("Неверное число узлов или производных")
        if not 0 < self.dt <= MAXIMUM:
            raise ValueError("Шаг времени должен быть положительным Q16.16")

        def valid(source, limit):
            return (0 <= source < len(self.states) or 4 <= source < 12 or
                    12 <= source < 12 + limit)

        for i, (operation, a, b) in enumerate(self.nodes):
            if operation not in range(4) or not valid(a, i):
                raise ValueError(f"Недопустимый узел {i}")
            if operation and not valid(b, i):
                raise ValueError(f"Недопустимый второй вход узла {i}")
        if not all(valid(source, len(self.nodes)) for source in self.derivatives):
            raise ValueError("Недопустимый источник производной")
        for value in (*self.states, *self.parameters):
            checked(value)

    def evolve(self, steps):
        """Та же арифметика, включая округление вниз и общий отказ шага."""
        self.validate()
        states = list(self.states)
        completed = 0
        for _ in range(steps):
            values = states + [0] * (4 - len(states))
            values += list(self.parameters) + [0] * (8 - len(self.parameters))
            try:
                for operation, a, b in self.nodes:
                    x, y = values[a], values[b] if operation else 0
                    result = (x, x + y, x - y, (x * y) >> 16)[operation]
                    values.append(checked(result))
                candidate = [checked(x + ((self.dt * values[source]) >> 16))
                             for x, source in zip(states, self.derivatives)]
            except OverflowError:
                return states, completed, 3
            states = candidate
            completed += 1
        return states, completed, 0

    def cycles_per_step(self):
        depths = [0] * 12
        for operation, a, b in self.nodes:
            latency = 5 if operation == 3 else 2
            depths.append(latency + max(depths[a], depths[b] if operation else 0))
        integration = 3 if self.dt > 0 and self.dt & (self.dt - 1) == 0 else 6
        return max(1, max(depths)) + integration


MODELS = {
    "лоренц": Model("лоренц", (q(1), q(1), q(1)), (q(10), q(28), q(8 / 3)),
                    ((2, 1, 0), (3, 4, 12), (3, 5, 0), (3, 0, 2),
                     (2, 14, 1), (2, 16, 15), (3, 0, 1), (3, 6, 2), (2, 18, 19)),
                    (13, 17, 20), q(1 / 1024)),
    "rlc": Model("rlc", (q(1), 0), (q(0.5), q(1), q(1), 0),
                 ((3, 4, 0), (2, 7, 12), (2, 13, 1), (3, 14, 5), (3, 0, 6)),
                 (15, 16), q(1 / 256)),
    "тепло": Model("тепло", (q(1), 0, 0, 0), (q(1 / 8), 0),
                   ((2, 1, 0), (3, 4, 12), (2, 2, 1), (3, 4, 14),
                    (2, 3, 2), (3, 4, 16), (2, 15, 13), (2, 17, 15), (2, 5, 17)),
                   (13, 18, 19, 20), q(1 / 8)),
    "осциллятор": Model("осциллятор", (q(1), 0), (0,),
                         ((0, 1, 0), (2, 4, 0)), (12, 13), q(1 / 256)),
}


def physical_derivative(name, x):
    """Уравнения независимо от раскладки узлов; эталон двойной точности."""
    if name == "лоренц":
        a, b, c = x
        return (10 * (b - a), a * (28 - c) - b, a * b - (8 / 3) * c)
    if name == "rlc":
        current, voltage = x
        return (-0.5 * current - voltage, current)
    if name == "тепло":
        flux = [(x[i + 1] - x[i]) / 8 for i in range(3)]
        return (flux[0], flux[1] - flux[0], flux[2] - flux[1], -flux[2])
    if name == "осциллятор":
        return (x[1], -x[0])
    raise ValueError("Нет независимого эталона для модели")


def verify_numerics():
    # Пределы относятся к этим параметрам и 256 шагам, не к произвольным запускам.
    fixed_limits = {"лоренц": 0.02, "rlc": 0.01, "тепло": 0.01, "осциллятор": 0.01}
    method_limits = {"лоренц": 0.35, "rlc": 0.01, "тепло": 0.01, "осциллятор": 0.01}
    for model in MODELS.values():
        fixed, completed, fault = model.evolve(256)
        euler = [x / ONE for x in model.states]
        accurate = euler[:]
        h = model.dt / ONE
        for _ in range(256):
            derivative = physical_derivative(model.name, euler)
            euler = [x + h * d for x, d in zip(euler, derivative)]
            for _ in range(8):
                step = h / 8
                k1 = physical_derivative(model.name, accurate)
                k2 = physical_derivative(model.name, [x + step * d / 2 for x, d in zip(accurate, k1)])
                k3 = physical_derivative(model.name, [x + step * d / 2 for x, d in zip(accurate, k2)])
                k4 = physical_derivative(model.name, [x + step * d for x, d in zip(accurate, k3)])
                accurate = [x + step * (a + 2 * b + 2 * c + d) / 6
                            for x, a, b, c, d in zip(accurate, k1, k2, k3, k4)]
        fixed_error = max(abs(x / ONE - y) for x, y in zip(fixed, euler))
        method_error = max(abs(x - y) for x, y in zip(euler, accurate))
        if fault or completed != 256 or fixed_error > fixed_limits[model.name]:
            raise AssertionError(f"Ошибка арифметики модели {model.name}: {fixed_error}")
        if method_error > method_limits[model.name]:
            raise AssertionError(f"Ошибка метода модели {model.name}: {method_error}")
        print(f"{model.name}: ошибка Q16.16 {fixed_error:.6g}, ошибка Эйлера {method_error:.6g}")


def frame(command, address=0, data=0):
    return (0xD5 << 56) | (command << 48) | (address << 32) | (data & 0xFFFFFFFF)


def image(model):
    model.validate()
    commands = [frame(1)]
    entries = [(0x10, len(model.states)), (0x11, len(model.nodes)), (0x12, model.dt)]
    entries += [(0x100 + i, x) for i, x in enumerate(model.states)]
    entries += [(0x200 + i, p) for i, p in enumerate(model.parameters)]
    entries += [(0x300 + i, op | (a << 2) | (b << 8))
                for i, (op, a, b) in enumerate(model.nodes)]
    entries += [(0x400 + i, s) for i, s in enumerate(model.derivatives)]
    return commands + [frame(2, address, value) for address, value in entries]


def scenario(model, steps):
    states, completed, fault = model.evolve(steps)
    vectors = [(cmd, 0, 4) for cmd in image(model)]
    vectors += [(frame(3, (cmd >> 32) & 0xFFFF), cmd & 0xFFFFFFFF, 4)
                for cmd in image(model)[1:]]
    vectors += [(frame(3, 0), SIGNATURE, 4)]
    vectors += [(frame(4, data=steps), 0, steps * model.cycles_per_step() + 8)]
    status = (fault << 8) | (4 if fault else 2)
    vectors += [(frame(3, 1), status, 4), (frame(3, 2), completed, 4)]
    if not fault:
        vectors += [(frame(3, 3), steps * model.cycles_per_step(), 4)]
    vectors += [(frame(3, 0x100 + i), x & 0xFFFFFFFF, 4) for i, x in enumerate(states)]
    return vectors


def test_vectors():
    vectors = []
    for model in MODELS.values():
        vectors += scenario(model, 256)
        states, _, _ = model.evolve(256)
        continued = replace(model, states=tuple(states))
        expected, _, _ = continued.evolve(16)
        vectors += [(frame(4, data=16), 0, 16 * model.cycles_per_step() + 8)]
        vectors += [(frame(3, 2), 16, 4)]
        vectors += [(frame(3, 0x100 + i), x & 0xFFFFFFFF, 4)
                    for i, x in enumerate(expected)]
    oscillator = MODELS["осциллятор"]
    vectors += scenario(oscillator, 1)
    vectors += scenario(oscillator, 0)
    # Непрерывное переключение между сдвигом и общим произведением в одной схеме.
    vectors += scenario(replace(MODELS['rlc'], dt=257), 16)
    vectors += scenario(replace(MODELS['тепло'], dt=8191), 16)
    vectors += scenario(Model("сумма и общий нуль", (q(1), q(-1)), (q(0.5),),
                              ((1, 0, 1), (3, 12, 4), (0, 4, 0)), (13, 14), q(1 / 64)), 16)
    # Даже ошибка последнего состояния отменяет запись всех четырёх.
    vectors += scenario(Model("переполнение перехода", (q(1), q(2), q(3), MAXIMUM),
                              (q(1),), (), (4, 4, 4, 4), q(1)), 1)
    vectors += scenario(Model("переполнение узла", (q(123),), (q(30000), q(2)),
                              ((3, 4, 5),), (12,), q(1)), 1)
    vectors += scenario(Model("ошибка второго шага", (q(32765),), (q(2),),
                              (), (4,), q(1)), 2)
    vectors += [(cmd, 0, 4) for cmd in image(oscillator)]
    for bad_config in (12 << 2, 63 << 2, 1 << 31):
        vectors += [(frame(2, 0x300, bad_config), 0, 4),
                    (frame(4, data=1), 0xBAD00003, 4),
                    (frame(3, 1), 0x204, 4), (frame(3, 2), 0, 4)]
    vectors += [(cmd, 0, 4) for cmd in image(oscillator)]
    # Длительный постоянный расчёт остаётся занятым и при быстром независимом тактировании.
    constant = Model("длительный нуль", (q(1),), (0,), (), (4,), q(1 / 256))
    vectors += [(cmd, 0, 4) for cmd in image(constant)]
    vectors += [(frame(4, data=100000000), 0, 4),
                (frame(2, 0x100, 0), 0xBAD00001, 4),
                (frame(4, data=1), 0xBAD00001, 4),
                (frame(3, 1), 1, 4), (frame(1), 0, 4),
                (frame(3, 1), 0, 4), (frame(3, 0xFFFF), 0xBAD00002, 4),
                (frame(0xFF), 0xBAD00004, 4)]
    return vectors


def hardware(vectors, *, measure=False):
    from jtag_runtime import exchange
    wait_runs = [i for i, (request, expected, clocks) in enumerate(vectors)
                 if ((request >> 48) & 0xff) == 4 and expected == 0 and clocks > 4]
    result = exchange([(request, expected) for request, expected, _ in vectors], wait_runs=wait_runs)
    print(f'УСПЕХ: проверены {len(vectors)} обменов DPU2')
    if measure:
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("действие", choices=("проверить", "образ", "плата"))
    parser.add_argument("модель", nargs="?", choices=tuple(MODELS) + ("все",), default="все")
    parser.add_argument("--шаги", type=int, default=256)
    parser.add_argument("--векторы", type=Path)
    parser.add_argument("--полная-проверка", action="store_true",
                        help="Выполнить на плате весь набор проверок, включая отказы")
    args = parser.parse_args()
    if not 0 <= args.шаги <= 0xFFFFFFFF:
        parser.error("Число шагов должно помещаться в 32 бита")
    if args.полная_проверка and (args.действие != "плата" or args.модель != "все" or args.шаги != 256):
        parser.error("Полная проверка запускается как: плата --полная-проверка")
    selected = list(MODELS.values()) if args.модель == "все" else [MODELS[args.модель]]
    if args.действие == "образ":
        for model in selected:
            for request in image(model):
                print(f"{request:016x}")
    elif args.действие == "плата":
        vectors = test_vectors() if args.полная_проверка else [
            v for model in selected for v in scenario(model, args.шаги)]
        hardware(vectors)
    else:
        verify_numerics()
        for model in selected:
            states, completed, fault = model.evolve(args.шаги)
            print(f"{model.name}: шагов {completed}, ошибка {fault}, "
                  f"состояния {[x / ONE for x in states]}, "
                  f"тактов/шаг {model.cycles_per_step()}")
        if args.векторы:
            vectors = test_vectors()
            args.векторы.write_text("".join(f"{r:016x} {e:08x} {c}\n" for r, e, c in vectors))
            print(f"Подготовлено {len(vectors)} проверок обмена")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(f"Проверка платы завершилась ошибкой:\n{error}") from None

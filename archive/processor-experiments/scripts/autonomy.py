#!/usr/bin/env python3
"""Доказать завершение расчёта в паузе без импульсов TCK."""
import json
from dpu import Model, ROOT, frame, image, q
from jtag_runtime import clock_sample, exchange


def main():
    model = Model('постоянное состояние', (q(1),), (0,), (), (4,), q(1 / 256))
    steps = 1_000_000
    vectors = [(request, 0) for request in image(model)]
    vectors += [(frame(4, data=steps), 0), (frame(3, 1), 1)]
    after_pause = len(vectors)
    vectors += [(frame(3, 1), 2), (frame(3, 2), steps),
                (frame(3, 3), steps * model.cycles_per_step()), (frame(3, 0x100), q(1))]
    result = exchange(vectors, pauses={after_pause: 300}, log_path=ROOT / 'reports/автономность.log')
    clock = clock_sample(ROOT / 'reports/частота-ядра.log')
    report = {'steps': steps, 'cycles': steps * model.cycles_per_step(), 'pause_without_tck_ms': 300,
              'clock': clock, 'records': result['records']}
    (ROOT / 'reports/автономность.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f'УСПЕХ: перед паузой busy=1, после 300 мс без TCK done=1; '
          f'{steps} шагов, {report["cycles"]} тактов, состояние сохранено')
    print(f'Измеренная частота: {clock["minimum_hz"] / 1e6:.3f}–{clock["maximum_hz"] / 1e6:.3f} МГц')


if __name__ == '__main__':
    main()

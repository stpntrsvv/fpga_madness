#!/usr/bin/env python3
"""Проверить автономный генератор и полный знаковый продукт на диагностической прошивке."""

import json
import random
from dpu import ROOT, frame
from jtag_runtime import clock_sample, exchange


def main():
    rng = random.Random(20261017)
    boundary = [0, 1, -1, 0x7fffffff, -0x80000000, 65536, -65536, 0x12345678]
    pairs = [(a, b) for a in boundary for b in boundary]
    pairs += [(rng.randint(-0x80000000, 0x7fffffff), rng.randint(-0x80000000, 0x7fffffff))
              for _ in range(128)]
    vectors = [(frame(3, 0), 0x50524232), (frame(1), 0)]
    for a, b in pairs:
        expected = a * b
        vectors += [(frame(2, 0x700, a), 0), (frame(2, 0x701, b), 0),
                    (frame(3, 0x702), expected & 0xffffffff),
                    (frame(3, 0x703), (expected >> 32) & 0xffffffff)]
    result = exchange(vectors, log_path=ROOT / 'reports/диагностика-DSP.log')
    print(f'УСПЕХ: на плате проверены {len(pairs)} знаковых произведений, {len(vectors)} обменов', flush=True)
    clock = clock_sample(ROOT / 'reports/диагностика-тактов.log')
    print(f'Независимая частота: {clock["minimum_hz"] / 1e6:.3f}–{clock["maximum_hz"] / 1e6:.3f} МГц', flush=True)
    report = {'products': len(pairs), 'exchanges': len(vectors), 'clock': clock,
              'wall_seconds': result['wall_seconds']}
    path = ROOT / 'reports/диагностика.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()

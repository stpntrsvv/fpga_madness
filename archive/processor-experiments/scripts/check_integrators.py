#!/usr/bin/env python3
"""Проверить полные продукты всех четырёх интеграторов в рабочей конфигурации."""
from dataclasses import asdict
from datetime import datetime, timezone
import json

from dpu import Model, ROOT, frame, hardware, scenario


def main():
    vectors, cases = [], []
    derivatives = ((-79215, -1, 1, 65535),
                   (65536, -65536, -65537, 0),
                   (-2147483648, 2147483647, -79215, 1))
    for dt in (1, 257, 8191, 65536, 65537, 1 << 30):
        for values in derivatives:
            model = Model('проверка четырёх интеграторов', (0, 0, 0, 0),
                          values, (), (4, 5, 6, 7), dt)
            cases.append(asdict(model))
            vectors += scenario(model, 1)
            # Производные постоянны: чтение после принятия шага не меняет продукт.
            for state, derivative in enumerate(values):
                product = (dt * derivative) & ((1 << 64) - 1)
                vectors += [(frame(3, 0x620 + state), product & 0xFFFFFFFF, 4),
                            (frame(3, 0x630 + state), product >> 32, 4)]
    vectors += [(frame(1), 0, 4)]
    result = hardware(vectors, measure=True)
    result.update(date_utc=datetime.now(timezone.utc).isoformat(), cases=cases,
                  full_products=len(cases) * 4)
    destination = ROOT / 'reports/интеграторы-плата.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'УСПЕХ: {len(cases)} конфигураций, {len(cases) * 4} полных произведения; {destination}')


if __name__ == '__main__':
    main()

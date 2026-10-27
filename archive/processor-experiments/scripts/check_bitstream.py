#!/usr/bin/env python3
"""Проверить, что выдаваемая конфигурация прошла ограничения и обработку DSP."""
import hashlib
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
metadata_path = Path(str(path) + '.meta.json')
if not metadata_path.exists():
    raise SystemExit('Нет свидетельства проверенной сборки; выполните make bitstream')
metadata = json.loads(metadata_path.read_text())
if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['sha256']:
    raise SystemExit('Контрольная сумма конфигурации не совпала со свидетельством сборки')
if not metadata.get('dsp_constants_checked') or not metadata.get('dsp_data_routes_checked') or not metadata.get('timing'):
    raise SystemExit('Не подтверждены управляющие биты, маршруты данных DSP и временные ограничения')
if metadata.get('profile') in ('field', 'grid-fclk8', 'grid8-safe') and not metadata.get('dsp_placement_constraints_checked'):
    raise SystemExit('Не подтверждена проверка ограничений независимых DSP программируемого поля')
if any(t['achieved'] < t['constraint'] for t in metadata['timing'].values()):
    raise SystemExit('Конфигурация не прошла временные ограничения')
root = Path(__file__).resolve().parent.parent
sources = metadata.get('sources')
if not sources or any(not (root / name).is_file() or
                      hashlib.sha256((root / name).read_bytes()).hexdigest() != digest
                      for name, digest in sources.items()):
    raise SystemExit('Исходники изменились после сборки; выполните make bitstream')
print('Конфигурация прошла проверку исходников, контрольной суммы, DSP, маршрутов данных и временных ограничений')

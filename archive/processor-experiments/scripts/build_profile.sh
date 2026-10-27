#!/bin/sh
set -eu
[ "$#" -eq 2 ] || { echo 'Нужны профиль и имя выходной прошивки' >&2; exit 2; }
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
profile=$1
output=$2
if [ "$profile" = field ] || [ "$profile" = grid-fclk8 ] || [ "$profile" = grid8-safe ]; then
    .venv/bin/python scripts/build_field.py "$profile"
else
    sh scripts/apio.sh build -e "$profile"
fi
.venv/bin/python - "$profile" <<'PY'
import json, sys
from pathlib import Path
p = json.loads((Path('_build') / sys.argv[1] / 'hardware.pnr').read_text())
if not p.get('fmax'):
    raise SystemExit('Нет отчёта временных ограничений; прошивка не выдана')
for name, timing in p['fmax'].items():
    if timing['achieved'] < timing['constraint']:
        raise SystemExit(f'Временное ограничение не выполнено: {name}, '
                         f'{timing["achieved"]} < {timing["constraint"]} МГц; прошивка не выдана')
print('Все заявленные ограничения частоты выполнены')
PY
tools_dir=${OPENXC7_BIN:-$HOME/.apio/packages/openxc7/bin}
database=$tools_dir/../share/nextpnr/external/prjxray-db/zynq7
prefix=_build/$profile/hardware
.venv/bin/python scripts/check_dsp_routes.py "$prefix.routed.json"
.venv/bin/python scripts/fix_dsp_fasm.py "$prefix.json" "$prefix.fasm" "$database" "$prefix.checked.fasm"
"$tools_dir/fasm2frames" --part xc7z020clg400-1 --db-root "$database" \
    "$prefix.checked.fasm" > "$prefix.checked.frames"
mkdir -p "$(dirname -- "$output")"
"$tools_dir/xc7frames2bit" --part_file "$database/xc7z020clg400-1/part.yaml" \
    --part_name xc7z020clg400-1 --frm_file "$prefix.checked.frames" --output_file "$output"
.venv/bin/python - "$profile" "$output" <<'PY'
import hashlib, json, sys
from pathlib import Path
profile, filename = sys.argv[1:]
p = Path(filename)
metadata = {'profile': profile, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
            'dsp_constants_checked': True,
            'dsp_data_routes_checked': True,
            'timing': json.loads((Path('_build') / profile / 'hardware.pnr').read_text())['fmax']}
if profile in ('field', 'grid-fclk8', 'grid8-safe'):
    placement = json.loads((Path('_build') / profile / 'dsp-placement.json').read_text())
    if placement['count'] != 128 or any(hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest
                                       for name, digest in placement['sources'].items()):
        raise SystemExit('Не подтверждены ограничения независимых DSP и исходники сборки')
    metadata['dsp_placement_constraints_checked'] = True
sources = sorted([*Path('rtl').glob('*.sv'), *Path('constraints').glob('*.xdc'),
                  Path('apio.ini'), Path('scripts/build_profile.sh'), Path('scripts/fix_dsp_fasm.py'),
                  Path('scripts/check_dsp_routes.py'), Path('scripts/build_field.py'),
                  Path('scripts/fix_dsp_placement.py')])
metadata['sources'] = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sources}
Path(str(p) + '.meta.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
PY
printf 'Создана проверенная конфигурация: %s\n' "$output"

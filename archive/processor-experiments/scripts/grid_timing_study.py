#!/usr/bin/env python3
"""Исследовать частоту фиксированной сетки в изолированных копиях RTL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'Неоднозначное место экспериментальной замены: {old}')
    return text.replace(old, new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--вариант', choices=('контроль', 'проверка-dt', 'проверка-dt-выбор-слова',
                                            'проверка-dt-конец-шага', 'проверка-dt-оба-пути'), required=True)
    parser.add_argument('--этап', choices=('подготовка', 'проверка', 'синтез', 'размещение', 'трассировка'), required=True)
    args = parser.parse_args()
    os.chdir(ROOT)
    directory = Path('_build/исследование-частоты') / args.вариант
    directory.mkdir(parents=True, exist_ok=True)
    tools = Path(os.environ.get('OPENXC7_BIN', Path.home() / '.apio/packages/openxc7/bin'))
    oss = Path(os.environ.get('OSS_CAD_BIN', Path.home() / '.apio/packages/oss-cad-suite/bin'))
    if args.этап == 'подготовка':
        sources = sorted(Path('rtl').glob('*.sv'))
        hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        for p in sources:
            shutil.copyfile(p, directory / p.name)
        p = directory / 'grid_core.sv'
        text = p.read_text()
        if args.вариант != 'контроль':
            text = replace_once(text, 'logic signed [31:0] dt;',
                                'logic signed [31:0] dt;\n    logic dt_positive;')
            text = replace_once(text, 'role <= 0; dt <= 0;',
                                'role <= 0; dt <= 0; dt_positive <= 0;')
            text = replace_once(text, 'begin dt <= control_data; done <= 0; fault <= 0; end',
                                'begin dt <= control_data; dt_positive <= $signed(control_data) > 0; done <= 0; fault <= 0; end')
            text = replace_once(text, '!all_loaded || dt <= 0 ||',
                                '!all_loaded || !dt_positive ||')
        if args.вариант in ('проверка-dt-выбор-слова', 'проверка-dt-оба-пути'):
            text = replace_once(text, 'logic [2:0] word_bank;',
                                'logic [7:0] word_bank_onehot;\n    logic [31:0] selected_host_word;')
            text = replace_once(text, 'word_bank <= host_offset[4:2];',
                                "for (integer b=0; b<8; b=b+1) word_bank_onehot[b] <= host_offset[4:2] == 3'(b);")
            text = replace_once(text, 'host_word <= word_banks[word_bank];',
                                'host_word <= selected_host_word;')
            text = replace_once(text, '    for (genvar word_index=0;',
                                '''    always_comb begin
        selected_host_word = 0;
        for (integer b=0; b<8; b=b+1)
            selected_host_word = selected_host_word | (word_banks[b] & {32{word_bank_onehot[b]}});
    end
    for (genvar word_index=0;''')
        if args.вариант in ('проверка-dt-конец-шага', 'проверка-dt-оба-пути'):
            text = replace_once(text, 'logic [31:0] target_steps, completed_steps,',
                                'logic [31:0] last_step_index, completed_steps,')
            text = replace_once(text, 'target_steps <= 0;', 'last_step_index <= 0;')
            text = replace_once(text, 'completed_steps + 1 == target_steps',
                                'completed_steps == last_step_index')
            text = replace_once(text, 'target_steps <= control_data;',
                                "last_step_index <= control_data - 32'd1;")
        p.write_text(text)
        shutil.copyfile('constraints/grid.xdc', directory / 'timing.xdc')
        (directory / 'sources.json').write_text(json.dumps(hashes, indent=2) + '\n')
        print(f'Подготовлена отдельная копия: {directory}')
        return
    original = json.loads((directory / 'sources.json').read_text())
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in original.items()):
        raise ValueError('Исходники изменились после подготовки; сначала создать новую копию')

    def run(command):
        subprocess.run([str(x) for x in command], check=True)

    if args.этап == 'проверка':
        vectors = directory / 'vectors.txt'
        run([ROOT / '.venv/bin/python', ROOT / 'scripts/grid_vectors.py', vectors])
        cells = oss.parent / 'share/yosys/xilinx'
        if (oss / 'verilator').exists():
            run([oss / 'verilator', '--binary', '--timing', '-j', '2', '-Wno-fatal',
                 '--top-module', 'grid_tb', '--Mdir', directory / 'obj',
                 *sorted(directory.glob('*.sv')), ROOT / 'tb/grid_tb.sv',
                 cells / 'cells_sim.v', cells / 'cells_xtra.v'])
            executable = [(directory / 'obj/Vgrid_tb').resolve()]
        else:
            run([oss / 'iverilog', '-g2012', '-s', 'grid_tb', '-o', directory / 'simulation',
                 *sorted(directory.glob('*.sv')), ROOT / 'tb/grid_tb.sv',
                 cells / 'cells_sim.v', cells / 'cells_xtra.v'])
            executable = [oss / 'vvp', (directory / 'simulation').resolve()]
        # Icarus искажает кириллицу в строковом параметре пути; файл рядом с моделью.
        result = subprocess.run([*map(str, executable), '+VECTORS=vectors.txt'], cwd=directory,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(result.stdout, end='', flush=True)
        result.check_returncode()
        if not any(line.startswith('УСПЕХ:') for line in result.stdout.splitlines()):
            raise ValueError('Моделирование не подтвердило завершение полного набора')
    elif args.этап == 'синтез':
        run([oss / 'yosys', '-q', '-DSYNTHESIZE', '-p',
             f'synth_xilinx -arch xc7 -top grid_top -nodsp -nowidelut -nosrl; write_json {directory}/hardware.json',
             *sorted(directory.glob('*.sv'))])
    else:
        common = [tools / 'nextpnr-xilinx', '--chipdb', tools.parent / 'chipdb/xc7z020clg400.bin',
                  '--xdc', directory / 'timing.xdc', '--freq', '125', '-q']
        if args.этап == 'размещение':
            run([*common, '--json', directory / 'hardware.json', '--no-route',
                 '--write', directory / 'placed.json', '--log', directory / 'placement.log'])
        else:
            run([*common, '--json', directory / 'placed.json', '--no-pack', '--no-place',
                 '--write', directory / 'routed.json', '--log', directory / 'timing.log',
                 '--report', directory / 'hardware.pnr'])
    print(f'Завершён этап {args.этап}: {args.вариант}; это исследование, прошивка не создаётся')


if __name__ == '__main__':
    main()

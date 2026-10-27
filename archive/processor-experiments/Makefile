SHELL := /bin/sh
-include .env
VIVADO_BIN ?= vivado
APIO ?= sh "$(CURDIR)/scripts/apio.sh"

.PHONY: help doctor probe lint sim sim-синтез synth bitstream vivado-synth vivado-bitstream program clean модели проверить-плату сравнить сравнить-плату диагностика проверить-диагностику проверить-автономность проверить-интеграторы модель-сетки проверить-модель-сетки сравнить-сетку сравнить-сетку-плату проверить-сетку сетка сетка-125 проверить-сетку-плату программировать-сетку
help:
	@sed -n 's/^## //p' Makefile
## doctor: inspect tools, probe, and configuration
doctor:
	@./scripts/doctor.sh
## probe: read-only Zynq JTAG scan
probe:
	@./scripts/probe.sh
## lint: lint synthesizable RTL
lint:
	@$(APIO) lint
## sim: проверить разные модели, обмен JTAG и ошибки ядра в моделировании
sim:
	@./scripts/sim.sh
## sim-синтез: повторить проверки на схеме после синтеза openXC7
sim-синтез:
	@./scripts/sim.sh --после-синтеза
## модели: проверить численные модели на хосте
модели:
	@.venv/bin/python scripts/dpu.py проверить
## проверить-плату: загрузить четыре модели в уже настроенное ядро через JTAG
проверить-плату:
	@.venv/bin/python scripts/dpu.py плата
## сравнить: измерить C на текущем Mac и сравнить с числом тактов FPGA
сравнить:
	@.venv/bin/python scripts/benchmark.py
## сравнить-плату: измерить C и обмен с уже настроенным ядром FPGA
сравнить-плату:
	@.venv/bin/python scripts/benchmark.py --плата
## диагностика: собрать отдельный опыт генератора и DSP с проверкой управляющих битов
диагностика:
	@sh scripts/build_profile.sh probe build/диагностика.bit
## проверить-диагностику: проверить 192 произведения на уже загруженной диагностической конфигурации
проверить-диагностику:
	@.venv/bin/python scripts/probe_math.py
## проверить-автономность: проверить выполнение миллиона шагов в паузе без TCK
проверить-автономность:
	@.venv/bin/python scripts/autonomy.py
## проверить-интеграторы: проверить 72 полных произведения четырёх трактов рабочего ядра
проверить-интеграторы:
	@.venv/bin/python scripts/check_integrators.py
.PHONY: модель-программируемого-поля проверить-модель-поля проверить-поле поле поле-синтез сравнить-поле проверить-поле-плату программировать-поле сопоставить-поле
.PHONY: сетка-fclk программировать-сетку-fclk проверить-сетку-fclk-плату сравнить-сетку-fclk
## сетка-fclk: собрать фиксированный GRD1 от FCLK0 на выбранной частоте
сетка-fclk:
	@sh scripts/build_profile.sh grid-fclk build/сетка-fclk.bit
## программировать-сетку-fclk: временно загрузить проверенный образ от FCLK0
программировать-сетку-fclk:
	@test "$(ALLOW_FPGA_PROGRAM)" = YES || { echo "Нужно ALLOW_FPGA_PROGRAM=YES make программировать-сетку-fclk" >&2; exit 2; }
	@.venv/bin/python scripts/check_bitstream.py build/сетка-fclk.bit
	@.venv/bin/python scripts/ps_clock.py --проверить-образ build/сетка-fclk.bit
	@./scripts/program.sh build/сетка-fclk.bit
## проверить-сетку-fclk-плату: проверить все поля, ошибки и время фиксированного ядра от FCLK0
проверить-сетку-fclk-плату:
	@.venv/bin/python scripts/grid_board.py --профиль grid-fclk
## сравнить-сетку-fclk: заново измерить C/NEON и проверить все поля уже загруженного ядра FCLK
сравнить-сетку-fclk:
	@.venv/bin/python scripts/grid_benchmark.py --отчёт reports/сравнение-сетки-fclk.json
	@.venv/bin/python scripts/grid_board.py --профиль grid-fclk --процессор reports/сравнение-сетки-fclk.json
.PHONY: сетка-fclk8 программировать-сетку-fclk8 проверить-сетку-fclk8-плату сравнить-сетку-fclk8
## сетка-fclk8: собрать восемь фиксированных трактов GRD1 от FCLK0 на выбранной частоте
сетка-fclk8:
	@sh scripts/build_profile.sh grid-fclk8 build/сетка-fclk8.bit
## программировать-сетку-fclk8: временно загрузить проверенный образ от FCLK0
программировать-сетку-fclk8:
	@test "$(ALLOW_FPGA_PROGRAM)" = YES || { echo "Нужно ALLOW_FPGA_PROGRAM=YES make программировать-сетку-fclk8" >&2; exit 2; }
	@.venv/bin/python scripts/check_bitstream.py build/сетка-fclk8.bit
	@.venv/bin/python scripts/ps_clock.py --проверить-образ build/сетка-fclk8.bit
	@.venv/bin/python scripts/ps_pl_release.py --проверить-готовность
	@./scripts/program.sh build/сетка-fclk8.bit
## проверить-сетку-fclk8-плату: проверить все поля, ошибки и время фиксированного ядра от FCLK0
проверить-сетку-fclk8-плату:
	@.venv/bin/python scripts/grid_board.py --профиль grid-fclk8
## сравнить-сетку-fclk8: заново измерить C/NEON и проверить все поля уже загруженного ядра FCLK
сравнить-сетку-fclk8:
	@.venv/bin/python scripts/grid_benchmark.py --отчёт reports/сравнение-сетки-fclk8.json
	@.venv/bin/python scripts/grid_board.py --профиль grid-fclk8 --процессор reports/сравнение-сетки-fclk8.json
## модель-программируемого-поля: разместить разные графы и измерить модельную цену программируемости
модель-программируемого-поля:
	@.venv/bin/python scripts/field_program.py
## проверить-модель-поля: проверить загрузку графов, такты, задержки и общий отказ эпохи
проверить-модель-поля:
	@.venv/bin/python tb/field_program_test.py
## проверить-поле: проверить RTL программируемого поля и смену графов без пересборки
проверить-поле:
	@.venv/bin/python tb/dsp_placement_test.py
	@sh scripts/apio.sh lint -e field
	@GRID_PROGRAMMABLE=1 sh scripts/sim_grid.sh
## поле: собрать отдельный программируемый профиль после проверки частоты и DSP
поле:
	@sh scripts/build_profile.sh field build/поле-программируемое.bit
## поле-синтез: проверить программируемое поле на актуальной схеме после синтеза
поле-синтез:
	@GRID_PROGRAMMABLE=1 sh scripts/sim_grid.sh --после-синтеза
## сравнить-поле: проверить и измерить обычные C/NEON для тепла и реакции–диффузии
сравнить-поле:
	@.venv/bin/python scripts/field_benchmark.py
## сопоставить-поле: сопоставить сохранённые полные опыты GRD2 и C/NEON без обращения к плате
сопоставить-поле:
	@.venv/bin/python scripts/field_comparison.py
## проверить-поле-плату: сменить графы в уже загруженном GRD2 и проверить полные поля
проверить-поле-плату:
	@.venv/bin/python scripts/field_board.py
## программировать-поле: временно загрузить прошедший ограничения профиль GRD2 при явном разрешении
программировать-поле:
	@test "$(ALLOW_FPGA_PROGRAM)" = YES || { echo "Нужно ALLOW_FPGA_PROGRAM=YES make программировать-поле" >&2; exit 2; }
	@.venv/bin/python scripts/check_bitstream.py build/поле-программируемое.bit
	@./scripts/program.sh build/поле-программируемое.bit
## модель-сетки: сравнить 1, 2 и 4 тракта в потактовой модели сетки 128×128 без платы
модель-сетки:
	@.venv/bin/python scripts/grid_model.py
## проверить-модель-сетки: проверить числа, порты, паузы и общий отказ потактовой модели
проверить-модель-сетки:
	@.venv/bin/python tb/grid_model_test.py
## сравнить-сетку: измерить обычный C и NEON на тепловых сетках под Mac
сравнить-сетку:
	@.venv/bin/python scripts/grid_benchmark.py
## сравнить-сетку-плату: также прочитать частоту малого ядра; время большой FPGA пока прогнозное
сравнить-сетку-плату:
	@.venv/bin/python scripts/grid_benchmark.py --плата
## проверить-сетку: проверить пакетный обмен и полный тепловой RTL 64×64
проверить-сетку:
	@sh scripts/sim_grid.sh
## сетка: собрать отдельную проверенную прошивку теплового опыта 64×64
сетка:
	@sh scripts/build_profile.sh grid-safe build/сетка-64.bit
## сетка-125: отдельно проверить цель 125 МГц; при отказе прошивка не выдаётся
сетка-125:
	@sh scripts/build_profile.sh grid build/сетка-64-125.bit
## проверить-сетку-плату: загрузить поля в уже настроенное пакетное ядро и сверить результат
проверить-сетку-плату:
	@.venv/bin/python scripts/grid_board.py
## программировать-сетку: временно загрузить проверенный тепловой профиль при ALLOW_FPGA_PROGRAM=YES
программировать-сетку:
	@test "$(ALLOW_FPGA_PROGRAM)" = YES || { echo "Нужно ALLOW_FPGA_PROGRAM=YES make программировать-сетку" >&2; exit 2; }
	@.venv/bin/python scripts/check_bitstream.py build/сетка-64.bit
	@./scripts/program.sh build/сетка-64.bit
## synth: синтезировать проект открытыми средствами
synth:
	@$(APIO) build
## bitstream: собрать прошивку открытыми средствами
bitstream:
	@./scripts/openxc7_build.sh
## vivado-synth: выполнить контрольный синтез Vivado
vivado-synth:
	@./scripts/run_vivado.sh scripts/synth.tcl
## vivado-bitstream: собрать контрольную прошивку Vivado
vivado-bitstream:
	@./scripts/run_vivado.sh scripts/bitstream.tcl
## program: volatile PL programming (explicit latch required)
program:
	@test "$(ALLOW_FPGA_PROGRAM)" = YES || { echo "Refusing: run ALLOW_FPGA_PROGRAM=YES make program" >&2; exit 2; }
	@test -f build/fpga_madness.bit || { echo "build/fpga_madness.bit is missing" >&2; exit 2; }
	@.venv/bin/python scripts/check_bitstream.py build/fpga_madness.bit
	@./scripts/program.sh build/fpga_madness.bit
clean:
	@./scripts/clean.sh

.PHONY: сетка8 программировать-сетку8 проверить-сетку8-плату сравнить-сетку8
## сетка8: собрать восемь фиксированных трактов от внутреннего генератора
сетка8:
	@sh scripts/build_profile.sh grid8-safe build/сетка8-64.bit
## программировать-сетку8: временно загрузить проверенный восьмитрактный образ без настройки PS
программировать-сетку8:
	@test "$(ALLOW_FPGA_PROGRAM)" = YES || { echo "Нужно ALLOW_FPGA_PROGRAM=YES make программировать-сетку8" >&2; exit 2; }
	@.venv/bin/python scripts/check_bitstream.py build/сетка8-64.bit
	@.venv/bin/python scripts/ps_pl_release.py --проверить-готовность
	@FPGA_PROGRAMMER=$${FPGA_PROGRAMMER:-openocd} ./scripts/program.sh build/сетка8-64.bit
## проверить-сетку8-плату: проверить поля и отказы восьми фиксированных трактов
проверить-сетку8-плату:
	@.venv/bin/python scripts/grid_board.py --профиль grid8-safe
## сравнить-сетку8: заново измерить обычный C/NEON и выполнить полный аппаратный опыт восьми трактов
сравнить-сетку8:
	@.venv/bin/python scripts/grid_benchmark.py --отчёт reports/сравнение-сетки8.json
	@.venv/bin/python scripts/grid_board.py --профиль grid8-safe --процессор reports/сравнение-сетки8.json

.PHONY: собрать-N6 проверить-связь-N6 сравнить-N6
## собрать-N6: собрать скалярный C и Helium для NUCLEO-N657X0-Q, CMSIS без HAL
собрать-N6:
	@.venv/bin/python scripts/n6/build.py
## проверить-связь-N6: прочитать идентификаторы N6 через STLINK без остановки процессора
проверить-связь-N6:
	@.venv/bin/python scripts/n6/benchmark.py
## сравнить-N6: разрешённый временный опыт в SRAM, проверка полей и измерение C/Helium
сравнить-N6:
	@test "$(ALLOW_N6_RUN)" = YES || { echo "Нужно разрешение на опыт и ALLOW_N6_RUN=YES make сравнить-N6" >&2; exit 2; }
	@.venv/bin/python scripts/n6/benchmark.py --выполнить

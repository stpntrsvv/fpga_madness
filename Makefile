SHELL := /bin/sh
-include .env
export VIVADO_BIN VIVADO_HOST

.DEFAULT_GOAL := help
.PHONY: help doctor

help:
	@printf '%s\n' 'make doctor — проверить доступность инструментов' 'Новая аппаратная задача пока не настроена.' 'Прежние опыты: archive/processor-experiments/АРХИВ.md'

doctor:
	@./scripts/doctor.sh

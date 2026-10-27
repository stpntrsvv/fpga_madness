# Тот же проверенный D4: QMTECH Bajie V01, Figure 2-15, проверен 2026-09-10.
set_property PACKAGE_PIN H17 [get_ports user_led_n]
set_property IOSTANDARD LVCMOS33 [get_ports user_led_n]
# STARTUPE2.CFGMCLK: AMD UG953. Частота генератора требует измерения.
# 80 МГц — ограничение сборки с запасом относительно измеренных ~65 МГц.
# Это не заявленная частота генератора; цель 100 МГц пока не выполнена.
create_clock -name core_clk -period 12.500 [get_nets clk]
create_clock -name jtag_tck -period 1000.000 [get_nets jtag_clk]

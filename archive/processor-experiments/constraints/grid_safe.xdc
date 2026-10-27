# D4: QMTECH Bajie V01, Figure 2-15; назначение H17 проверено на плате.
set_property PACKAGE_PIN H17 [get_ports user_led_n]
set_property IOSTANDARD LVCMOS33 [get_ports user_led_n]
# Функциональный профиль для измеренного CFGMCLK около 65 МГц.
# Цель 125 МГц проверяется отдельно в constraints/grid.xdc.
create_clock -name core_clk -period 12.500 [get_nets clk]
create_clock -name jtag_tck -period 1000.000 [get_nets jtag_clk]

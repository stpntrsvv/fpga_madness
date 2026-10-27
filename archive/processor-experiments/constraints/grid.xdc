# D4: QMTECH Bajie V01, Figure 2-15; H17 и активный ноль проверены на плате.
set_property PACKAGE_PIN H17 [get_ports user_led_n]
set_property IOSTANDARD LVCMOS33 [get_ports user_led_n]
# 125 МГц — проверяемая цель размещения, а не частота CFGMCLK.
create_clock -name core_clk -period 8.000 [get_nets clk]
create_clock -name jtag_tck -period 1000.000 [get_nets jtag_clk]

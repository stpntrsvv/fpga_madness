# D4: QMTECH Bajie V01, Figure 2-15; H17 и полярность проверены на плате.
set_property PACKAGE_PIN H17 [get_ports user_led_n]
set_property IOSTANDARD LVCMOS33 [get_ports user_led_n]
# FCLK0 прочитан как IO_PLL / 5 / 2, номинально 100 МГц (2026-10-20).
# Это выбранный режим опыта, не обещание предельной частоты тракта.
create_clock -name core_clk -period 10.000 [get_nets engine.clk]
create_clock -name jtag_tck -period 1000.000 [get_nets engine.jtag_clk]

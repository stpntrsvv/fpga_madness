# QMTECH Bajie Board User Manual V01, Figure 2-15: D4 through R267 to 3V3.
# Active low; проверено на плате 2026-09-10.
set_property PACKAGE_PIN H17 [get_ports user_led_n]
set_property IOSTANDARD LVCMOS33 [get_ports user_led_n]

# BSCANE2.TCK: внутренний сигнал JTAG, UG953, BSCANE2, порт TCK.
# Проверяется отдельно от назначения H17; 1 МГц — частота первого опыта.
# Ограничиваем выход BUFG: nextpnr не переносит ограничение с tck_raw.
create_clock -name jtag_tck -period 1000.000 [get_nets clk]

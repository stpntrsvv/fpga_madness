# Обнаружение IDCODE после сброса не проверяет провод TDI.
# BYPASS требует только единиц в IR; затем проверяем прохождение нулей в DR.
irscan zynq_pl.bs 0x3f
set bypass [drscan zynq_pl.bs 32 0x00000000]
if {[expr "0x$bypass"] != 0} {
    error "Не проходят нули через BYPASS: $bypass. Проверьте TDI и контакты JTAG"
}
irscan zynq_pl.bs 0x09
set pl_id [drscan zynq_pl.bs 32 0x00000000]
if {[expr "0x$pl_id"] != 0x23727093} {
    error "Повторное чтение IDCODE PL не прошло: $pl_id"
}
echo "JTAG исправен: нули BYPASS проходят, IDCODE PL = $pl_id"

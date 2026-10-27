# Только TAP: Cortex-A9 не опрашиваются, не останавливаются и не сбрасываются.
transport select jtag
jtag newtap zynq_pl bs -irlen 6 -ircapture 0x1 -irmask 0x03 -expected-id 0x23727093
jtag newtap zynq cpu -irlen 4 -ircapture 0x1 -irmask 0x0f -expected-id 0x4ba00477
adapter speed 1000

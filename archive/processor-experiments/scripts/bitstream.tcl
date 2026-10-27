source [file join [file dirname [info script]] project.tcl]
synth_design -top $top -part $part
opt_design
place_design
phys_opt_design
route_design
report_drc -file [file join $reports_dir post_route_drc.rpt]
report_utilization -file [file join $reports_dir post_route_utilization.rpt]
report_timing_summary -file [file join $reports_dir post_route_timing.rpt]
write_checkpoint -force [file join $build_dir post_route.dcp]
write_bitstream -force [file join $build_dir fpga_madness.bit]


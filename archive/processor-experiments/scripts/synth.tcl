source [file join [file dirname [info script]] project.tcl]
synth_design -top $top -part $part
write_checkpoint -force [file join $build_dir post_synth.dcp]
report_utilization -file [file join $reports_dir post_synth_utilization.rpt]
report_timing_summary -file [file join $reports_dir post_synth_timing.rpt]


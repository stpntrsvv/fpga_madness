set origin_dir [file normalize [file join [file dirname [info script]] ..]]
set build_dir [file join $origin_dir build]
set reports_dir [file join $origin_dir reports]
file mkdir $build_dir
file mkdir $reports_dir
set part xc7z020clg400-1
set top top
read_verilog -sv [lsort [glob [file join $origin_dir rtl *.sv]]]
read_xdc [file join $origin_dir constraints probe.xdc]


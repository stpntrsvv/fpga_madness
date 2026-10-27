`default_nettype none
module top (output logic user_led_n);
    wire tck_raw, jtag_clk, clk_raw, clk, selected, capture, shift, update, tdi, tdo;
    wire busy, done;
    wire [7:0] fault;
    logic [2:0] startup = 0;
    logic [2:0] jtag_startup = 0;
    wire rst = !(&startup);
    wire tck_rst = !(&jtag_startup);

    // JTAG управляет обменом; вычислительное ядро имеет независимый генератор.
    BSCANE2 #(.JTAG_CHAIN(1)) boundary_scan (
        .TCK(tck_raw), .SEL(selected), .CAPTURE(capture), .SHIFT(shift),
        .UPDATE(update), .TDI(tdi), .TDO(tdo),
        .DRCK(), .RESET(), .RUNTEST(), .TMS()
    );
    STARTUPE2 oscillator (
        .CFGMCLK(clk_raw), .CFGCLK(), .EOS(), .PREQ(),
        .CLK(1'b0), .GSR(1'b0), .GTS(1'b0), .KEYCLEARB(1'b1), .PACK(1'b0),
        .USRCCLKO(1'b0), .USRCCLKTS(1'b1), .USRDONEO(1'b1), .USRDONETS(1'b1)
    );
    BUFG clock_buffer (.I(clk_raw), .O(clk));
    BUFG jtag_clock_buffer (.I(tck_raw), .O(jtag_clk));
    always_ff @(posedge clk) if (!(&startup)) startup <= startup + 1;
    always_ff @(posedge jtag_clk) if (!(&jtag_startup)) jtag_startup <= jtag_startup + 1;
    dpu_jtag engine (
        .tck(jtag_clk), .tck_rst(tck_rst), .clk(clk), .rst(rst), .selected(selected), .capture(capture),
        .shift(shift), .update(update), .tdi(tdi), .tdo(tdo),
        .busy(busy), .done(done), .fault(fault)
    );
    assign user_led_n = !(done && fault == 0 && !busy);
endmodule
`default_nettype wire

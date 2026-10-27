`default_nettype none
module field_top (output wire user_led_n);
    wire tck_raw, jtag_clk, clk_raw, clk, selected, capture, shift, update, tdi, tdo;
    logic [2:0] startup = 0, jtag_startup = 0;
    wire rst = !(&startup), tck_rst = !(&jtag_startup);
    wire command_valid, reply_valid, busy, done;
    wire [7:0] command, fault;
    wire [15:0] address;
    wire [31:0] write_data, reply;
    wire [1023:0] payload, read_payload;
    BSCANE2 #(.JTAG_CHAIN(1)) boundary_scan (
        .TCK(tck_raw), .SEL(selected), .CAPTURE(capture), .SHIFT(shift), .UPDATE(update),
        .TDI(tdi), .TDO(tdo), .DRCK(), .RESET(), .RUNTEST(), .TMS());
    STARTUPE2 oscillator (
        .CFGMCLK(clk_raw), .CFGCLK(), .EOS(), .PREQ(), .CLK(1'b0), .GSR(1'b0), .GTS(1'b0),
        .KEYCLEARB(1'b1), .PACK(1'b0), .USRCCLKO(1'b0), .USRCCLKTS(1'b1), .USRDONEO(1'b1), .USRDONETS(1'b1));
    BUFG clock_buffer (.I(clk_raw), .O(clk));
    BUFG jtag_clock_buffer (.I(tck_raw), .O(jtag_clk));
    always_ff @(posedge clk) if (!(&startup)) startup <= startup + 1;
    always_ff @(posedge jtag_clk) if (!(&jtag_startup)) jtag_startup <= jtag_startup + 1;
    field_link link (.tck(jtag_clk), .tck_rst(tck_rst), .clk(clk), .rst(rst), .selected(selected),
        .capture(capture), .shift(shift), .update(update), .tdi(tdi), .tdo(tdo),
        .command_valid(command_valid), .command(command), .address(address), .write_data(write_data),
        .payload(payload), .reply(reply), .read_payload(read_payload), .reply_valid(reply_valid));
    field_core core (.clk(clk), .rst(rst), .command_valid(command_valid), .command(command),
        .address(address), .write_data(write_data), .payload(payload), .reply(reply),
        .read_payload(read_payload), .reply_valid(reply_valid), .busy(busy), .done(done), .fault(fault));
    assign user_led_n = !(done && !busy && fault == 0);
endmodule
`default_nettype wire

`default_nettype none
module dpu_jtag (
    input wire tck,
    input wire tck_rst,
    input wire clk,
    input wire rst,
    input wire selected,
    input wire capture,
    input wire shift,
    input wire update,
    input wire tdi,
    output wire tdo,
    output wire busy,
    output wire done,
    output wire [7:0] fault
);
    wire command_valid;
    wire [7:0] command;
    wire [15:0] address;
    wire [31:0] write_data;
    wire [31:0] reply;
    wire reply_valid;
    dpu_link link (
        .tck(tck), .tck_rst(tck_rst), .clk(clk), .rst(rst),
        .selected(selected), .capture(capture), .shift(shift), .update(update),
        .tdi(tdi), .tdo(tdo), .command_valid(command_valid), .command(command),
        .address(address), .write_data(write_data), .reply(reply), .reply_valid(reply_valid)
    );

    dpu_core core (
        .clk(clk), .rst(rst), .command_valid(command_valid), .command(command),
        .address(address), .write_data(write_data), .reply(reply), .reply_valid(reply_valid),
        .busy(busy), .done(done), .fault(fault)
    );
endmodule
`default_nettype wire

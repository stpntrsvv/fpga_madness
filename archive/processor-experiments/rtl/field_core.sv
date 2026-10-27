`default_nettype none
module field_core (
    input wire clk,rst,command_valid,
    input wire [7:0] command,
    input wire [15:0] address,
    input wire [31:0] write_data,
    input wire [1023:0] payload,
    output wire [31:0] reply,
    output wire [1023:0] read_payload,
    output wire reply_valid,busy,done,
    output wire [7:0] fault
);
    grid_core #(.PROGRAMMABLE(1)) core (.*);
endmodule
`default_nettype wire

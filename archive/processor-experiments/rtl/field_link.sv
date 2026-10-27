`default_nettype none
module field_link (
    input wire tck,tck_rst,clk,rst,selected,capture,shift,update,tdi,
    output wire tdo,command_valid,
    output wire [7:0] command,
    output wire [15:0] address,
    output wire [31:0] write_data,
    output wire [1023:0] payload,
    input wire [31:0] reply,
    input wire [1023:0] read_payload,
    input wire reply_valid
);
    grid_link #(.SIGNATURE(32'h47524432)) transport (.*);
endmodule
`default_nettype wire

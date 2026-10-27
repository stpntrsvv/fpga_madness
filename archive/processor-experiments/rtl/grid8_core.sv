`default_nettype none
module grid8_core (
    input wire clk, rst, command_valid,
    input wire [7:0] command,
    input wire [15:0] address,
    input wire [31:0] write_data,
    input wire [1023:0] payload,
    output logic [31:0] reply,
    output logic [1023:0] read_payload,
    output logic reply_valid, busy, done,
    output logic [7:0] fault
);
    grid_core #(.LANES(8)) engine (.*);
endmodule
`default_nettype wire

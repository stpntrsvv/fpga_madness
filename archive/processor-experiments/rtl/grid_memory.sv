`default_nettype none
// Два синхронных порта чтения; запись занимает порт A. Содержимое не сбрасывается.
module grid_memory #(parameter integer ADDRESS_BITS = 10) (
    input wire clk, write_enable,
    input wire [ADDRESS_BITS-1:0] address_a, address_b,
    input wire [31:0] write_data,
    output logic [31:0] read_a, read_b
);
    (* ram_style = "block" *) logic [31:0] words [0:(1<<ADDRESS_BITS)-1];
    always_ff @(posedge clk) begin
        if (write_enable) words[address_a] <= write_data;
        else read_a <= words[address_a];
        read_b <= words[address_b];
    end
endmodule

module grid_delay #(parameter integer WIDTH = 1, DEPTH = 4) (
    input wire clk,
    input wire [WIDTH-1:0] data,
    output wire [WIDTH-1:0] delayed
);
    logic [WIDTH-1:0] stages [0:DEPTH-1];
    always_ff @(posedge clk) begin
        stages[0] <= data;
        for (integer i = 1; i < DEPTH; i = i + 1) stages[i] <= stages[i-1];
    end
    assign delayed = stages[DEPTH-1];
endmodule
`default_nettype wire

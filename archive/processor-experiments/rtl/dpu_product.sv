`default_nettype none
module dpu_partial_product (
    input wire [15:0] a,
    input wire [15:0] b,
    output wire [31:0] product
);
    // Независимое частичное произведение; рабочая сборка задаёт -nodsp.
    assign product = a * b;
endmodule

module dpu_product (
    input wire signed [31:0] a,
    input wire signed [31:0] b,
    output wire signed [63:0] product
);
    wire [31:0] low_low, low_high, high_low, high_high;
    wire [63:0] unsigned_product;
    dpu_partial_product p00 (.a(a[15:0]), .b(b[15:0]), .product(low_low));
    dpu_partial_product p01 (.a(a[15:0]), .b(b[31:16]), .product(low_high));
    dpu_partial_product p10 (.a(a[31:16]), .b(b[15:0]), .product(high_low));
    dpu_partial_product p11 (.a(a[31:16]), .b(b[31:16]), .product(high_high));
    assign unsigned_product = {32'b0, low_low} + {16'b0, low_high, 16'b0} +
                              {16'b0, high_low, 16'b0} + {high_high, 32'b0};
    // Исправление беззнакового произведения в арифметике по модулю 2^64.
    assign product = unsigned_product - (a[31] ? {b, 32'b0} : 64'b0) -
                                       (b[31] ? {a, 32'b0} : 64'b0);
endmodule
`default_nettype wire

`default_nettype none
module dpu_dsp_partial (
    input wire clk,
    input wire signed [16:0] a, b,
    output wire signed [33:0] product
);
    wire [47:0] p;
    // Две стадии внутри DSP: входные регистры и регистр произведения.
    // Все входы управления заданы явно; каскадных соединений нет.
    DSP48E1 #(
        .A_INPUT("DIRECT"), .B_INPUT("DIRECT"), .USE_DPORT("FALSE"),
        .USE_MULT("MULTIPLY"), .USE_SIMD("ONE48"),
        .AREG(1), .ACASCREG(1), .BREG(1), .BCASCREG(1), .MREG(1), .PREG(0),
        .ADREG(0), .DREG(0), .CREG(0), .ALUMODEREG(0), .CARRYINREG(0),
        .CARRYINSELREG(0), .INMODEREG(0), .OPMODEREG(0)
    ) dsp (
        .CLK(clk), .A({{13{a[16]}}, a}), .B({b[16], b}), .C(48'b0), .D(25'b0),
        .ACIN(30'b0), .BCIN(18'b0), .PCIN(48'b0),
        .INMODE(5'b0), .OPMODE(7'b0000101), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0), .MULTSIGNIN(1'b0),
        .CEA1(1'b1), .CEA2(1'b1), .CEB1(1'b1), .CEB2(1'b1), .CEM(1'b1), .CEP(1'b1),
        .CEAD(1'b1), .CED(1'b1), .CEC(1'b1), .CEALUMODE(1'b1),
        .CECARRYIN(1'b1), .CECTRL(1'b1), .CEINMODE(1'b1),
        .RSTA(1'b0), .RSTB(1'b0), .RSTM(1'b0), .RSTP(1'b0), .RSTC(1'b0), .RSTD(1'b0),
        .RSTCTRL(1'b0), .RSTINMODE(1'b0), .RSTALUMODE(1'b0), .RSTALLCARRYIN(1'b0),
        .P(p), .ACOUT(), .BCOUT(), .PCOUT(), .CARRYOUT(), .CARRYCASCOUT(),
        .MULTSIGNOUT(), .OVERFLOW(), .UNDERFLOW(), .PATTERNDETECT(), .PATTERNBDETECT()
    );
    assign product = p[33:0];
endmodule

// Полное знаковое произведение. Приём на фронте t, выход после фронта t+3.
module dpu_product_pipe (
    input wire clk, rst, valid,
    input wire signed [31:0] a, b,
    output logic signed [63:0] product,
    output wire ready
);
    // Нуль данных идёт от сохранённого LUT, минуя особую трассировку GND.
    // На плате обнаружен пропущенный маршрут GND к A24 одного DSP.
    (* keep = 1 *) wire data_zero;
    (* keep = 1 *) LUT1 #(.INIT(2'b00)) zero_driver (.I0(a[0]), .O(data_zero));
    wire signed [16:0] al = {data_zero, a[15:0]}, ah = {a[31], a[31:16]};
    wire signed [16:0] bl = {data_zero, b[15:0]}, bh = {b[31], b[31:16]};
    wire signed [33:0] p00, p01, p10, p11;
    logic signed [63:0] sum_low, sum_high;
    logic [3:0] valid_pipe;
    dpu_dsp_partial m00 (.clk(clk), .a(al), .b(bl), .product(p00));
    dpu_dsp_partial m01 (.clk(clk), .a(al), .b(bh), .product(p01));
    dpu_dsp_partial m10 (.clk(clk), .a(ah), .b(bl), .product(p10));
    dpu_dsp_partial m11 (.clk(clk), .a(ah), .b(bh), .product(p11));
    assign ready = valid_pipe[3];
    always_ff @(posedge clk) begin
        // Сбрасывается допустимость, а не данные каждого арифметического регистра.
        // Старые значения не принимаются; новые проходят все четыре стадии.
        if (rst) valid_pipe <= 0;
        else valid_pipe <= {valid_pipe[2:0], valid};
        sum_low <= {{30{p00[33]}}, p00} + {{14{p01[33]}}, p01, 16'b0};
        sum_high <= {{14{p10[33]}}, p10, 16'b0} + {p11[31:0], 32'b0};
        product <= sum_low + sum_high;
    end
endmodule
`default_nettype wire

`default_nettype none
// Тепловой опыт, четыре полных произведения на тракт. Граф здесь фиксирован.
module grid_heat_lane #(parameter integer LANE = 0, LANES = 4) (
    input wire clk, rst, valid,
    input wire [9:0] index,
    input wire signed [31:0] center, east, south, ge, gs, ci, source, dt,
    input wire signed [31:0] west_flux,
    output wire signed [31:0] east_flux,
    output wire flux_valid,
    output wire [9:0] flux_index,
    output logic result_valid, result_fault,
    output logic [9:0] result_index,
    output logic [31:0] result
);
    localparam integer COLUMN_BITS = $clog2(64/LANES);
    function automatic overflow64(input logic signed [63:0] value);
        overflow64 = value != {{32{value[31]}}, value[31:0]};
    endfunction
    function automatic overflow33(input logic signed [32:0] value);
        overflow33 = value[32] != value[31];
    endfunction
    wire last_east = LANE == LANES-1 && index[COLUMN_BITS-1:0] == COLUMN_BITS'(64/LANES-1);
    wire last_south = (index >> COLUMN_BITS) == 63;
    wire signed [32:0] de = $signed({east[31], east}) - $signed({center[31], center});
    wire signed [32:0] ds = $signed({south[31], south}) - $signed({center[31], center});
    logic signed [31:0] delta_e, delta_s, g_e, g_s;
    logic [106:0] start_meta;
    logic start_valid;
    // Метаданные: Q[106:75], invC[74:43], T[42:11], индекс[10:1], ошибка[0].
    always_ff @(posedge clk) begin
        start_valid <= !rst && valid;
        delta_e <= last_east ? 32'sd0 : de[31:0];
        delta_s <= last_south ? 32'sd0 : ds[31:0];
        g_e <= ge;
        g_s <= gs;
        start_meta <= {source, ci, center, index,
                       ((!last_east && overflow33(de)) || (!last_south && overflow33(ds)))};
    end
    wire signed [63:0] ep, sp;
    wire e_valid, s_valid;
    dpu_product_pipe em (.clk(clk), .rst(rst), .valid(start_valid), .a(g_e), .b(delta_e), .product(ep), .ready(e_valid));
    dpu_product_pipe sm (.clk(clk), .rst(rst), .valid(start_valid), .a(g_s), .b(delta_s), .product(sp), .ready(s_valid));
    wire [106:0] flux_meta;
    grid_delay #(.WIDTH(107)) flux_metadata (.clk(clk), .data(start_meta), .delayed(flux_meta));
    assign east_flux = ep[47:16];
    assign flux_valid = e_valid && s_valid;
    assign flux_index = flux_meta[10:1];

    // Одна строка южных потоков на тракт. Читается прежняя строка, затем записывается новая.
    (* ram_style = "distributed" *) logic signed [31:0] north_line [0:64/LANES-1];
    logic signed [31:0] north_read, align_e, align_s, align_w;
    logic [106:0] align_meta;
    logic align_valid;
    always_ff @(posedge clk) begin
        align_valid <= !rst && flux_valid;
        if (flux_valid) begin
            north_read <= (flux_index >> COLUMN_BITS) == 0 ? 32'sd0 : north_line[flux_index[COLUMN_BITS-1:0]];
            north_line[flux_index[COLUMN_BITS-1:0]] <= sp[47:16];
        end
        align_e <= ep[47:16];
        align_s <= sp[47:16];
        align_w <= west_flux;
        align_meta <= {flux_meta[106:1], flux_meta[0] ||
                       overflow64(ep >>> 16) || overflow64(sp >>> 16)};
    end

    logic signed [31:0] balance [0:3];
    logic [106:0] balance_meta [0:3];
    logic [3:0] balance_valid;
    logic signed [31:0] west_delay, north_delay [0:1];
    wire signed [32:0] b0 = $signed({align_e[31], align_e}) + $signed({align_s[31], align_s});
    wire signed [32:0] b1 = $signed({balance[0][31], balance[0]}) - $signed({west_delay[31], west_delay});
    wire signed [32:0] b2 = $signed({balance[1][31], balance[1]}) - $signed({north_delay[1][31], north_delay[1]});
    wire signed [32:0] b3 = $signed({balance[2][31], balance[2]}) +
                            $signed({balance_meta[2][106], balance_meta[2][106:75]});
    always_ff @(posedge clk) begin
        balance_valid <= rst ? 4'b0 : {balance_valid[2:0], align_valid};
        west_delay <= align_w;
        north_delay[0] <= north_read;
        north_delay[1] <= north_delay[0];
        balance[0] <= b0[31:0]; balance[1] <= b1[31:0];
        balance[2] <= b2[31:0]; balance[3] <= b3[31:0];
        balance_meta[0] <= {align_meta[106:1], align_meta[0] || overflow33(b0)};
        balance_meta[1] <= {balance_meta[0][106:1], balance_meta[0][0] || overflow33(b1)};
        balance_meta[2] <= {balance_meta[1][106:1], balance_meta[1][0] || overflow33(b2)};
        balance_meta[3] <= {balance_meta[2][106:1], balance_meta[2][0] || overflow33(b3)};
    end
    wire signed [63:0] derivative_product, transition_product;
    wire derivative_valid, transition_valid;
    wire [106:0] derivative_meta, transition_meta;
    dpu_product_pipe dm (.clk(clk), .rst(rst), .valid(balance_valid[3]),
        .a(balance[3]), .b(balance_meta[3][74:43]), .product(derivative_product), .ready(derivative_valid));
    grid_delay #(.WIDTH(107)) derivative_metadata (.clk(clk), .data(balance_meta[3]), .delayed(derivative_meta));
    wire [106:0] checked_derivative = {derivative_meta[106:1],
                                      derivative_meta[0] || overflow64(derivative_product >>> 16)};
    dpu_product_pipe tm (.clk(clk), .rst(rst), .valid(derivative_valid),
        .a(dt), .b(derivative_product[47:16]), .product(transition_product), .ready(transition_valid));
    grid_delay #(.WIDTH(107)) transition_metadata (.clk(clk), .data(checked_derivative), .delayed(transition_meta));
    wire signed [63:0] next_wide = $signed({{32{transition_meta[42]}}, transition_meta[42:11]}) +
                                  (transition_product >>> 16);
    always_ff @(posedge clk) begin
        result_valid <= !rst && transition_valid;
        result <= next_wide[31:0];
        result_index <= transition_meta[10:1];
        result_fault <= transition_meta[0] || overflow64(next_wide);
    end
endmodule
`default_nettype wire

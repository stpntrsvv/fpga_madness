`default_nettype none
// Отдельный опыт: свободный генератор и четыре зарегистрированных DSP.
module dpu_probe_top (output wire user_led_n);
    wire tck_raw, jtag_clk, clk_raw, clk;
    wire selected, capture, shift, update, tdi, tdo;
    wire eos;
    logic [3:0] core_startup = 0, jtag_startup = 0;
    wire rst = !(&core_startup), tck_rst = !(&jtag_startup);
    BSCANE2 #(.JTAG_CHAIN(1)) boundary_scan (
        .TCK(tck_raw), .SEL(selected), .CAPTURE(capture), .SHIFT(shift),
        .UPDATE(update), .TDI(tdi), .TDO(tdo), .DRCK(), .RESET(), .RUNTEST(), .TMS()
    );
    STARTUPE2 oscillator (
        .CFGMCLK(clk_raw), .CFGCLK(), .EOS(eos), .PREQ(),
        .CLK(1'b0), .GSR(1'b0), .GTS(1'b0), .KEYCLEARB(1'b1), .PACK(1'b0),
        .USRCCLKO(1'b0), .USRCCLKTS(1'b1), .USRDONEO(1'b1), .USRDONETS(1'b1)
    );
    BUFG core_clock_buffer (.I(clk_raw), .O(clk));
    BUFG jtag_clock_buffer (.I(tck_raw), .O(jtag_clk));
    always_ff @(posedge clk) if (!(&core_startup)) core_startup <= core_startup + 1;
    always_ff @(posedge jtag_clk) if (!(&jtag_startup)) jtag_startup <= jtag_startup + 1;
    wire command_valid;
    wire [7:0] command;
    wire [15:0] address;
    wire [31:0] write_data;
    logic [31:0] reply;
    logic reply_valid;
    logic signed [31:0] a, b;
    logic [63:0] ticks;
    logic [31:0] ticks_high, product_high;
    wire signed [63:0] product;
    wire product_ready;
    dpu_link link (
        .tck(jtag_clk), .tck_rst(tck_rst), .clk(clk), .rst(rst),
        .selected(selected), .capture(capture), .shift(shift), .update(update),
        .tdi(tdi), .tdo(tdo), .command_valid(command_valid), .command(command),
        .address(address), .write_data(write_data), .reply(reply), .reply_valid(reply_valid)
    );
    dpu_product_pipe multiply (.clk(clk), .rst(rst), .valid(1'b1),
        .a(a), .b(b), .product(product), .ready(product_ready));
    assign user_led_n = !product_ready;
    always_ff @(posedge clk) begin
        if (rst) begin
            a <= 0; b <= 0; ticks <= 0; reply <= 0; reply_valid <= 0; ticks_high <= 0; product_high <= 0;
        end else begin
            ticks <= ticks + 1;
            reply_valid <= command_valid;
            if (command_valid) begin
                reply <= 0;
                case (command)
                    1: begin a <= 0; b <= 0; end
                    2: case (address)
                        'h700: a <= write_data;
                        'h701: b <= write_data;
                        default: reply <= 32'hbad00002;
                    endcase
                    3: case (address)
                        0: reply <= 32'h50524232;
                        5: begin reply <= ticks[31:0]; ticks_high <= ticks[63:32]; end
                        6: reply <= ticks_high;
                        'h700: reply <= a;
                        'h701: reply <= b;
                        'h702: begin reply <= product[31:0]; product_high <= product[63:32]; end
                        'h703: reply <= product_high;
                        default: reply <= 32'hbad00002;
                    endcase
                    default: reply <= 32'hbad00004;
                endcase
            end
        end
    end
endmodule
`default_nettype wire

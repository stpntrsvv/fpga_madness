`default_nettype none
// Постоянная физическая стадия. Источники и назначение загружаются до RUN.
// Нет памяти арифметических команд или счётчика их последовательной выдачи.
// Дорожка 0 переносит исходное T и не является назначением операции.
module field_stage #(parameter integer TRACKS=7, parameter bit MULTIPLY=0,
                     parameter integer LATENCY=MULTIPLY ? 4 : 2) (
    input wire clk, rst, valid,
    input wire [31:0] configuration,
    input wire [63:0] literals,
    input wire [TRACKS*32-1:0] tracks,
    input wire [10:0] metadata,
    output wire output_valid,
    output wire [TRACKS*32-1:0] output_tracks,
    output wire [10:0] output_metadata
);
    wire [223:0] sources;
    for (genvar t=0;t<7;t=t+1) begin : sources_bus
        if (t<TRACKS) assign sources[t*32+:32]=tracks[t*32+:32];
        else assign sources[t*32+:32]=0;
    end
    // Константа выбрана общим трактом настройки заранее, вне пути каждой точки.
    wire signed [31:0] a=configuration[6:3]<7 ? sources[int'(configuration[5:3])*32+:32] :
        configuration[6:3]==15 ? 32'sd0 : $signed(literals[31:0]);
    wire signed [31:0] b=configuration[10:7]<7 ? sources[int'(configuration[9:7])*32+:32] :
        configuration[10:7]==15 ? 32'sd0 : $signed(literals[63:32]);
    wire [TRACKS*32-1:0] delayed_tracks;
    wire [10:0] delayed_metadata;
    grid_delay #(.WIDTH(TRACKS*32),.DEPTH(LATENCY)) data_delay (.clk(clk),.data(tracks),.delayed(delayed_tracks));
    grid_delay #(.WIDTH(11),.DEPTH(LATENCY)) metadata_delay (.clk(clk),.data(metadata),.delayed(delayed_metadata));
    wire signed [31:0] result;
    wire arithmetic_fault;
    if (MULTIPLY) begin : multiply
        wire signed [63:0] product;
        dpu_product_pipe arithmetic (.clk(clk),.rst(rst),.valid(valid),.a(a),.b(b),
            .product(product),.ready(output_valid));
        wire signed [63:0] scaled_product=product >>> 16;
        assign result=product[47:16];
        assign arithmetic_fault=scaled_product != {{32{product[47]}},product[47:16]};
    end else begin : simple
        logic signed [31:0] operand_a, operand_b, result_register;
        logic overflow_register;
        logic [1:0] validity;
        wire signed [32:0] wide=configuration[2:0]==1 ? $signed({operand_a[31],operand_a}) :
            configuration[2:0]==2 ? $signed({operand_a[31],operand_a})+$signed({operand_b[31],operand_b}) :
                                   $signed({operand_a[31],operand_a})-$signed({operand_b[31],operand_b});
        always_ff @(posedge clk) begin
            operand_a<=a;operand_b<=b;
            result_register<=wide[31:0];overflow_register<=wide[32]!=wide[31];
            validity<=rst ? 2'b0 : {validity[0],valid};
        end
        assign result=result_register;
        assign arithmetic_fault=overflow_register;
        assign output_valid=validity[1];
    end
    for (genvar t=0;t<TRACKS;t=t+1) begin : result_bus
        if (t==0) assign output_tracks[t*32+:32]=delayed_tracks[t*32+:32];
        else assign output_tracks[t*32+:32]=configuration[2:0]!=0 && configuration[13:11]==3'(t) ?
                result : delayed_tracks[t*32+:32];
    end
    assign output_metadata={delayed_metadata[10] || (configuration[2:0]!=0 && arithmetic_fault),delayed_metadata[9:0]};
endmodule

module field_graph #(parameter bit EDGE=0, parameter integer TRACKS=EDGE ? 3 : 7, STAGES=EDGE ? 2 : 11) (
    input wire clk,rst,valid,
    input wire [STAGES*32-1:0] configurations,
    input wire [STAGES*64-1:0] literals,
    input wire [TRACKS*32-1:0] tracks,
    input wire [10:0] metadata,
    output wire output_valid,
    output wire [TRACKS*32-1:0] output_tracks,
    output wire [10:0] output_metadata
);
    wire [TRACKS*32-1:0] values [0:STAGES];
    wire [10:0] information [0:STAGES];
    wire [STAGES:0] validity;
    assign values[0]=tracks;assign information[0]=metadata;assign validity[0]=valid;
    for (genvar s=0;s<STAGES;s=s+1) begin : stages
        localparam bit MUL=EDGE ? s==1 : (s>=4 && s<9);
        localparam integer STAGE_TRACKS=EDGE ? 3 : (s<4 ? 7 : 4);
        wire [STAGE_TRACKS*32-1:0] stage_values;
        field_stage #(.TRACKS(STAGE_TRACKS),.MULTIPLY(MUL)) stage (
            .clk(clk),.rst(rst),.valid(validity[s]),.configuration(configurations[s*32+:32]),
            .literals(literals[s*64+:64]),.tracks(values[s][STAGE_TRACKS*32-1:0]),.metadata(information[s]),
            .output_valid(validity[s+1]),.output_tracks(stage_values),.output_metadata(information[s+1]));
        assign values[s+1]={{(TRACKS-STAGE_TRACKS)*32{1'b0}},stage_values};
    end
    assign output_valid=validity[STAGES];assign output_tracks=values[STAGES];
    assign output_metadata=information[STAGES];
endmodule
`default_nettype wire

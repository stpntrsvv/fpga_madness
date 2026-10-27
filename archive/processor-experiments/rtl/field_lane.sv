`default_nettype none
// Два настроенных графа связи и один локальный граф на поток точек.
module field_lane #(parameter integer LANE=0) (
    input wire clk,rst,valid,
    input wire [9:0] index,
    input wire signed [31:0] center,east,south,ge,gs,ci,source,dt,west_flux,
    input wire [415:0] configurations,
    input wire [255:0] constants,
    input wire [831:0] literals,
    input wire [3:0] edge_output,local_output,
    output wire signed [31:0] east_flux,
    output wire flux_valid,
    output wire [9:0] flux_index,
    output logic result_valid,result_fault,
    output logic [9:0] result_index,
    output logic [31:0] result
);
    function automatic signed [31:0] select_source(input logic [3:0] selector,
        input logic [223:0] values, input logic [255:0] coefficients);
        logic [511:0] bus_values;
        begin
            bus_values={32'b0,coefficients,values};
            select_source=bus_values[int'(selector)*32+:32];
        end
    endfunction
    wire [95:0] edge_values [0:1];
    wire [10:0] edge_meta [0:1];
    wire [1:0] edge_valid;
    for (genvar d=0;d<2;d=d+1) begin : directions
        wire [95:0] input_values=d==0 ? {ge,east,center} : {gs,south,center};
        field_graph #(.EDGE(1)) graph (.clk(clk),.rst(rst),.valid(valid),
            .configurations(configurations[63:0]),.literals(literals[127:0]),.tracks(input_values),
            .metadata({1'b0,index}),.output_valid(edge_valid[d]),
            .output_tracks(edge_values[d]),.output_metadata(edge_meta[d]));
    end
    wire east_present=!(LANE==3 && edge_meta[0][3:0]==15);
    wire south_present=edge_meta[0][9:4]!=63;
    wire signed [31:0] south_flux=south_present ? select_source(edge_output,{128'b0,edge_values[1]},constants) : 0;
    assign east_flux=east_present ? select_source(edge_output,{128'b0,edge_values[0]},constants) : 0;
    assign flux_valid=&edge_valid;
    assign flux_index=edge_meta[0][9:0];
    wire [63:0] delayed_maps;
    grid_delay #(.WIDTH(64),.DEPTH(6)) maps_delay (.clk(clk),.data({source,ci}),.delayed(delayed_maps));
    (* ram_style="distributed" *) logic signed [31:0] north_line [0:15];
    logic [223:0] aligned_values;
    logic [10:0] aligned_meta;
    logic aligned_valid;
    always_ff @(posedge clk) begin
        aligned_valid<=!rst && flux_valid;
        if (flux_valid) north_line[flux_index[3:0]]<=south_flux;
        aligned_values<={flux_index[9:4]==0 ? 32'b0 : north_line[flux_index[3:0]],west_flux,south_flux,east_flux,
                         delayed_maps,edge_values[0][31:0]};
        aligned_meta<={(east_present && edge_meta[0][10]) || (south_present && edge_meta[1][10]),edge_meta[0][9:0]};
    end
    wire [223:0] local_values;
    wire [10:0] local_meta;
    wire local_valid;
    field_graph graph (.clk(clk),.rst(rst),.valid(aligned_valid),.configurations(configurations[415:64]),
        .literals(literals[831:128]),.tracks(aligned_values),.metadata(aligned_meta),
        .output_valid(local_valid),.output_tracks(local_values),.output_metadata(local_meta));
    wire signed [31:0] derivative=select_source(local_output,local_values,constants);
    wire signed [63:0] product;
    wire product_valid;
    dpu_product_pipe integrate (.clk(clk),.rst(rst),.valid(local_valid),.a(dt),.b(derivative),
        .product(product),.ready(product_valid));
    wire [42:0] transition_meta;
    grid_delay #(.WIDTH(43),.DEPTH(4)) transition_delay (.clk(clk),
        .data({local_meta[10],local_values[31:0],local_meta[9:0]}),.delayed(transition_meta));
    wire signed [63:0] next_wide=$signed({{32{transition_meta[41]}},transition_meta[41:10]})+(product >>> 16);
    always_ff @(posedge clk) begin
        result_valid<=!rst && product_valid;
        result<=next_wide[31:0];result_index<=transition_meta[9:0];
        result_fault<=transition_meta[42] || next_wide!={{32{next_wide[31]}},next_wide[31:0]};
    end
endmodule
`default_nettype wire

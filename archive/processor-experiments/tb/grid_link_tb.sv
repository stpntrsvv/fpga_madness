`timescale 1ns/1ps
`default_nettype none
module grid_link_tb;
    logic clk=0, tck=0, rst=1, tck_rst=1;
    always #4 clk=!clk;
    logic selected=1, capture=0, shift=0, update=0, tdi=0;
    wire tdo, command_valid;
    wire [7:0] command;
    wire [15:0] address;
    wire [31:0] write_data;
    wire [1023:0] payload;
    logic [31:0] reply=0;
    logic [1023:0] read_payload=0;
    logic reply_valid=0, allow_reply=1, waiting=0;
    logic [1023:0] final_payload;
    logic [31:0] final_reply;
    integer accepted=0;
`ifdef FIELD_PROGRAMMABLE
    localparam [31:0] SIGNATURE=32'h47524432;
    field_link dut (.*);
`else
    localparam [31:0] SIGNATURE=32'h47524431;
    grid_link dut (.*);
`endif
    always @(posedge clk) begin
        reply_valid <= 0;
        if (command_valid) begin
            accepted <= accepted+1; waiting<=1;
            final_reply <= write_data ^ {command, address, 8'h5a};
            final_payload <= ~payload;
        end
        if (waiting && allow_reply) begin
            reply <= final_reply; read_payload <= final_payload;
            reply_valid<=1; waiting<=0;
        end else if (waiting) begin
            // Незавершённый результат может меняться до reply_valid.
            reply <= reply+1; read_payload <= {read_payload[1022:0], ~read_payload[1023]};
        end
    end
    task automatic tick;
        #5; tck=1; #5; tck=0;
    endtask
    task automatic scan(input logic [1087:0] request, output logic [1087:0] response);
        capture=1; tick(); capture=0; shift=1;
        for (integer b=0; b<1088; b=b+1) begin
            tdi=request[b]; response[b]=tdo; tick();
        end
        shift=0; update=1; repeat(4) tick(); update=0; repeat(4) tick();
    endtask
    logic [1087:0] request, response;
    logic [1023:0] expected;
    logic [31:0] expected_reply;
    initial begin
        repeat(5) tick(); rst=0; tck_rst=0;
        for (integer trial=0; trial<12; trial=trial+1) begin
            for(integer word_index=0; word_index<32; word_index=word_index+1)
                request[word_index*32+:32]=$random;
            request[1087:1056]=32'hd5421234; request[1055:1024]=32'(trial);
            expected=~request[1023:0]; expected_reply=32'(trial)^32'h4212345a;
            allow_reply=0;
            scan(request,response);
            scan(0,response);
            if(response[1087:1056]!==32'h42555359) $fatal(1,"Нет признака ожидания");
            // Основная область работает, пока TCK полностью остановлен.
            allow_reply=1; repeat(20) @(negedge clk); repeat(8) tick();
            scan(0,response);
            if(response!=={SIGNATURE,expected_reply,expected}) $fatal(1,"Искажён пакет %0d",trial);
            if(accepted!=trial+1) $fatal(1,"Повторная обработка UPDATE");
        end
        $display("УСПЕХ: 12 пакетов по 32 слова, удержание, остановка TCK и однократный UPDATE");
        $finish;
    end
    initial begin #10000000; $fatal(1,"Истекло время проверки пакетов"); end
endmodule
`default_nettype wire

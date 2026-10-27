`timescale 1ns/1ps
`default_nettype none
module grid_tb;
    logic clk=0, rst=1, command_valid=0;
    always #4 clk = !clk;
    logic [7:0] command;
    logic [15:0] address;
    logic [31:0] write_data;
    logic [1023:0] payload;
    wire [31:0] reply;
    wire [1023:0] read_payload;
    wire reply_valid, busy, done;
    wire [7:0] fault;
`ifdef GRID_WIDE
    grid8_core dut (.*);
`else
    grid_core dut (.*);
`endif
    integer fh, count=0, parsed, compare_payload, wait_cycles, timeout;
    logic [1087:0] request;
    logic [31:0] expected;
    logic [1023:0] expected_payload;
    string path;
    initial begin
        if (!$value$plusargs("VECTORS=%s", path)) $fatal(1, "Нет проверок");
        fh = $fopen(path, "r");
        if (!fh) $fatal(1, "Не открыт файл проверок");
        repeat (5) @(negedge clk);
        rst=0;
        while (!$feof(fh)) begin
            parsed=$fscanf(fh, "%h %h %h %d %d\n", request, expected, expected_payload, compare_payload, wait_cycles);
            if (parsed != 5) $fatal(1, "Повреждена проверка %0d", count);
            command=request[1079:1072]; address=request[1071:1056];
            write_data=request[1055:1024]; payload=request[1023:0]; command_valid=1;
            @(negedge clk); command_valid=0;
            timeout=0;
            while (!reply_valid && timeout < 200) begin @(negedge clk); timeout=timeout+1; end
            if (!reply_valid || reply !== expected || (compare_payload != 0 && read_payload !== expected_payload))
                $fatal(1, "Проверка %0d: действие %0d адрес %h ответ %h/%h; данные %h/%h", count, command, address, reply, expected, read_payload, expected_payload);
            repeat (wait_cycles) @(negedge clk);
            @(negedge clk);
            count=count+1;
        end
        if (count < 1000) $fatal(1, "Мало проверок");
        $display("УСПЕХ: %0d проверок ядра 64×64, полные поля и отказ эпохи", count);
        $finish;
    end
`ifndef GRID_NETLIST
`ifdef GRID_WIDE
`define GRID_DUT dut.engine
    localparam integer LANES=8;
`else
`define GRID_DUT dut
    localparam integer LANES=4;
`endif
    integer returned_groups=0;
    always @(negedge clk) begin
      if (`GRID_DUT.pipeline_reset) returned_groups=0;
      if (!rst && busy) begin
        if (`GRID_DUT.result_valid != 0 && !(&`GRID_DUT.result_valid)) $fatal(1, "Тракты разошлись");
        for (integer i=1; i<LANES; i=i+1)
            if (&`GRID_DUT.result_valid && `GRID_DUT.result_index[0] !== `GRID_DUT.result_index[i])
                $fatal(1, "Адреса трактов разошлись");
        if (&`GRID_DUT.result_valid) begin
            if (`GRID_DUT.result_index[0] !== 10'(returned_groups)) $fatal(1, "Пропущена или повторена группа точек");
            returned_groups=returned_groups+1;
        end
      end
    end
`undef GRID_DUT
`endif
    initial begin #100000000; $fatal(1, "Истекло время проверки сетки"); end
endmodule
`default_nettype wire

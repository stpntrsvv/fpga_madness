`timescale 1ns/1ps
`default_nettype none
module top_tb;
    logic clk = 0;
    always #5 clk = !clk;
    logic rst = 1;
    logic selected = 1, capture = 0, shift = 0, update = 0, tdi = 0;
    wire tdo, busy, done;
    wire [7:0] fault;
    logic tck = 0, tck_rst = 1;
    dpu_jtag dut (.tck(tck), .tck_rst(tck_rst), .clk(clk), .rst(rst), .selected(selected), .capture(capture),
        .shift(shift), .update(update), .tdi(tdi), .tdo(tdo),
        .busy(busy), .done(done), .fault(fault));

    task automatic tick;
        begin #4; tck = 1; #4; tck = 0; end
    endtask

    task automatic scan(input logic [63:0] request, output logic [63:0] response);
        begin
            capture = 1; tick();
            capture = 0; shift = 1;
            for (integer bit_index = 0; bit_index < 64; bit_index = bit_index + 1) begin
                tdi = request[bit_index];
                response[bit_index] = tdo;
                tick();
            end
            shift = 0; update = 1;
            // UPDATE может удерживаться несколько тактов: запрос однократный.
            repeat (3) tick();
            update = 0;
            repeat (3) tick();
        end
    endtask

    string vectors_path;
    integer file_handle, parsed, clocks, checks = 0;
    logic [63:0] request, response;
    logic [31:0] expected;
    initial begin
        if (!$value$plusargs("VECTORS=%s", vectors_path))
            $fatal(1, "Не указан файл проверок +VECTORS");
        file_handle = $fopen(vectors_path, "r");
        if (!file_handle) $fatal(1, "Не удалось открыть файл проверок");
        repeat (4) tick();
        repeat (4) @(negedge clk);
        rst = 0; tck_rst = 0;
        while (!$feof(file_handle)) begin
            parsed = $fscanf(file_handle, "%h %h %d\n", request, expected, clocks);
            if (parsed != 3) $fatal(1, "Повреждена строка %0d", checks + 1);
            scan(request, response);
            repeat (clocks) @(negedge clk);
            repeat (4) tick();
            scan(0, response);
            for (integer attempt = 0; response[63:32] == 32'h42555359 && attempt < 100; attempt = attempt + 1) begin
                repeat (4) tick(); scan(0, response);
            end
            if (response !== {32'h44505532, expected})
                $fatal(1, "Обмен %0d, запрос %h: получено %h, ожидалось %h",
                       checks, request, response, {32'h44505532, expected});
            checks = checks + 1;
        end
        $fclose(file_handle);
        if (checks < 100) $fatal(1, "Недостаточно проверок");
        $display("УСПЕХ: %0d проверок обмена; разные модели в одном автономном ядре", checks);
        $finish;
    end
    initial begin
        #100000000;
        $fatal(1, "Превышено время моделирования");
    end
endmodule
`default_nettype wire

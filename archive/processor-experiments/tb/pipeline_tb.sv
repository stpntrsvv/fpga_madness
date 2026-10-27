`timescale 1ns/1ps
`default_nettype none
module pipeline_tb;
    logic clk = 0;
    always #5 clk = !clk;
    logic rst = 1, valid = 0;
    logic signed [31:0] a = 0, b = 0;
    wire signed [63:0] product;
    wire ready;
    logic signed [63:0] reference_pipe [0:3];
    logic [3:0] reference_valid = 0;
    integer products = 0, seed = 54321;
    dpu_product_pipe multiply (.clk(clk), .rst(rst), .valid(valid),
        .a(a), .b(b), .product(product), .ready(ready));
    always @(posedge clk) begin
        if (rst) reference_valid <= 0;
        else begin
            reference_valid <= {reference_valid[2:0], valid};
            reference_pipe[0] <= a * b;
            for (integer i = 1; i < 4; i = i + 1) reference_pipe[i] <= reference_pipe[i-1];
        end
    end
    always @(negedge clk) if (!rst) begin
        if (ready !== reference_valid[3]) $fatal(1, "Нарушена задержка умножителя");
        if (ready) begin
            if (product !== reference_pipe[3])
                $fatal(1, "Ошибка конвейера: %h вместо %h", product, reference_pipe[3]);
            products = products + 1;
        end
    end

    logic tck = 0, tck_rst = 1;
    logic selected = 1, capture = 0, shift = 0, update = 0, tdi = 0;
    wire tdo, command_valid;
    wire [7:0] command;
    wire [15:0] address;
    wire [31:0] write_data;
    logic [31:0] reply = 0;
    logic reply_valid = 0;
    integer commands = 0;
    integer tck_half_period = 7;
    dpu_link link (.tck(tck), .tck_rst(tck_rst), .clk(clk), .rst(rst),
        .selected(selected), .capture(capture), .shift(shift), .update(update),
        .tdi(tdi), .tdo(tdo), .command_valid(command_valid), .command(command),
        .address(address), .write_data(write_data), .reply(reply), .reply_valid(reply_valid));
    always @(posedge clk) reply_valid <= !rst && command_valid;
    always @(posedge clk) if (!rst && command_valid) begin
        reply <= write_data ^ {command, 8'b0, address};
        commands = commands + 1;
    end
    task automatic tick;
        begin #(tck_half_period); tck = 1; #(tck_half_period); tck = 0; end
    endtask
    task automatic scan(input logic [63:0] request, output logic [63:0] response);
        begin
            capture = 1; tick(); capture = 0; shift = 1;
            for (integer i = 0; i < 64; i = i + 1) begin
                tdi = request[i]; response[i] = tdo; tick();
            end
            shift = 0; update = 1;
            repeat (3) tick();
            update = 0;
        end
    endtask
    logic [31:0] values [0:7];
    logic [63:0] response, request;
    logic [31:0] expected;
    integer before_commands;
    initial begin
        values[0] = 0; values[1] = 1; values[2] = -1;
        values[3] = 'h7fffffff; values[4] = 'h80000000;
        values[5] = 'h00010000; values[6] = 'hffff0000; values[7] = 'h12345678;
        repeat (4) @(negedge clk);
        rst = 0;
        for (integer x = 0; x < 8; x = x + 1)
            for (integer y = 0; y < 8; y = y + 1) begin
                @(negedge clk); a = values[x]; b = values[y]; valid = 1;
            end
        repeat (512) begin
            @(negedge clk); a = $random(seed); b = $random(seed); valid = ($random(seed) & 3) != 0;
        end
        @(negedge clk); valid = 0;
        repeat (8) @(negedge clk);

        // Сброс среди незавершённых произведений отменяет старую допустимость.
        @(negedge clk); valid = 1; a = 'h7fffffff; b = 'h80000000;
        repeat (2) @(negedge clk);
        rst = 1;
        repeat (2) @(negedge clk);
        rst = 0; valid = 0;
        for (integer n = 0; n < 8; n = n + 1) begin
            @(negedge clk); valid = 1; a = n * 12345; b = -n * 6789;
        end
        @(negedge clk); valid = 0;
        repeat (8) @(negedge clk);

        repeat (4) tick(); tck_rst = 0;
        for (integer n = 0; n < 48; n = n + 1) begin
            // Быстрее и медленнее основной области, с независимыми фазами.
            case (n % 3) 0: tck_half_period = 1; 1: tck_half_period = 7; 2: tck_half_period = 19; endcase
            request = {8'hd5, 8'(n+1), 16'(n*17), 32'($random(seed))};
            expected = request[31:0] ^ {request[55:48], 8'b0, request[47:32]};
            before_commands = commands;
            scan(request, response);
            // Никаких фронтов TCK: запрос должен дойти и исполниться автономно.
            #300;
            if (commands != before_commands + 1)
                $fatal(1, "Запрос %0d потерян либо повторён при остановке TCK: до %0d после %0d, ответ %h", n, before_commands, commands, response);
            repeat (4) tick();
            scan(0, response);
            if (response !== {32'h44505532, expected})
                $fatal(1, "Неверный ответ перехода областей: %h, ожидалось %h", response, expected);
        end
        // Одновременный сброс областей и новый обмен не повторяют старую команду.
        rst = 1; tck_rst = 1; repeat (4) tick();
        @(negedge clk); rst = 0; tck_rst = 0;
        before_commands = commands;
        scan(64'hd5030123456789ab, response); #300; repeat (4) tick(); scan(0, response);
        if (commands != before_commands + 1 || response !== {32'h44505532, 32'h46678888})
            $fatal(1, "Ошибка обмена после сброса: %h", response);
        if (products < 300) $fatal(1, "Недостаточно произведений");
        $display("УСПЕХ: %0d произведений с переменными входами и пропусками; 49 переходов областей без TCK", products);
        $finish;
    end
    initial begin #1000000; $fatal(1, "Превышено время проверки конвейера"); end
endmodule
`default_nettype wire

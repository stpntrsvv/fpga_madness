`timescale 1ns/1ps
`default_nettype none
// Проверка полного перехода без задержек последовательного транспорта.
module integrator_tb;
    logic clk = 0, rst = 1, command_valid = 0;
    always #5 clk = !clk;
    logic [7:0] command = 0;
    logic [15:0] address = 0;
    logic [31:0] write_data = 0;
    wire [31:0] reply;
    wire reply_valid, busy, done;
    wire [7:0] fault;
    dpu_core dut (.clk(clk), .rst(rst), .command_valid(command_valid), .command(command),
        .address(address), .write_data(write_data), .reply(reply), .reply_valid(reply_valid),
        .busy(busy), .done(done), .fault(fault));

    task automatic transact(input logic [7:0] op, input logic [15:0] addr,
                            input logic [31:0] data, expected);
        begin
            @(negedge clk); command_valid = 1; command = op; address = addr; write_data = data;
            @(negedge clk); command_valid = 0;
            for (integer n = 0; !reply_valid && n < 20; n = n + 1) @(negedge clk);
            if (!reply_valid || reply !== expected)
                $fatal(1, "Управление интегратором: %h/%h, ответ %h вместо %h", op, addr, reply, expected);
        end
    endtask

    logic signed [31:0] derivative [0:3];
    logic signed [63:0] products [0:3], next_values [0:3];
    integer cases = 0;
    task automatic run_case(input logic [31:0] dt, input integer expected_cycles);
        logic expected_fault;
        begin
            expected_fault = 0;
            for (integer s = 0; s < 4; s = s + 1) begin
                products[s] = $signed(dt) * $signed(derivative[s]);
                next_values[s] = products[s] >>> 16;
                if (next_values[s] > 64'sd2147483647 || next_values[s] < -64'sd2147483648)
                    expected_fault = 1;
            end
            transact(1, 0, 0, 0);
            transact(2, 'h10, 4, 0);
            transact(2, 'h11, 0, 0);
            transact(2, 'h12, dt, 0);
            for (integer s = 0; s < 4; s = s + 1) begin
                transact(2, 16'h200 + 16'(s), derivative[s], 0);
                transact(2, 16'h400 + 16'(s), 32'(4+s), 0);
            end
            transact(4, 0, 1, 0);
            for (integer n = 0; busy && n < 20; n = n + 1) @(negedge clk);
            if (busy || fault !== (expected_fault ? 8'd3 : 8'd0) || done !== !expected_fault)
                $fatal(1, "Статус перехода dt=%h: busy=%b fault=%h done=%b", dt, busy, fault, done);
            transact(3, 2, 0, expected_fault ? 0 : 1);
            transact(3, 3, 0, 32'(expected_cycles));
            transact(3, 4, 0, expected_fault ? 32'h1000 : 32'b0);
            for (integer s = 0; s < 4; s = s + 1) begin
                transact(3, 16'h100 + 16'(s), 0, expected_fault ? 32'b0 : next_values[s][31:0]);
                transact(3, 16'h620 + 16'(s), 0, products[s][31:0]);
                transact(3, 16'h630 + 16'(s), 0, products[s][63:32]);
            end
            cases = cases + 1;
        end
    endtask

    initial begin
        derivative[0] = 'h80000000; derivative[1] = -1;
        derivative[2] = 1; derivative[3] = 'h7fffffff;
        repeat (4) @(negedge clk); rst = 0;
        for (integer p = 0; p < 31; p = p + 1) begin
            run_case(32'd1 << p, 4);
            if (p > 0) run_case((32'd1 << p) + 1, 7);
        end
        // Левый сдвиг без переполнения: оба знака принимаются одновременно.
        derivative[0] = -100; derivative[1] = -1; derivative[2] = 1; derivative[3] = 100;
        run_case(32'h40000000, 4);
        // Меняем только dt без CLEAR: общий путь и возврат к сдвигу.
        transact(2, 'h12, 3, 0);
        transact(4, 0, 1, 0);
        for (integer n = 0; busy && n < 20; n = n + 1) @(negedge clk);
        transact(3, 1, 0, 2); transact(3, 3, 0, 7);
        transact(3, 'h100, 0, 32'(-1638401)); transact(3, 'h101, 0, 32'(-16385));
        transact(3, 'h102, 0, 16384); transact(3, 'h103, 0, 1638400);
        transact(2, 'h12, 65536, 0);
        transact(4, 0, 1, 0);
        for (integer n = 0; busy && n < 20; n = n + 1) @(negedge clk);
        transact(3, 1, 0, 2); transact(3, 3, 0, 4);
        transact(3, 'h100, 0, 32'(-1638501)); transact(3, 'h101, 0, 32'(-16386));
        transact(3, 'h102, 0, 16385); transact(3, 'h103, 0, 1638500);
        cases = cases + 2;
        transact(2, 'h12, 0, 0);
        transact(4, 0, 1, 'hbad00003);
        transact(3, 1, 0, 'h204);
        transact(2, 'h12, 'h80000000, 0);
        transact(4, 0, 1, 'hbad00003);
        transact(3, 1, 0, 'h204);
        transact(2, 'h12, 'hffffffff, 0);
        transact(4, 0, 1, 'hbad00003);
        transact(3, 1, 0, 'h204);
        $display("УСПЕХ: %0d перехода интегратора и 3 отказа dt; все 31 степени, общий путь, знаки и отмена переполнения", cases);
        $finish;
    end
    initial begin #1000000; $fatal(1, "Превышено время проверки интегратора"); end
endmodule
`default_nettype wire

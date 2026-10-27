`default_nettype none
// Один удерживаемый запрос и один ответ между независимыми областями.
module dpu_link (
    input wire tck, tck_rst, clk, rst,
    input wire selected, capture, shift, update, tdi,
    output wire tdo,
    output logic command_valid,
    output wire [7:0] command,
    output wire [15:0] address,
    output wire [31:0] write_data,
    input wire [31:0] reply,
    input wire reply_valid
);
    logic [63:0] scan_register, request_hold;
    // Основная область стартует даже при полном отсутствии TCK.
    // Начальный запрос обязан быть определён уже глобальной инициализацией FPGA.
    logic request_toggle = 0;
    logic update_previous, pending;
    logic [31:0] response_hold, response_tck;
    logic acknowledge = 0;
    (* ASYNC_REG = "TRUE" *) logic request_meta, request_sync;
    (* ASYNC_REG = "TRUE" *) logic ack_meta, ack_sync;
    logic [1:0] receive_phase;
    logic [55:0] request_core;
    assign tdo = scan_register[0];
    assign command = request_core[55:48];
    assign address = request_core[47:32];
    assign write_data = request_core[31:0];

    always_ff @(posedge tck) begin
        if (tck_rst) begin
            scan_register <= 0;
            request_hold <= 0;
            request_toggle <= 0;
            pending <= 0;
            update_previous <= 0;
            response_tck <= 0;
            ack_meta <= 0;
            ack_sync <= 0;
        end else begin
            ack_meta <= acknowledge;
            ack_sync <= ack_meta;
            update_previous <= update;
            if (pending && ack_sync == request_toggle) begin
                response_tck <= response_hold;
                pending <= 0;
            end
            if (selected && capture)
                scan_register <= pending ? {32'h42555359, 32'b0} : {32'h44505532, response_tck};
            else if (selected && shift) scan_register <= {tdi, scan_register[63:1]};
            if (selected && update && !update_previous && !pending && scan_register[63:56] == 8'hd5) begin
                request_hold <= scan_register;
                request_toggle <= !request_toggle;
                pending <= 1;
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            request_meta <= 0;
            request_sync <= 0;
            request_core <= 0;
            acknowledge <= 0;
            response_hold <= 0;
            command_valid <= 0;
            receive_phase <= 0;
        end else begin
            request_meta <= request_toggle;
            request_sync <= request_meta;
            command_valid <= 0;
            case (receive_phase)
                0: if (request_sync != acknowledge) begin
                    request_core <= request_hold[55:0];
                    command_valid <= 1;
                    receive_phase <= 1;
                end
                // Обработка управления может занимать несколько тактов.
                1: if (reply_valid) begin
                    response_hold <= reply;
                    acknowledge <= request_sync;
                    receive_phase <= 0;
                end
                default: receive_phase <= 0;
            endcase
        end
    end
endmodule
`default_nettype wire

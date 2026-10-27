`default_nettype none
// Удерживаемый пакет: 32 слова данных, слово управления и заголовок.
// Один запрос в полёте; полезная нагрузка неподвижна до подтверждения.
module grid_link #(parameter [31:0] SIGNATURE=32'h47524431) (
    input wire tck, tck_rst, clk, rst,
    input wire selected, capture, shift, update, tdi,
    output wire tdo,
    output logic command_valid,
    output wire [7:0] command,
    output wire [15:0] address,
    output wire [31:0] write_data,
    output wire [1023:0] payload,
    input wire [31:0] reply,
    input wire [1023:0] read_payload,
    input wire reply_valid
);
    logic [1087:0] scan_register, request_hold;
    logic request_toggle = 0, acknowledge = 0;
    logic previous_update, pending;
    logic [1055:0] response_hold, response_tck;
    (* ASYNC_REG = "TRUE" *) logic request_meta, request_sync, ack_meta, ack_sync;
    logic waiting;
    logic [1079:0] request_core;
    assign tdo = scan_register[0];
    assign command = request_core[1079:1072];
    assign address = request_core[1071:1056];
    assign write_data = request_core[1055:1024];
    assign payload = request_core[1023:0];
    always_ff @(posedge tck) begin
        if (tck_rst) begin
            scan_register <= 0; request_hold <= 0; request_toggle <= 0;
            pending <= 0; previous_update <= 0; response_tck <= 0; ack_meta <= 0; ack_sync <= 0;
        end else begin
            ack_meta <= acknowledge; ack_sync <= ack_meta;
            previous_update <= update;
            if (pending && ack_sync == request_toggle) begin response_tck <= response_hold; pending <= 0; end
            if (selected && capture)
                scan_register <= pending ? {32'h42555359, 1056'b0} : {SIGNATURE, response_tck};
            else if (selected && shift) scan_register <= {tdi, scan_register[1087:1]};
            if (selected && update && !previous_update && !pending && scan_register[1087:1080] == 8'hd5) begin
                request_hold <= scan_register; request_toggle <= !request_toggle; pending <= 1;
            end
        end
    end
    always_ff @(posedge clk) begin
        // Поля источников удерживаются протоколом до следующего запроса.
        // Постоянная регистрация исключает общий CE более тысячи разрядов.
        request_core <= request_hold[1079:0];
        response_hold <= {reply, read_payload};
        if (rst) begin
            request_meta <= 0; request_sync <= 0; acknowledge <= 0;
            command_valid <= 0; waiting <= 0;
        end else begin
            request_meta <= request_toggle; request_sync <= request_meta;
            command_valid <= 0;
            if (!waiting && request_sync != acknowledge) begin
                command_valid <= 1; waiting <= 1;
            end else if (waiting && reply_valid) begin
                acknowledge <= request_sync; waiting <= 0;
            end
        end
    end
endmodule
`default_nettype wire

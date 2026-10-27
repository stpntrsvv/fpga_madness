`default_nettype none
// Опыт 64×64: два снимка T, четыре карты, четыре или восемь банков на массив.
module grid_core #(parameter bit PROGRAMMABLE=0, parameter integer LANES=4) (
    input wire clk, rst, command_valid,
    input wire [7:0] command,
    input wire [15:0] address,
    input wire [31:0] write_data,
    input wire [1023:0] payload,
    output logic [31:0] reply,
    output logic [1023:0] read_payload,
    output logic reply_valid, busy, done,
    output logic [7:0] fault
);
    localparam integer BANK_BITS = $clog2(LANES);
    localparam integer MEMORY_BITS = 12-BANK_BITS;
    localparam integer COLUMN_BITS = 6-BANK_BITS;
    // Фиксированный профиль поддерживает две проверяемые ширины; GRD2 — только четыре.
    initial begin
        if ((LANES != 4 && LANES != 8) || (PROGRAMMABLE && LANES != 4))
            $error("Недопустимое число трактов");
    end
    localparam [2:0] HOST_IDLE=0, HOST_WRITE=1, HOST_READ=2, HOST_CAPTURE=3,
                     HOST_WRITE_A=4, HOST_WRITE_B=5, HOST_READ_A=6, HOST_READ_B=7;
    logic [2:0] host_phase;
    logic [14:0] host_address;
    logic [5:0] host_left, host_offset;
    logic [31:0] host_word, word_banks [0:7], read_words [0:31];
    logic [31:0] read_banks [0:5], host_read_word;
    logic [2:0] word_bank;
    logic [5:0] response_length;
    logic [12:0] loaded [0:4];
    logic role;
    logic signed [31:0] dt;
    // Проверяется при записи, вне пути принятия RUN.
    logic dt_positive;
    logic [31:0] field_config [0:12];
    logic [31:0] field_constant [0:7];
    logic [31:0] edge_source, local_source;
    wire [415:0] field_config_bus;
    wire [255:0] field_constant_bus;
    logic [831:0] field_literals;
    logic field_configuration_valid;
    for (genvar c=0;c<13;c=c+1) assign field_config_bus[c*32+:32]=field_config[c];
    for (genvar c=0;c<8;c=c+1) assign field_constant_bus[c*32+:32]=field_constant[c];
    // Общий выбор постоянных операндов до RUN. Во время RUN записи запрещены.
    for (genvar c=0;c<13;c=c+1) begin : literal_selection
        always_ff @(posedge clk) begin
            field_literals[c*64+:32]<=field_constant[3'(field_config[c][6:3]-4'd7)];
            field_literals[c*64+32+:32]<=field_constant[3'(field_config[c][10:7]-4'd7)];
        end
    end
    function automatic configuration_word_valid(input logic [31:0] word_value,
                                                input logic multiply, input integer tracks);
        logic [2:0] op;
        logic [3:0] a,b;
        begin
            op=word_value[2:0];a=word_value[6:3];b=word_value[10:7];
            configuration_word_valid=word_value==0 || (word_value[31:14]==0 &&
                (multiply ? op==4 : (op>=1 && op<=3)) &&
                word_value[13:11] != 0 && int'(word_value[13:11]) < tracks &&
                !(int'(a)>=tracks && a<7) &&
                (op==1 || !(int'(b)>=tracks && b<7)));
        end
    endfunction
    always_comb begin
        field_configuration_valid=edge_source[31:4]==0 && local_source[31:4]==0 &&
            !(edge_source[3:0]>=3 && edge_source[3:0]<7) &&
            !(local_source[3:0]>=4 && local_source[3:0]<7);
        for (integer c=0;c<13;c=c+1)
            if (!configuration_word_valid(field_config[c],c==1 || (c>=6 && c<11),c<2 ? 3 : (c<6 ? 7 : 4)))
                field_configuration_valid=0;
    end
    logic [31:0] target_steps, completed_steps, elapsed_cycles, epoch_cycles, last_epoch_cycles;
    logic [63:0] clock_ticks;
    logic [31:0] clock_high;
    logic epoch_start, issuing;
    logic [9:0] issue_index;
    // Проверка длины, диапазона и полноты загрузки отделена от ответа управления.
    logic input_valid, control_valid, control_clear;
    logic [7:0] request_command, control_command;
    logic [15:0] request_address, control_address;
    logic [31:0] request_data, control_data;
    logic [1023:0] request_payload, control_payload;
    logic [13:0] request_end;
    logic [12:0] request_loaded;
    logic request_packet_valid, control_packet_valid, control_write_valid, control_read_valid;
    always_ff @(posedge clk) begin
        input_valid <= !rst && command_valid;
        control_valid <= !rst && input_valid;
        control_clear <= !rst && input_valid && request_command == 1;
        request_command <= command; control_command <= request_command;
        request_address <= address; control_address <= request_address;
        request_data <= write_data; control_data <= request_data;
        request_payload <= payload; control_payload <= request_payload;
        request_end <= {2'b0, address[11:0]} + {1'b0, write_data[12:0]};
        request_loaded <= address[15:12] < 5 ? loaded[address[14:12]] : 13'd0;
        request_packet_valid <= address[15:12] < 5 && write_data > 0 && write_data <= 32;
        control_packet_valid <= request_packet_valid && request_end <= 4096;
        control_write_valid <= {1'b0, request_address[11:0]} <= request_loaded;
        control_read_valid <= request_end <= {1'b0, request_loaded};
    end
    wire soft_clear = control_clear;
    wire pipeline_reset = rst || soft_clear || epoch_start;
    wire issue = busy && issuing && !epoch_start;
    wire [31:0] memory_a [0:5][0:LANES-1], memory_b [0:5][0:LANES-1];
    wire [LANES-1:0] result_valid, result_fault, flux_valid;
    wire [9:0] result_index [0:LANES-1], flux_index [0:LANES-1];
    wire [31:0] result [0:LANES-1];
    wire signed [31:0] east_flux [0:LANES-1];
    logic signed [31:0] previous_east;
    logic epoch_fault;
    logic [2:0] host_plane, physical_host_plane;
    wire [9:0] host_row_address = 10'(host_address[11:BANK_BITS]);
    // Выбор слова пакета разбит на две короткие стадии. Нет общей загрузки
    // 1024-разрядного сдвигового регистра от сложного декодирования команды.
    always_ff @(posedge clk) begin
        for (integer i=0; i<8; i=i+1)
            word_banks[i] <= control_payload[i*128 + int'(host_offset[1:0])*32 +: 32];
        word_bank <= host_offset[4:2];
        host_word <= word_banks[word_bank];
        for (integer p=0; p<6; p=p+1)
            read_banks[p] <= memory_a[p][host_address[BANK_BITS-1:0]];
        host_read_word <= read_banks[physical_host_plane];
    end
    for (genvar word_index=0; word_index<32; word_index=word_index+1) begin : response_words
        logic included, selected_word, capture_word;
        always_ff @(posedge clk) included <= response_length > word_index;
        always_ff @(posedge clk) begin
            selected_word <= host_offset == 6'(word_index);
            capture_word <= host_phase == HOST_READ_B && selected_word;
        end
        always_ff @(posedge clk)
            if (capture_word)
                read_words[word_index] <= host_read_word;
        assign read_payload[word_index*32+:32] = included ? read_words[word_index] : 32'b0;
    end
    for (genvar p = 0; p < 6; p = p + 1) begin : planes
        for (genvar b = 0; b < LANES; b = b + 1) begin : banks
            wire writing_host = host_phase == HOST_WRITE && physical_host_plane == 3'(p) && host_address[BANK_BITS-1:0] == BANK_BITS'(b);
            wire writing_trial = busy && result_valid[b] && p < 2 && role != 1'(p);
            wire [9:0] a = writing_trial ? result_index[b] :
                            (busy ? issue_index : host_row_address);
            wire [31:0] value = writing_trial ? result[b] : host_word;
            wire [9:0] south_address = issue_index + 10'(64/LANES);
            grid_memory #(.ADDRESS_BITS(MEMORY_BITS)) memory (.clk(clk), .write_enable(writing_host || writing_trial),
                .address_a(a[MEMORY_BITS-1:0]), .address_b(south_address[MEMORY_BITS-1:0]), .write_data(value),
                .read_a(memory_a[p][b]), .read_b(memory_b[p][b]));
        end
    end

    logic request_valid, previous_valid, window_valid;
    logic [9:0] request_index, previous_index, window_index;
    logic [31:0] previous_t [0:LANES-1], previous_s [0:LANES-1];
    logic [31:0] previous_ge [0:LANES-1], previous_gs [0:LANES-1], previous_ci [0:LANES-1], previous_q [0:LANES-1];
    logic [31:0] window_t [0:LANES-1], window_e [0:LANES-1], window_s [0:LANES-1];
    logic [31:0] window_ge [0:LANES-1], window_gs [0:LANES-1], window_ci [0:LANES-1], window_q [0:LANES-1];
    always_ff @(posedge clk) begin
        // Данные идут постоянно; сбрасывается только их допустимость.
        // Общее разрешение записи сотен разрядов создавало длинный путь управления.
        request_index <= issue_index;
        window_index <= previous_index;
        previous_index <= request_index;
        for (integer i = 0; i < LANES; i = i + 1) begin
            window_t[i] <= previous_t[i]; window_s[i] <= previous_s[i];
            window_e[i] <= i < LANES-1 ? previous_t[(i+1)%LANES] : memory_a[{2'b0, role}][0];
            window_ge[i] <= previous_ge[i]; window_gs[i] <= previous_gs[i];
            window_ci[i] <= previous_ci[i]; window_q[i] <= previous_q[i];
            previous_t[i] <= memory_a[{2'b0, role}][i]; previous_s[i] <= memory_b[{2'b0, role}][i];
            previous_ge[i] <= memory_a[2][i]; previous_gs[i] <= memory_a[3][i];
            previous_ci[i] <= memory_a[4][i]; previous_q[i] <= memory_a[5][i];
        end
        if (pipeline_reset) begin
            request_valid <= 0; previous_valid <= 0; window_valid <= 0;
        end else begin
            request_valid <= issue;
            window_valid <= previous_valid;
            previous_valid <= request_valid;
        end
    end
    // Перенос западного потока через границу соседних групп точек.
    always_ff @(posedge clk) begin
        if (pipeline_reset) previous_east <= 0;
        else if (flux_valid[LANES-1]) previous_east <= east_flux[LANES-1];
    end
    for (genvar lane = 0; lane < LANES; lane = lane + 1) begin : lanes
        wire signed [31:0] west;
        if (lane == 0) assign west = flux_index[0][COLUMN_BITS-1:0] == 0 ? 32'sd0 : previous_east;
        else assign west = east_flux[lane-1];
        if (PROGRAMMABLE) begin : programmed
        field_lane #(.LANE(lane)) arithmetic (
            .clk(clk), .rst(pipeline_reset), .valid(window_valid), .index(window_index),
            .center(window_t[lane]), .east(window_e[lane]), .south(window_s[lane]),
            .ge(window_ge[lane]), .gs(window_gs[lane]), .ci(window_ci[lane]),
            .source(window_q[lane]), .dt(dt), .west_flux(west),
            .configurations(field_config_bus),.constants(field_constant_bus),
            .literals(field_literals),
            .edge_output(edge_source[3:0]),.local_output(local_source[3:0]),
            .east_flux(east_flux[lane]), .flux_valid(flux_valid[lane]), .flux_index(flux_index[lane]),
            .result_valid(result_valid[lane]), .result_fault(result_fault[lane]),
            .result_index(result_index[lane]), .result(result[lane]));
        end else begin : fixed_heat
        grid_heat_lane #(.LANE(lane), .LANES(LANES)) arithmetic (
            .clk(clk), .rst(pipeline_reset), .valid(window_valid), .index(window_index),
            .center(window_t[lane]), .east(window_e[lane]), .south(window_s[lane]),
            .ge(window_ge[lane]), .gs(window_gs[lane]), .ci(window_ci[lane]),
            .source(window_q[lane]), .dt(dt), .west_flux(west),
            .east_flux(east_flux[lane]), .flux_valid(flux_valid[lane]), .flux_index(flux_index[lane]),
            .result_valid(result_valid[lane]), .result_fault(result_fault[lane]),
            .result_index(result_index[lane]), .result(result[lane]));
        end
    end
    wire all_loaded = loaded[0] == 4096 && loaded[1] == 4096 && loaded[2] == 4096 &&
                      loaded[3] == 4096 && loaded[4] == 4096;
    always_ff @(posedge clk) begin
        if (rst) clock_ticks <= 0;
        else clock_ticks <= clock_ticks + 1;
        if (rst || soft_clear) begin
            host_phase <= HOST_IDLE; host_address <= 0; host_left <= 0; host_offset <= 0;
            host_plane <= 0; physical_host_plane <= 0;
            response_length <= 0; reply <= 0; reply_valid <= !rst;
            role <= 0; dt <= 0; dt_positive <= 0; target_steps <= 0; completed_steps <= 0; elapsed_cycles <= 0;
            edge_source<=32'hffffffff;local_source<=32'hffffffff;
            for (integer c=0;c<13;c=c+1) field_config[c]<=32'hffffffff;
            for (integer c=0;c<8;c=c+1) field_constant[c]<=0;
            epoch_cycles <= 0; last_epoch_cycles <= 0; clock_high <= 0;
            busy <= 0; done <= 0; fault <= 0; epoch_start <= 0; issuing <= 0; issue_index <= 0;
            epoch_fault <= 0;
            for (integer p = 0; p < 5; p = p + 1) loaded[p] <= 0;
        end else begin
            reply_valid <= 0;
            epoch_start <= 0;
            if (busy) begin
                elapsed_cycles <= elapsed_cycles + 1;
                epoch_cycles <= epoch_cycles + 1;
                if (epoch_start) begin issuing <= 1; issue_index <= 0; end
                else if (issuing) begin
                    issue_index <= issue_index + 1;
                    if (issue_index == 10'(4096/LANES-1)) issuing <= 0;
                end
                if (&result_valid) begin
                    if (|result_fault) epoch_fault <= 1;
                    if (result_index[0] == 10'(4096/LANES-1)) begin
                        last_epoch_cycles <= epoch_cycles + 1;
                        if (epoch_fault || (|result_fault)) begin busy <= 0; fault <= 3; end
                        else begin
                            role <= !role;
                            completed_steps <= completed_steps + 1;
                            if (completed_steps + 1 == target_steps) begin busy <= 0; done <= 1; end
                            else begin epoch_start <= 1; epoch_cycles <= 0; epoch_fault <= 0; end
                        end
                    end
                end
            end
            case (host_phase)
                HOST_WRITE: begin
                    if ({1'b0, host_address[11:0]} == loaded[host_plane]) loaded[host_plane] <= loaded[host_plane] + 1;
                    host_address <= host_address + 1;
                    host_offset <= host_offset + 1;
                    host_left <= host_left - 1;
                    if (host_left == 1) begin host_phase <= HOST_IDLE; reply_valid <= 1; reply <= 0; end
                    else host_phase <= HOST_WRITE_A;
                end
                HOST_WRITE_A: host_phase <= HOST_WRITE_B;
                HOST_WRITE_B: host_phase <= HOST_WRITE;
                HOST_READ: host_phase <= HOST_READ_A;
                HOST_READ_A: host_phase <= HOST_READ_B;
                HOST_READ_B: host_phase <= HOST_CAPTURE;
                HOST_CAPTURE: begin
                    host_address <= host_address + 1; host_offset <= host_offset + 1;
                    host_left <= host_left - 1;
                    if (host_left == 1) begin host_phase <= HOST_IDLE; reply_valid <= 1; reply <= 0; end
                    else host_phase <= HOST_READ;
                end
                default: begin end
            endcase
            if (control_valid) begin
                reply_valid <= 1; reply <= 0;
                case (control_command)
                    2: begin
                        if (busy) reply <= 32'hbad00001;
                        else if (control_address == 16'h0010) begin
                            dt <= control_data; dt_positive <= $signed(control_data) > 0;
                            done <= 0; fault <= 0;
                        end
                        else if (PROGRAMMABLE && control_address==16'h0020) begin edge_source<=control_data;done<=0;fault<=0;end
                        else if (PROGRAMMABLE && control_address==16'h0021) begin local_source<=control_data;done<=0;fault<=0;end
                        else if (PROGRAMMABLE && control_address>=16'h0030 && control_address<16'h0038) begin
                            field_constant[control_address[2:0]]<=control_data;done<=0;fault<=0;
                        end
                        else if (PROGRAMMABLE && control_address>=16'h0080 && control_address<16'h008d) begin
                            field_config[control_address[3:0]]<=control_data;done<=0;fault<=0;
                        end
                        else reply <= 32'hbad00002;
                    end
                    3: case (control_address)
                        0: reply <= PROGRAMMABLE ? 32'h47524432 : 32'h47524431;
                        1: reply <= {16'b0, fault, 5'b0, fault != 0, done, busy};
                        2: reply <= completed_steps;
                        3: reply <= elapsed_cycles;
                        4: reply <= last_epoch_cycles;
                        5: begin reply <= clock_ticks[31:0]; clock_high <= clock_ticks[63:32]; end
                        6: reply <= clock_high;
                        16: reply <= dt;
                        17: reply <= 32'd4096;
                        18: reply <= 32'(LANES);
                        default: begin
                            if (PROGRAMMABLE && control_address==16'h0020) reply<=edge_source;
                            else if (PROGRAMMABLE && control_address==16'h0021) reply<=local_source;
                            else if (PROGRAMMABLE && control_address>=16'h0030 && control_address<16'h0038)
                                reply<=field_constant[control_address[2:0]];
                            else if (PROGRAMMABLE && control_address>=16'h0080 && control_address<16'h008d)
                                reply<=field_config[control_address[3:0]];
                            else reply<=32'hbad00002;
                        end
                    endcase
                    4: begin
                        if (busy) reply <= 32'hbad00001;
                        else if (!all_loaded || !dt_positive || (PROGRAMMABLE && !field_configuration_valid)) begin
                            fault <= 2; done <= 0; reply <= 32'hbad00003;
                        end
                        else begin
                            target_steps <= control_data; completed_steps <= 0; elapsed_cycles <= 0;
                            epoch_cycles <= 0; last_epoch_cycles <= 0; epoch_fault <= 0;
                            busy <= control_data != 0; done <= control_data == 0; fault <= 0;
                            epoch_start <= control_data != 0;
                        end
                    end
                    5, 6: begin
                        if (busy) reply <= 32'hbad00001;
                        else if (!control_packet_valid) reply <= 32'hbad00002;
                        else if (control_command == 5 && !control_write_valid) reply <= 32'hbad00003;
                        else if (control_command == 6 && !control_read_valid) reply <= 32'hbad00003;
                        else begin
                            host_address <= control_address[14:0]; host_left <= control_data[5:0]; host_offset <= 0;
                            host_plane <= control_address[14:12];
                            physical_host_plane <= control_address[14:12] == 0 ? {2'b0, role} : control_address[14:12] + 3'd1;
                            response_length <= control_command == 6 ? control_data[5:0] : 6'd0;
                            reply_valid <= 0;
                            host_phase <= control_command == 5 ? HOST_WRITE_A : HOST_READ;
                            if (control_command == 5) begin done <= 0; fault <= 0; end
                        end
                    end
                    default: reply <= 32'hbad00004;
                endcase
            end
        end
    end
endmodule
`default_nettype wire

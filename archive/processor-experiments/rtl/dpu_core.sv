`default_nettype none
module dpu_core (
    input wire clk,
    input wire rst,
    input wire command_valid,
    input wire [7:0] command,
    input wire [15:0] address,
    input wire [31:0] write_data,
    output logic [31:0] reply,
    output logic reply_valid,
    output logic busy,
    output logic done,
    output logic [7:0] fault
);
    // Пространственный профиль: 4 состояния, 8 параметров, 9 узлов, Q16.16.
    localparam integer NODE_CAPACITY = 9;
    localparam integer SOURCE_COUNT = 12 + NODE_CAPACITY;
    localparam integer SOURCE_BITS = 32 * SOURCE_COUNT;
    localparam [2:0] IDLE = 0, EVALUATE = 1, COMMIT = 2, INTEGRATE = 3, WAIT_PRODUCT = 4,
                     VALIDATE_WAIT = 5, VALIDATE = 6, FAST_TRANSITION = 7;
    logic [2:0] phase;
    logic signed [31:0] state_value [0:3];
    logic signed [31:0] parameter_value [0:7];
    logic [31:0] node_config [0:NODE_CAPACITY-1];
    logic [31:0] derivative_source [0:3];
    logic signed [31:0] node_value [0:NODE_CAPACITY-1];
    logic [NODE_CAPACITY-1:0] node_ready;
    logic [NODE_CAPACITY-1:0] node_started;
    logic [NODE_CAPACITY-1:0] fast_pending, node_fault;
    logic [NODE_CAPACITY-1:0] node_active, node_configuration_valid;
    logic [3:0] state_active, derivative_configuration_valid;
    logic configuration_checked;
    logic signed [31:0] operand_a [0:NODE_CAPACITY-1], operand_b [0:NODE_CAPACITY-1];
    logic signed [31:0] candidate_state [0:3];
    logic [3:0] candidate_fault;
    logic [63:0] clock_ticks;
    logic [31:0] clock_high;
    logic control_valid, control_clear;
    logic [7:0] control_command;
    logic [31:0] control_data;
    logic [27:0] write_target;
    logic [NODE_CAPACITY-1:0] decoded_node_active;
    logic [3:0] decoded_state_active;
    logic [3:0] control_read_bank;
    logic control_clock_read;
    logic [31:0] sampled_clock_high;
    logic [31:0] read_words [0:10];
    logic [31:0] control_read;
    logic [3:0] selected_read_bank;
    logic [31:0] state_count, node_count;
    logic signed [31:0] dt;
    logic dt_is_shift, decoded_dt_is_shift;
    logic [4:0] dt_power, decoded_dt_power;
    logic [31:0] target_steps, completed_steps, elapsed_cycles;
    wire epoch_fault = |node_fault;
    logic configuration_valid, all_ready;
    wire signed [63:0] node_wide [0:NODE_CAPACITY-1];
    wire operands_ready [0:NODE_CAPACITY-1];
    wire node_result_valid [0:NODE_CAPACITY-1];
    wire [3:0] transition_ready;
    wire clear_pipeline = rst || control_clear;
    wire signed [63:0] next_wide [0:3];
    wire signed [63:0] transition_product [0:3];
    wire signed [31:0] derivative_value [0:3];
    wire [SOURCE_BITS-1:0] value_bus;

    function automatic source_ready(input logic [5:0] source, input logic [NODE_CAPACITY-1:0] ready);
        begin
            source_ready = 1'b0;
            if (source < 12) source_ready = 1'b1;
            else if (int'(source) < SOURCE_COUNT) source_ready = ready[4'(source - 12)];
        end
    endfunction

    function automatic source_valid(input logic [5:0] source, input logic [3:0] limit);
        begin
            if (source < 4) source_valid = source[2:0] < state_count[2:0];
            else if (source < 12) source_valid = 1'b1;
            else source_valid = source < 6'd12 + {2'b0, limit};
        end
    endfunction

    function automatic signed [63:0] arithmetic(
        input logic [1:0] operation,
        input logic signed [31:0] a, b,
        input logic signed [63:0] product
    );
        logic signed [32:0] small_result;
        begin
            small_result = 0;
            case (operation)
                0: arithmetic = {{32{a[31]}}, a};
                1: begin
                    small_result = $signed({a[31], a}) + $signed({b[31], b});
                    arithmetic = {{31{small_result[32]}}, small_result};
                end
                2: begin
                    small_result = $signed({a[31], a}) - $signed({b[31], b});
                    arithmetic = {{31{small_result[32]}}, small_result};
                end
                3: arithmetic = product >>> 16;
            endcase
        end
    endfunction

    function automatic overflow(input logic signed [63:0] value);
        overflow = value != {{32{value[31]}}, value[31:0]};
    endfunction

    for (genvar n = 0; n < NODE_CAPACITY; n = n + 1) begin : nodes
        wire [5:0] a = node_config[n][7:2];
        wire [5:0] b = node_config[n][13:8];
        // Физически выбираются только состояния, параметры и предыдущие узлы.
        wire [(12+n)*32-1:0] local_values = value_bus[(12+n)*32-1:0];
        wire signed [31:0] a_value = int'(a) < 12+n ? local_values[int'(a)*32 +: 32] : 32'sd0;
        wire signed [31:0] b_value = int'(b) < 12+n ? local_values[int'(b)*32 +: 32] : 32'sd0;
        // Отдельная стадия выбора операндов удерживает допустимую частоту.
        always_ff @(posedge clk) begin
            operand_a[n] <= a_value;
            operand_b[n] <= b_value;
        end
        wire signed [63:0] product;
        wire multiplying = node_config[n][1:0] == 3;
        wire start_product = phase == EVALUATE && node_active[n] && !node_started[n] &&
                             !node_ready[n] && operands_ready[n] && multiplying;
        wire product_ready;
        dpu_product_pipe multiply (.clk(clk), .rst(clear_pipeline), .valid(start_product),
            .a(a_value), .b(b_value), .product(product), .ready(product_ready));
        assign value_bus[(12 + n) * 32 +: 32] = node_value[n];
        assign operands_ready[n] = source_ready(a, node_ready) &&
            ((node_config[n][1:0] == 0) || source_ready(b, node_ready));
        assign node_result_valid[n] = multiplying ? product_ready : fast_pending[n];
        assign node_wide[n] = arithmetic(node_config[n][1:0],
                                        operand_a[n], operand_b[n], product);
    end

    for (genvar p = 0; p < 8; p = p + 1) begin : parameters_bus
        assign value_bus[(4 + p) * 32 +: 32] = parameter_value[p];
    end

    for (genvar s = 0; s < 4; s = s + 1) begin : transitions
        assign value_bus[s * 32 +: 32] = state_value[s];
        // Раскрытие источника производной отделено от сдвига регистром.
        logic [SOURCE_COUNT-1:0] select_derivative;
        logic signed [31:0] selected_derivative;
        for (genvar i = 0; i < SOURCE_COUNT; i = i + 1) begin : selectors
            always_ff @(posedge clk) select_derivative[i] <= derivative_source[s][5:0] == 6'(i);
        end
        always_comb begin
            selected_derivative = 0;
            for (integer i = 0; i < SOURCE_COUNT; i = i + 1)
                selected_derivative = selected_derivative | (value_bus[i*32 +: 32] & {32{select_derivative[i]}});
        end
        assign derivative_value[s] = selected_derivative;
        wire signed [63:0] slow_product;
        logic signed [63:0] shifted_product;
        // Полный продукт сохраняет точность и исходные диагностические разряды.
        always_ff @(posedge clk)
            shifted_product <= $signed({{32{derivative_value[s][31]}}, derivative_value[s]}) <<< dt_power;
        assign transition_product[s] = dt_is_shift ? shifted_product : slow_product;
        dpu_product_pipe multiply (.clk(clk), .rst(clear_pipeline),
            .valid(phase == INTEGRATE && !dt_is_shift), .a(dt),
            .b(derivative_value[s]),
            .product(slow_product), .ready(transition_ready[s]));
        assign next_wide[s] = $signed({{32{state_value[s][31]}}, state_value[s]}) +
                              (transition_product[s] >>> 16);
    end

    // Признак степени двойки и показатель вычисляются при настройке, вне шага.
    always_comb begin
        decoded_dt_is_shift = 0;
        decoded_dt_power = 0;
        for (integer p = 0; p < 31; p = p + 1)
            if (dt == (32'sd1 <<< p)) begin
                decoded_dt_is_shift = 1;
                decoded_dt_power = 5'(p);
            end
    end

    // Две стадии чтения: локальные банки, затем выбор уже зарегистрированного слова.
    // Декодирование адреса не проходит через всю сеть отладочных регистров за такт.
    always_comb begin
        case (address[7:0])
            'h00: control_read = 32'h44505532;
            'h01: control_read = {16'b0, fault, 5'b0, (fault != 0), done, busy};
            'h02: control_read = completed_steps;
            'h03: control_read = elapsed_cycles;
            'h04: control_read = {18'b0, epoch_fault, (|candidate_fault), 3'b0, node_ready};
            'h05: control_read = clock_ticks[31:0];
            'h06: control_read = clock_high;
            'h10: control_read = state_count;
            'h11: control_read = node_count;
            'h12: control_read = dt;
            default: control_read = 32'hbad00002;
        endcase
        selected_read_bank = 11;
        case (address[15:8])
            0: selected_read_bank = 0;
            1: if (address[7:2] == 0) selected_read_bank = 1;
            2: if (address[7:3] == 0) selected_read_bank = 2;
            3: if (address[7:4] == 0 && address[3:0] < 9) selected_read_bank = 3;
            4: if (address[7:2] == 0) selected_read_bank = 4;
            5: if (address[7:4] == 0 && address[3:0] < 9) selected_read_bank = 5;
            6: if (address[3:2] == 0) begin
                case (address[7:4])
                    0: selected_read_bank = 6;
                    1: selected_read_bank = 7;
                    2: selected_read_bank = 8;
                    3: selected_read_bank = 9;
                    4: selected_read_bank = 10;
                    default: begin end
                endcase
            end
            default: begin end
        endcase
    end

    always_ff @(posedge clk) if (command_valid && command == 3) begin
        read_words[0] <= control_read;
        read_words[1] <= state_value[address[1:0]];
        read_words[2] <= parameter_value[address[2:0]];
        read_words[3] <= node_config[address[3:0]];
        read_words[4] <= derivative_source[address[1:0]];
        read_words[5] <= node_value[address[3:0]];
        read_words[6] <= next_wide[address[1:0]][31:0];
        read_words[7] <= next_wide[address[1:0]][63:32];
        read_words[8] <= transition_product[address[1:0]][31:0];
        read_words[9] <= transition_product[address[1:0]][63:32];
        read_words[10] <= derivative_value[address[1:0]];
    end

    always_comb begin
        configuration_valid = state_count > 0 && state_count <= 4 &&
                              node_count <= NODE_CAPACITY && dt > 0;
        all_ready = 1'b1;
        for (integer n = 0; n < NODE_CAPACITY; n = n + 1) begin
            if (node_active[n]) begin
                if (!node_configuration_valid[n]) configuration_valid = 1'b0;
                if (!node_ready[n] && !node_result_valid[n]) all_ready = 1'b0;
            end
        end
        for (integer s = 0; s < 4; s = s + 1) begin
            if (state_active[s]) begin
                if (!derivative_configuration_valid[s]) configuration_valid = 1'b0;
            end
        end
    end

    // Проверки образа не входят в путь арифметики или сброса эпохи.
    always_ff @(posedge clk) begin
        if (rst) begin
            node_configuration_valid <= 0;
            derivative_configuration_valid <= 0;
            configuration_checked <= 0;
            dt_is_shift <= 0;
            dt_power <= 0;
        end else begin
            dt_is_shift <= decoded_dt_is_shift;
            dt_power <= decoded_dt_power;
            for (integer n = 0; n < NODE_CAPACITY; n = n + 1)
                node_configuration_valid[n] <= node_config[n][31:14] == 0 &&
                    source_valid(node_config[n][7:2], 4'(n)) &&
                    (node_config[n][1:0] == 0 || source_valid(node_config[n][13:8], 4'(n)));
            for (integer s = 0; s < 4; s = s + 1)
                derivative_configuration_valid[s] <= derivative_source[s][31:6] == 0 &&
                    source_valid(derivative_source[s][5:0], node_count[3:0]);
            configuration_checked <= configuration_valid;
        end
    end

    // Декодирование управления отделено регистром от разрешений записи банков.
    always_ff @(posedge clk) begin
        if (rst) begin
            control_valid <= 0;
            control_clear <= 0;
            control_command <= 0;
            control_data <= 0;
            write_target <= 0;
            decoded_node_active <= 0;
            decoded_state_active <= 0;
            control_read_bank <= 11;
            control_clock_read <= 0;
            sampled_clock_high <= 0;
        end else begin
            control_valid <= command_valid;
            control_clear <= command_valid && command == 1;
            control_command <= command;
            control_data <= write_data;
            control_read_bank <= selected_read_bank;
            control_clock_read <= address == 16'h0005;
            sampled_clock_high <= clock_ticks[63:32];
            write_target[0] <= address == 16'h0010;
            write_target[1] <= address == 16'h0011;
            write_target[2] <= address == 16'h0012;
            for (integer i = 0; i < NODE_CAPACITY; i = i + 1)
                decoded_node_active[i] <= write_data > i && write_data <= NODE_CAPACITY;
            for (integer i = 0; i < 4; i = i + 1) begin
                decoded_state_active[i] <= write_data > i && write_data <= 4;
                write_target[3+i] <= address == 16'h0100 + 16'(i);
                write_target[24+i] <= address == 16'h0400 + 16'(i);
            end
            for (integer i = 0; i < 8; i = i + 1)
                write_target[7+i] <= address == 16'h0200 + 16'(i);
            for (integer i = 0; i < NODE_CAPACITY; i = i + 1)
                write_target[15+i] <= address == 16'h0300 + 16'(i);
        end
    end

    // Счётчик независимой частоты не очищается командой CLEAR нашего ядра.
    always_ff @(posedge clk) begin
        if (rst) clock_ticks <= 0;
        else clock_ticks <= clock_ticks + 1;
    end

    always_ff @(posedge clk) begin
        if (clear_pipeline) begin
            phase <= IDLE;
            busy <= 0;
            done <= 0;
            fault <= 0;
            reply <= 0;
            reply_valid <= !rst;
            state_count <= 0;
            node_count <= 0;
            dt <= 0;
            target_steps <= 0;
            completed_steps <= 0;
            elapsed_cycles <= 0;
            node_ready <= 0;
            node_active <= 0;
            state_active <= 0;
            node_started <= 0;
            node_fault <= 0;
            fast_pending <= 0;
            candidate_fault <= 0;
            clock_high <= 0;
            for (integer s = 0; s < 4; s = s + 1) begin
                state_value[s] <= 0;
                derivative_source[s] <= 32'hffffffff;
                candidate_state[s] <= 0;
            end
            for (integer p = 0; p < 8; p = p + 1) parameter_value[p] <= 0;
            for (integer n = 0; n < NODE_CAPACITY; n = n + 1) begin
                node_config[n] <= 32'hffffffff;
                node_value[n] <= 0;
            end
        end else begin
            fast_pending <= 0;
            reply_valid <= 0;
            if (busy) elapsed_cycles <= elapsed_cycles + 1;
            case (phase)
                EVALUATE: begin
                    // Каждый узел имеет собственный регистр и постоянную операцию.
                    for (integer n = 0; n < NODE_CAPACITY; n = n + 1) begin
                        if (node_active[n] && !node_started[n] && !node_ready[n] && operands_ready[n]) begin
                            node_started[n] <= 1;
                            if (node_config[n][1:0] != 3) fast_pending[n] <= 1;
                        end
                        if (node_active[n] && !node_ready[n] && node_result_valid[n]) begin
                            node_value[n] <= node_wide[n][31:0];
                            node_ready[n] <= 1;
                            node_fault[n] <= overflow(node_wide[n]);
                        end
                    end
                    if (all_ready) phase <= INTEGRATE;
                end
                INTEGRATE: phase <= dt_is_shift ? FAST_TRANSITION : WAIT_PRODUCT;
                FAST_TRANSITION, WAIT_PRODUCT: if (phase == FAST_TRANSITION || &transition_ready) begin
                    // Ошибка сохраняется локально вместе с пробным состоянием.
                    for (integer s = 0; s < 4; s = s + 1) begin
                        candidate_state[s] <= next_wide[s][31:0];
                        candidate_fault[s] <= state_active[s] && overflow(next_wide[s]);
                    end
                    phase <= COMMIT;
                end
                COMMIT: begin
                    if (epoch_fault || (|candidate_fault)) begin
                        fault <= 3;
                        busy <= 0;
                        phase <= IDLE;
                    end else begin
                        // Все пробные значения проверены до общей записи.
                        for (integer s = 0; s < 4; s = s + 1)
                            if (state_active[s]) state_value[s] <= candidate_state[s];
                        completed_steps <= completed_steps + 1;
                        if (completed_steps + 1 == target_steps) begin
                            busy <= 0;
                            done <= 1;
                            phase <= IDLE;
                        end else begin
                            node_ready <= 0;
                            node_started <= 0;
                            node_fault <= 0;
                            phase <= EVALUATE;
                        end
                    end
                end
                VALIDATE_WAIT: phase <= VALIDATE;
                VALIDATE: begin
                    reply_valid <= 1;
                    reply <= 0;
                    if (!configuration_checked) begin
                        fault <= 2;
                        reply <= 32'hbad00003;
                        phase <= IDLE;
                    end else begin
                        busy <= target_steps != 0;
                        done <= target_steps == 0;
                        phase <= target_steps == 0 ? IDLE : EVALUATE;
                    end
                end
                default: begin end
            endcase

            if (control_valid) begin
                reply_valid <= 1;
                case (control_command)
                    2: begin
                        reply <= 0;
                        if (busy) reply <= 32'hbad00001;
                        else begin
                            done <= 0;
                            fault <= 0;
                            if (write_target[0]) begin
                                state_count <= control_data;
                                state_active <= decoded_state_active;
                            end
                            if (write_target[1]) begin
                                node_count <= control_data;
                                node_active <= decoded_node_active;
                            end
                            if (write_target[2]) dt <= control_data;
                            for (integer i = 0; i < 4; i = i + 1) begin
                                if (write_target[3+i]) state_value[i] <= control_data;
                                if (write_target[24+i]) derivative_source[i] <= control_data;
                            end
                            for (integer i = 0; i < 8; i = i + 1)
                                if (write_target[7+i]) parameter_value[i] <= control_data;
                            for (integer i = 0; i < NODE_CAPACITY; i = i + 1)
                                if (write_target[15+i]) node_config[i] <= control_data;
                            if (!(|write_target)) reply <= 32'hbad00002;
                        end
                    end
                    3: begin
                        reply <= control_read_bank <= 10 ? read_words[control_read_bank] : 32'hbad00002;
                        if (control_clock_read) clock_high <= sampled_clock_high;
                    end
                    4: begin
                        reply <= 0;
                        if (busy) reply <= 32'hbad00001;
                        else begin
                            done <= 0;
                            fault <= 0;
                            completed_steps <= 0;
                            elapsed_cycles <= 0;
                            target_steps <= control_data;
                            node_ready <= 0;
                            node_started <= 0;
                            node_fault <= 0;
                            reply_valid <= 0;
                            phase <= VALIDATE_WAIT;
                        end
                    end
                    default: reply <= 32'hbad00004;
                endcase
            end
        end
    end
endmodule
`default_nettype wire

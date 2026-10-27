// Только SDP 512×36, общий такт, READ_FIRST, чтение A и запись B.
// AMD UG473, таблица 1-15: младшие/старшие данные и чётность разделены по портам.
    reg [35:0] model_words [0:511];
    reg [35:0] model_read;
    wire [31:0] model_input = {DIBDI, DIADI};
    wire [3:0] model_parity = {DIPBDIP, DIPADIP};
    assign {DOPBDOP, DOPADOP, DOBDO, DOADO} = model_read;
    always @(posedge CLKARDCLK) begin
        if (CLKBWRCLK !== 1'b1 || (|WEA)) $fatal(1, "Неподдерживаемый такт или запись A модели RAMB18");
        if (ENARDEN) begin
            if (RSTRAMARSTRAM) model_read <= {SRVAL_B[17:16], SRVAL_A[17:16], SRVAL_B[15:0], SRVAL_A[15:0]};
            else model_read <= model_words[ADDRARDADDR[13:5]];
        end
        if (ENBWREN)
            for (integer byte_index=0; byte_index<4; byte_index=byte_index+1)
                if (WEBWE[byte_index]) begin
                    model_words[ADDRBWRADDR[13:5]][byte_index*8+:8] <= model_input[byte_index*8+:8];
                    model_words[ADDRBWRADDR[13:5]][32+byte_index] <= model_parity[byte_index];
                end
    end

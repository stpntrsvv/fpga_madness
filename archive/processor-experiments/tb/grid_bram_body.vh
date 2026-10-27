// Только режим этого опыта: TDP 1024×36, общий такт, READ_FIRST, запись через A.
// Основание: AMD UG473, Read/Write Operation и Block RAM Port Signals.
// Заголовок примитива берётся из установленного Yosys; другие режимы отклоняются.
    reg [35:0] model_words [0:1023];
    reg [35:0] model_a, model_b;
    assign {DOPADOP, DOADO} = model_a;
    assign {DOPBDOP, DOBDO} = model_b;
    assign {CASCADEOUTA, CASCADEOUTB, ECCPARITY, RDADDRECC, SBITERR, DBITERR} = 21'b0;
    always @(posedge CLKARDCLK) begin
        if (CLKBWRCLK !== 1'b1 || (|WEBWE)) $fatal(1, "Неподдерживаемый такт или запись B модели BRAM");
        if (ENARDEN) begin
            if (RSTRAMARSTRAM) model_a <= SRVAL_A;
            else model_a <= model_words[ADDRARDADDR[14:5]];
            for (integer byte_index=0; byte_index<4; byte_index=byte_index+1)
                if (WEA[byte_index]) begin
                    model_words[ADDRARDADDR[14:5]][byte_index*8+:8] <= DIADI[byte_index*8+:8];
                    model_words[ADDRARDADDR[14:5]][32+byte_index] <= DIPADIP[byte_index];
                end
        end
        if (ENBWREN) begin
            if (RSTRAMB) model_b <= SRVAL_B;
            else model_b <= model_words[ADDRBWRADDR[14:5]];
        end
    end

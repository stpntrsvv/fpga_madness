`default_nettype none
// Восемь фиксированных трактов от внутреннего CFGMCLK, без интерфейса PS.
module grid8_top (output wire user_led_n);
    grid_top #(.LANES(8)) engine (.user_led_n(user_led_n));
endmodule
`default_nettype wire

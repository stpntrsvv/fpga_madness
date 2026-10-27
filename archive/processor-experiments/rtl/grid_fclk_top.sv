`default_nettype none
// Фиксированный тепловой GRD1 с аппаратным источником тактов от PS.
module grid_fclk_top (output wire user_led_n);
    grid_top #(.USE_PS_FCLK(1)) engine (.user_led_n(user_led_n));
endmodule
`default_nettype wire

`default_nettype none
// Восемь фиксированных тепловых трактов от FCLK0.
module grid_fclk8_top (output wire user_led_n);
    grid_top #(.USE_PS_FCLK(1), .LANES(8)) engine (.user_led_n(user_led_n));
endmodule
`default_nettype wire

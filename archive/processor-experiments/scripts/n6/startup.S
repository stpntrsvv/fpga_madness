.syntax unified
.thumb
.section .vectors,"a",%progbits
.global vectors
vectors:
.word _estack
.word Reset_Handler
.rept 14
.word exception_handler
.endr
.section .text.Reset_Handler,"ax",%progbits
.thumb_func
.global Reset_Handler
Reset_Handler:
 cpsid i
 ldr r0, =_estack
 msr msp, r0
 /* GCC может сохранить векторные регистры уже в прологе C-функции. */
 ldr r0, =0xe000ed88
 ldr r1, [r0]
 orr r1, r1, #0x00f00000
 str r1, [r0]
 dsb
 isb
 ldr r0, =0xe000ed08
 ldr r1, =vectors
 str r1, [r0]
 dsb
 isb
 bl n6_main
 b .

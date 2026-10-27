/* Адрес AXISRAM2 из официального шаблона ST Template_FSBL_LRUN. */
ENTRY(Reset_Handler)
MEMORY { RAM (rwx) : ORIGIN = 0x34180400, LENGTH = 511K
 DTCM (rw) : ORIGIN = 0x30000000, LENGTH = 128K }
_estack = ORIGIN(RAM) + LENGTH(RAM);
SECTIONS {
 .vectors : { KEEP(*(.vectors)) } > RAM
 .text : { *(.text*) *(.rodata*) } > RAM
 .ARM.exidx : { *(.ARM.exidx*) } > RAM
 .data : { *(.data*) } > RAM
 .bss (NOLOAD) : { . = ALIGN(32); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(32); _ebss = .; } > RAM
 .dtcm (NOLOAD) : { . = ALIGN(32); _sdtcm = .; *(.dtcm) . = ALIGN(32); _edtcm = .; } > DTCM
 ASSERT(_edtcm <= ORIGIN(DTCM) + LENGTH(DTCM), "Недостаточно DTCM")
 ASSERT(_ebss + 8192 < _estack, "Недостаточно памяти для стека N6")
}

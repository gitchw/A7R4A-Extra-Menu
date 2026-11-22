SECTIONS {
 . = 0x00000000;
 .text : {
  KEEP(*(.text.entry))
  *(.text*)
  *(.rodata*)
  . = ALIGN(4);
  KEEP(*(.a7r4a_tail))
 }
 /DISCARD/ : {
  *(.ARM.exidx*)
  *(.ARM.extab*)
  *(.comment*)
  *(.note*)
  *(.got*)
  *(.rel*)
  *(.data*)
  *(.bss*)
 }
}

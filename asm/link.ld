/*
 * Linker script for ARM64 code cave injection in libbluetooth_jni.so
 * Target base address: 0xE5A370 (in extended PT_LOAD segment)
 */

ret_0x90c9cc = 0x90c9cc;
ret_0x90cb4c = 0x90cb4c;
ret_0x90cbcc = 0x90cbcc;
ret_0x911544 = 0x911544;
ret_0x911668 = 0x911668;
sym_s16ChannelMode = 0xeddf26;

SECTIONS {
    . = 0xE5A370;
    .text : { *(.text) }
}

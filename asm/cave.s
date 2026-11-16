/*
 * cave.s - ARM64 Code Cave hooks for SBC XQ (Dual Channel 452/552 kbps)
 *
 * Implements:
 * 1. hook1_chmask: Intercepts codec channel capability negotiation.
 *    Checks if codec_specific_3 equals 0x5851 ('XQ'). If true and sink
 *    supports Dual Channel (bit 2), select Dual Channel mask.
 *
 * 2. hook2_place_c: Intercepts codec configuration placement logic to
 *    prioritize Dual Channel mode when SBC XQ is active.
 *
 * 3. hook3_rate_hi: Intercepts SBC high target bitrate selection.
 *    If s16ChannelMode == 1 (SBC_DUAL_CHANNEL), elevates target bitrate
 *    from 328 kbps to 552 kbps.
 *
 * 4. hook4_rate_lo: Intercepts SBC low target bitrate fallback selection.
 *    If s16ChannelMode == 1 (SBC_DUAL_CHANNEL), elevates low target bitrate
 *    from 229 kbps to 328 kbps.
 */

.text
.global hook1_chmask
.global hook2_place_c
.global hook3_rate_hi
.global hook4_rate_lo

hook1_chmask:
    ldr x10, [x19, #0x188]      /* Load codec_specific_3 from btav_a2dp_codec_config_t */
    mov w11, #0x5851            /* 'XQ' in ASCII / 22609 */
    cmp x10, x11
    b.ne 1f
    tst w8, #0x4                /* Test if sink advertises SBC_DUAL_CHANNEL (bit 2) */
    mov w10, #0xc
    mov w11, #0xf
    csel w10, w10, w11, ne      /* Select Dual Channel mask */
    b ret_0x90c9cc
1:
    mov w10, #0x9               /* Fallback to stock Joint Stereo (0x09) */
    b ret_0x90c9cc

hook2_place_c:
    ldr x12, [x19, #0x188]
    mov w13, #0x5851
    cmp x12, x13
    b.ne 1f
    tbz w10, #2, 1f             /* Test bit 2 of channel mode */
    b ret_0x90cb4c
1:
    tbz w8, #0, 2f
    b ret_0x90cbcc
2:
    b ret_0x90cb4c

hook3_rate_hi:
    adrp x11, sym_s16ChannelMode
    ldrh w11, [x11, :lo12:sym_s16ChannelMode]
    cmp w11, #1                 /* 1 = SBC_DUAL_CHANNEL */
    mov w11, #552               /* High bitrate target: 552 kbps */
    mov w21, #328               /* Stock Stereo high target: 328 kbps */
    csel w21, w11, w21, eq
    b ret_0x911544

hook4_rate_lo:
    adrp x11, sym_s16ChannelMode
    ldrh w11, [x11, :lo12:sym_s16ChannelMode]
    cmp w11, #1                 /* 1 = SBC_DUAL_CHANNEL */
    mov w11, #328               /* Low bitrate target: 328 kbps */
    mov w21, #229               /* Stock Stereo low target: 229 kbps */
    csel w21, w11, w21, eq
    b ret_0x911668

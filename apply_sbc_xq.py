#!/usr/bin/env python3
"""
apply_sbc_xq.py - Automated HyperOS / Android SBC XQ Enabler & Patcher

Patches:
  1. libbluetooth_jni.so (inside com.android.bt.apex):
     - Enables SBC_MONO | SBC_DUAL | SBC_STEREO | SBC_JOINT in a2dp_sbc_source_caps
       and setPeerCodecCapabilities.
     - Extends executable PT_LOAD with a 0x200-byte ARM64 code cave at 0xE5A370:
       * Checks codec_specific_3 == 0x5851 ("XQ") in A2DP SBC config selection to
         choose Dual Channel (0x04) vs stock Stereo/Joint (0x09).
       * Forces Dual Channel placement and switches target bitrates to 552 kbps
         (fallback 328 kbps) when s16ChannelMode == 1 (SBC_DUAL_CHANNEL).
  2. Settings.apk (hybrid repack preserving original V2/V3/V4 APK Sig Block 42):
     - Disables BluetoothA2dpHwOffloadPreferenceController so HyperOS UI never
       resets persist.bluetooth.a2dp_offload.disabled to "false".
     - Injects "SBC XQ" right below "SBC" in BluetoothAudioCodecPreferenceController
       using codecSpecific3 = 0x5851.
  3. Boot init & KernelSU/Magisk scripts:
     - ROM mode: /system/etc/init/sbc_xq.rc + /system/lib64/sbc_xq/libbluetooth_jni.so
       + /system_ext/priv-app/Settings/Settings.apk (removes Settings/oat).
     - ADB live mode: post-fs-data.d/sbc_xq_prop.sh, service.d/sbc_xq.sh,
       /data/adb/sbc_xq/libbluetooth_jni.so, and /data/adb/modules/sbc_xq/ (nomount).

Usage:
  python3 apply_sbc_xq.py --dry-run
  python3 apply_sbc_xq.py --apply-rom --run-sync-verify
  python3 apply_sbc_xq.py --apply-adb
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import io
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DEFAULT_ROM_DIR = "/home/emre/ROM_Mutfagi/OS4.0.5.0_work/extracted"
DEFAULT_APKTOOL_GLOBS = [
    "/home/emre/.gemini/antigravity/brain/*/scratch/tools/apktool.jar",
    "/home/emre/ROM_Mutfagi/**/apktool*.jar",
]
DEFAULT_SYNC_VERIFY_GLOBS = [
    "/home/emre/.gemini/antigravity/brain/*/scratch/sync_and_verify.py",
    "/home/emre/ROM_Mutfagi/**/sync_and_verify.py",
]

CAVE_VA = 0xE5A370
CAVE_RESERVE = 0x200

ASM_SRC = """
.text
.global hook1_chmask
.global hook2_place_c
.global hook3_rate_hi
.global hook4_rate_lo

hook1_chmask:
    ldr x10, [x19, #0x188]
    mov w11, #0x5851
    cmp x10, x11
    b.ne 1f
    tst w8, #0x4
    mov w10, #0xc
    mov w11, #0xf
    csel w10, w10, w11, ne
    b ret_0x90c9cc
1:
    mov w10, #0x9
    b ret_0x90c9cc

hook2_place_c:
    ldr x12, [x19, #0x188]
    mov w13, #0x5851
    cmp x12, x13
    b.ne 1f
    tbz w10, #2, 1f
    b ret_0x90cb4c
1:
    tbz w8, #0, 2f
    b ret_0x90cbcc
2:
    b ret_0x90cb4c

hook3_rate_hi:
    adrp x11, sym_s16ChannelMode
    ldrh w11, [x11, :lo12:sym_s16ChannelMode]
    cmp w11, #1
    mov w11, #552
    mov w21, #328
    csel w21, w11, w21, eq
    b ret_0x911544

hook4_rate_lo:
    adrp x11, sym_s16ChannelMode
    ldrh w11, [x11, :lo12:sym_s16ChannelMode]
    cmp w11, #1
    mov w11, #328
    mov w21, #229
    csel w21, w11, w21, eq
    b ret_0x911668
"""

LINKER_SCRIPT = f"""
ret_0x90c9cc = 0x90c9cc;
ret_0x90cb4c = 0x90cb4c;
ret_0x90cbcc = 0x90cbcc;
ret_0x911544 = 0x911544;
ret_0x911668 = 0x911668;
sym_s16ChannelMode = 0xeddf26;
SECTIONS {{
    . = {hex(CAVE_VA)};
    .text : {{ *(.text) }}
}}
"""

SBC_XQ_RC = """# Enable SBC XQ (Dual Channel 452/551 kbps) at boot before audio HAL/audioserver and after APEX activation
on post-fs-data
    setprop persist.bluetooth.a2dp_offload.disabled true
    setprop persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

on property:apexd.status=ready
    mount overlay overlay /apex/com.android.bt/lib64 lowerdir=/system/lib64/sbc_xq:/apex/com.android.bt/lib64
    setprop persist.bluetooth.a2dp_offload.disabled true
    setprop persist.bluetooth.a2dp_offload.cap aac-ldac-mihc
"""

SBC_XQ_PROP_SH = """#!/system/bin/sh
# Set A2DP offload props in post-fs-data BEFORE audioserver & audio HAL start
resetprop -n persist.bluetooth.a2dp_offload.disabled true
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc
"""

SBC_XQ_SH = """#!/system/bin/sh
# Ensure SBC XQ properties and bind-mount patched libbluetooth_jni.so across namespaces
resetprop -n persist.bluetooth.a2dp_offload.disabled true
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

TGT=/apex/com.android.bt/lib64/libbluetooth_jni.so
SRC=/data/adb/sbc_xq/libbluetooth_jni.so
STAGE=/dev/sbc_xq/libbluetooth_jni.so

i=0
while [ ! -f "$TGT" ] && [ $i -lt 30 ]; do
    sleep 1
    i=$((i + 1))
done
[ -f "$TGT" ] && [ -f "$SRC" ] || exit 0

mkdir -p /dev/sbc_xq
cp -f "$SRC" "$STAGE"
chmod 0644 "$STAGE"
chcon u:object_r:system_lib_file:s0 "$STAGE" 2>/dev/null || true

for p in 1 $(pidof zygote64) $(pidof zn-nsdaemon-zygote) $(pidof usap64); do
    [ -n "$p" ] || continue
    grep -q " $TGT " "/proc/$p/mounts" 2>/dev/null || \
        nsenter --mount="/proc/$p/ns/mnt" mount -o bind,ro "$STAGE" "$TGT" 2>/dev/null || true
done

BT_PID=$(pidof com.android.bluetooth)
if [ -n "$BT_PID" ]; then
    CUR_MD5=$(md5sum "/proc/$BT_PID/root$TGT" 2>/dev/null | cut -d' ' -f1)
    NEW_MD5=$(md5sum "$STAGE" | cut -d' ' -f1)
    if [ "$CUR_MD5" != "$NEW_MD5" ]; then
        kill "$BT_PID"
    fi
fi
"""

MODULE_PROP = """id=sbc_xq
name=SBC XQ & DevOpts Codec Toggle
version=v1.0
versionCode=1
author=apply_sbc_xq.py
description=Mounts patched Settings.apk with SBC XQ Developer Options entry and offload lock.
"""

PATCHED_BLUETOOTH_AUDIO_CODEC_SMALI = """.class public Lcom/android/settings/development/BluetoothAudioCodecPreferenceController;
.super Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;
.source "SourceFile"


# direct methods
.method public constructor <init>(Landroid/content/Context;Lcom/android/settingslib/core/lifecycle/Lifecycle;Lcom/android/settings/development/BluetoothA2dpConfigStore;)V
    .locals 0

    .line 39
    invoke-direct {p0, p1, p2, p3}, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;-><init>(Landroid/content/Context;Lcom/android/settingslib/core/lifecycle/Lifecycle;Lcom/android/settings/development/BluetoothA2dpConfigStore;)V

    return-void
.end method

.method private insertSbcXq([Ljava/lang/String;)[Ljava/lang/String;
    .locals 5

    array-length v0, p1

    const/16 v1, 0xe

    if-eq v0, v1, :cond_0

    return-object p1

    :cond_0
    add-int/lit8 v1, v0, 0x1

    new-array v1, v1, [Ljava/lang/String;

    const/4 v2, 0x2

    const/4 v3, 0x0

    invoke-static {p1, v3, v1, v3, v2}, Ljava/lang/System;->arraycopy(Ljava/lang/Object;ILjava/lang/Object;II)V

    const-string v4, "SBC XQ"

    aput-object v4, v1, v2

    sub-int v4, v0, v2

    const/4 v3, 0x3

    invoke-static {p1, v2, v1, v3, v4}, Ljava/lang/System;->arraycopy(Ljava/lang/Object;ILjava/lang/Object;II)V

    return-object v1
.end method


# virtual methods
.method public displayPreference(Landroidx/preference/PreferenceScreen;)V
    .locals 1

    invoke-super {p0, p1}, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->displayPreference(Landroidx/preference/PreferenceScreen;)V

    iget-object p1, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mPreference:Lcom/android/settingslib/miuisettings/preference/miuix/DropDownPreference;

    if-eqz p1, :cond_0

    invoke-virtual {p0}, Lcom/android/settings/development/BluetoothAudioCodecPreferenceController;->getListValues()[Ljava/lang/String;

    move-result-object v0

    invoke-virtual {p1, v0}, Lcom/android/settingslib/miuisettings/preference/miuix/DropDownPreference;->setEntries([Ljava/lang/CharSequence;)V

    invoke-virtual {p1, v0}, Lmiuix/preference/DropDownPreference;->setEntryValues([Ljava/lang/CharSequence;)V

    const/4 p0, 0x0

    aget-object p0, v0, p0

    invoke-virtual {p1, p0}, Lmiuix/preference/DropDownPreference;->setValue(Ljava/lang/String;)V

    :cond_0
    return-void
.end method

.method getA2dpActiveDevice()Landroid/bluetooth/BluetoothDevice;
    .locals 2

    .line 247
    iget-object p0, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothAdapter:Landroid/bluetooth/BluetoothAdapter;

    const/4 v0, 0x0

    if-nez p0, :cond_0

    return-object v0

    :cond_0
    const/4 v1, 0x2

    .line 251
    invoke-virtual {p0, v1}, Landroid/bluetooth/BluetoothAdapter;->getActiveDevices(I)Ljava/util/List;

    move-result-object p0

    .line 252
    invoke-interface {p0}, Ljava/util/List;->size()I

    move-result v1

    if-lez v1, :cond_1

    const/4 v0, 0x0

    invoke-interface {p0, v0}, Ljava/util/List;->get(I)Ljava/lang/Object;

    move-result-object p0

    check-cast p0, Landroid/bluetooth/BluetoothDevice;

    return-object p0

    :cond_1
    return-object v0
.end method

.method protected getCurrentA2dpSettingIndex(Landroid/bluetooth/BluetoothCodecConfig;)I
    .locals 5

    invoke-virtual {p1}, Landroid/bluetooth/BluetoothCodecConfig;->getCodecType()I

    move-result v0

    if-nez v0, :cond_1

    invoke-virtual {p1}, Landroid/bluetooth/BluetoothCodecConfig;->getCodecSpecific3()J

    move-result-wide v0

    const-wide/16 v2, 0x5851

    cmp-long p0, v0, v2

    if-nez p0, :cond_0

    const/4 p0, 0x2

    return p0

    :cond_0
    const/4 p0, 0x1

    return p0

    :cond_1
    sparse-switch v0, :sswitch_data_0

    const/4 p0, 0x0

    return p0

    :sswitch_0
    const/4 p0, 0x3

    return p0

    :sswitch_1
    const/4 p0, 0x4

    return p0

    :sswitch_2
    const/4 p0, 0x5

    return p0

    :sswitch_3
    const/4 p0, 0x6

    return p0

    :sswitch_4
    const/4 p0, 0x7

    return p0

    :sswitch_5
    const/16 p0, 0x8

    return p0

    :sswitch_6
    const/16 p0, 0xb

    return p0

    :sswitch_7
    const/16 p0, 0xa

    return p0

    :sswitch_8
    const/16 p0, 0xc

    return p0

    :sswitch_9
    const/16 p0, 0x9

    return p0

    :sswitch_data_0
    .sparse-switch
        0x1 -> :sswitch_0
        0x2 -> :sswitch_1
        0x3 -> :sswitch_2
        0x4 -> :sswitch_3
        0x8 -> :sswitch_4
        0x9 -> :sswitch_5
        0xc -> :sswitch_6
        0xd -> :sswitch_7
        0xe -> :sswitch_8
        0xf -> :sswitch_9
    .end sparse-switch
.end method

.method protected getDefaultIndex()I
    .locals 0

    const/4 p0, 0x0

    return p0
.end method

.method protected getListSummaries()[Ljava/lang/String;
    .locals 2

    .line 55
    iget-object v0, p0, Lcom/android/settingslib/core/AbstractPreferenceController;->mContext:Landroid/content/Context;

    invoke-virtual {v0}, Landroid/content/Context;->getResources()Landroid/content/res/Resources;

    move-result-object v0

    sget v1, Lcom/android/settings/R$array;->bluetooth_a2dp_codec_summaries:I

    invoke-virtual {v0, v1}, Landroid/content/res/Resources;->getStringArray(I)[Ljava/lang/String;

    move-result-object v0

    invoke-direct {p0, v0}, Lcom/android/settings/development/BluetoothAudioCodecPreferenceController;->insertSbcXq([Ljava/lang/String;)[Ljava/lang/String;

    move-result-object p0

    return-object p0
.end method

.method protected getListValues()[Ljava/lang/String;
    .locals 2

    .line 49
    iget-object v0, p0, Lcom/android/settingslib/core/AbstractPreferenceController;->mContext:Landroid/content/Context;

    invoke-virtual {v0}, Landroid/content/Context;->getResources()Landroid/content/res/Resources;

    move-result-object v0

    sget v1, Lcom/android/settings/R$array;->bluetooth_a2dp_codec_titles:I

    invoke-virtual {v0, v1}, Landroid/content/res/Resources;->getStringArray(I)[Ljava/lang/String;

    move-result-object v0

    invoke-direct {p0, v0}, Lcom/android/settings/development/BluetoothAudioCodecPreferenceController;->insertSbcXq([Ljava/lang/String;)[Ljava/lang/String;

    move-result-object p0

    return-object p0
.end method

.method public getPreferenceKey()Ljava/lang/String;
    .locals 0

    .line 44
    const-string p0, "bluetooth_select_a2dp_codec"

    return-object p0
.end method

.method protected writeConfigurationValues(Ljava/lang/Object;)V
    .locals 5

    iget-object v0, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mPreference:Lcom/android/settingslib/miuisettings/preference/miuix/DropDownPreference;

    invoke-virtual {p1}, Ljava/lang/Object;->toString()Ljava/lang/String;

    move-result-object p1

    invoke-virtual {v0, p1}, Lmiuix/preference/DropDownPreference;->findIndexOfValue(Ljava/lang/String;)I

    move-result p1

    const/4 v0, 0x0

    const v1, 0xf4240

    const-wide/16 v2, 0x0

    packed-switch p1, :pswitch_data_0

    const/4 v1, 0x0

    goto :goto_0

    :pswitch_0
    iget-object p1, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mPreference:Lcom/android/settingslib/miuisettings/preference/miuix/DropDownPreference;

    invoke-virtual {p1}, Lmiuix/preference/DropDownPreference;->getValue()Ljava/lang/String;

    move-result-object p1

    iget-object v4, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mPreference:Lcom/android/settingslib/miuisettings/preference/miuix/DropDownPreference;

    invoke-virtual {v4, p1}, Lmiuix/preference/DropDownPreference;->findIndexOfValue(Ljava/lang/String;)I

    move-result p1

    const/4 v1, 0x0

    packed-switch p1, :pswitch_data_1

    goto :goto_0

    :pswitch_1
    const/4 v0, 0x1

    goto :goto_0

    :pswitch_2
    const/4 v0, 0x2

    goto :goto_0

    :pswitch_3
    const/4 v0, 0x3

    goto :goto_0

    :pswitch_4
    const/4 v0, 0x4

    goto :goto_0

    :pswitch_5
    const/16 v0, 0x8

    goto :goto_0

    :pswitch_6
    const/16 v0, 0x9

    goto :goto_0

    :pswitch_7
    const/16 v0, 0xf

    goto :goto_0

    :pswitch_8
    const/16 v0, 0xd

    goto :goto_0

    :pswitch_9
    const/16 v0, 0xc

    goto :goto_0

    :pswitch_a
    const/16 v0, 0xe

    goto :goto_0

    :pswitch_b
    const-wide/16 v2, 0x5851

    goto :goto_0

    :pswitch_c
    const/4 v0, 0x1

    goto :goto_0

    :pswitch_d
    const/4 v0, 0x2

    goto :goto_0

    :pswitch_e
    const/4 v0, 0x3

    goto :goto_0

    :pswitch_f
    const/4 v0, 0x4

    goto :goto_0

    :pswitch_10
    const/16 v0, 0x8

    goto :goto_0

    :pswitch_11
    const/16 v0, 0x9

    goto :goto_0

    :pswitch_12
    const/16 v0, 0xf

    goto :goto_0

    :pswitch_13
    const/16 v0, 0xd

    goto :goto_0

    :pswitch_14
    const/16 v0, 0xc

    goto :goto_0

    :pswitch_15
    const/16 v0, 0xe

    goto :goto_0

    :pswitch_16
    iget-object p1, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dpConfigStore:Lcom/android/settings/development/BluetoothA2dpConfigStore;

    monitor-enter p1

    :try_start_0
    iget-object v0, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dp:Landroid/bluetooth/BluetoothA2dp;

    if-eqz v0, :cond_0

    invoke-virtual {p0}, Lcom/android/settings/development/BluetoothAudioCodecPreferenceController;->getA2dpActiveDevice()Landroid/bluetooth/BluetoothDevice;

    move-result-object v0

    if-eqz v0, :cond_0

    iget-object p0, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dp:Landroid/bluetooth/BluetoothA2dp;

    invoke-virtual {p0, v0}, Landroid/bluetooth/BluetoothA2dp;->enableOptionalCodecs(Landroid/bluetooth/BluetoothDevice;)V

    :cond_0
    monitor-exit p1

    return-void

    :catchall_0
    move-exception p0

    monitor-exit p1
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    throw p0

    :pswitch_17
    iget-object p1, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dpConfigStore:Lcom/android/settings/development/BluetoothA2dpConfigStore;

    monitor-enter p1

    :try_start_1
    iget-object v0, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dp:Landroid/bluetooth/BluetoothA2dp;

    if-eqz v0, :cond_1

    invoke-virtual {p0}, Lcom/android/settings/development/BluetoothAudioCodecPreferenceController;->getA2dpActiveDevice()Landroid/bluetooth/BluetoothDevice;

    move-result-object v0

    if-eqz v0, :cond_1

    iget-object p0, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dp:Landroid/bluetooth/BluetoothA2dp;

    invoke-virtual {p0, v0}, Landroid/bluetooth/BluetoothA2dp;->disableOptionalCodecs(Landroid/bluetooth/BluetoothDevice;)V

    :cond_1
    monitor-exit p1

    return-void

    :catchall_1
    move-exception p0

    monitor-exit p1
    :try_end_1
    .catchall {:try_start_1 .. :try_end_1} :catchall_1

    throw p0

    :goto_0
    :pswitch_18
    iget-object p1, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dpConfigStore:Lcom/android/settings/development/BluetoothA2dpConfigStore;

    invoke-virtual {p1, v0}, Lcom/android/settings/development/BluetoothA2dpConfigStore;->setCodecType(I)V

    iget-object p1, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dpConfigStore:Lcom/android/settings/development/BluetoothA2dpConfigStore;

    invoke-virtual {p1, v1}, Lcom/android/settings/development/BluetoothA2dpConfigStore;->setCodecPriority(I)V

    iget-object p0, p0, Lcom/android/settings/development/AbstractBluetoothA2dpPreferenceController;->mBluetoothA2dpConfigStore:Lcom/android/settings/development/BluetoothA2dpConfigStore;

    invoke-virtual {p0, v2, v3}, Lcom/android/settings/development/BluetoothA2dpConfigStore;->setCodecSpecific3Value(J)V

    return-void

    nop

    :pswitch_data_0
    .packed-switch 0x0
        :pswitch_0
        :pswitch_18
        :pswitch_b
        :pswitch_c
        :pswitch_d
        :pswitch_e
        :pswitch_f
        :pswitch_10
        :pswitch_11
        :pswitch_12
        :pswitch_13
        :pswitch_14
        :pswitch_15
        :pswitch_16
        :pswitch_17
    .end packed-switch

    :pswitch_data_1
    .packed-switch 0x3
        :pswitch_1
        :pswitch_2
        :pswitch_3
        :pswitch_4
        :pswitch_5
        :pswitch_6
        :pswitch_7
        :pswitch_8
        :pswitch_9
        :pswitch_a
    .end packed-switch
.end method
"""


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=capture, capture_output=capture, check=check)


def require_tools(tools: list[str]) -> dict[str, str]:
    resolved = {}
    missing = []
    for t in tools:
        p = shutil.which(t)
        if not p:
            missing.append(t)
        else:
            resolved[t] = p
    if missing:
        raise RuntimeError(f"Missing required host tools: {', '.join(missing)}")
    return resolved


def find_first(explicit: str | None, globs_list: list[str]) -> str | None:
    if explicit:
        if not os.path.exists(explicit):
            raise FileNotFoundError(f"Specified path not found: {explicit}")
        return explicit
    for pattern in globs_list:
        matches = sorted(glob.glob(pattern, recursive=True))
        if matches:
            return matches[0]
    return None


def find_apktool(explicit: str | None) -> list[str]:
    if explicit:
        if explicit.endswith(".jar"):
            return ["java", "-jar", explicit]
        return [explicit]
    env_jar = os.environ.get("APKTOOL_JAR")
    if env_jar and os.path.exists(env_jar):
        return ["java", "-jar", env_jar]
    jar = find_first(None, DEFAULT_APKTOOL_GLOBS)
    if jar:
        return ["java", "-jar", jar]
    bin_path = shutil.which("apktool")
    if bin_path:
        return [bin_path]
    raise FileNotFoundError("Could not find apktool.jar or apktool binary; pass --apktool")


def encode_arm64_b(src_va: int, dst_va: int) -> bytes:
    delta = dst_va - src_va
    if delta % 4 != 0:
        raise ValueError(f"Unaligned branch delta: {delta}")
    imm26 = (delta >> 2) & 0x03FFFFFF
    return struct.pack("<I", 0x14000000 | imm26)


def extract_stock_libbluetooth_jni(src_path: str, out_path: str) -> None:
    """Extract pristine libbluetooth_jni.so from .apex or copy if .so given."""
    if src_path.endswith(".apex"):
        require_tools(["7z", "debugfs"])
        with tempfile.TemporaryDirectory() as td:
            run(["7z", "e", "-y", f"-o{td}", src_path, "apex_payload.img"], capture=True)
            img = os.path.join(td, "apex_payload.img")
            if not os.path.exists(img):
                raise RuntimeError(f"apex_payload.img missing in {src_path}")
            run(["debugfs", "-R", f"dump /lib64/libbluetooth_jni.so {out_path}", img], capture=True)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"Failed to extract /lib64/libbluetooth_jni.so from {src_path}")
    else:
        shutil.copy2(src_path, out_path)


def is_libbt_already_patched(data: bytes) -> bool:
    return (
        len(data) > CAVE_VA + CAVE_RESERVE
        and data[0x10C3DB] == 0x2F
        and data[0x90D268 : 0x90D26C] == bytes.fromhex("eb018052")
        and any(b != 0 for b in data[CAVE_VA : CAVE_VA + CAVE_RESERVE])
    )


def patch_libbluetooth_jni(src_path: str, out_path: str, force: bool = False) -> str:
    """Extract/read stock libbluetooth_jni.so, apply ARM64 SBC XQ hooks, write out_path."""
    with tempfile.TemporaryDirectory() as td:
        raw_so = os.path.join(td, "libbluetooth_jni.so.orig")
        extract_stock_libbluetooth_jni(src_path, raw_so)
        data = bytearray(open(raw_so, "rb").read())

        if is_libbt_already_patched(data):
            if force:
                raise RuntimeError(
                    f"Input {src_path} is already patched; pass stock .so or com.android.bt.apex with --force"
                )
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(data)
            return f"reused already-patched libbluetooth_jni.so (md5={md5_file(out_path)})"

        require_tools([
            "aarch64-linux-gnu-as",
            "aarch64-linux-gnu-ld",
            "aarch64-linux-gnu-objcopy",
            "aarch64-linux-gnu-nm",
        ])

        if not all(b == 0 for b in data[CAVE_VA : CAVE_VA + CAVE_RESERVE]):
            raise RuntimeError(f"Code cave region {hex(CAVE_VA)}..{hex(CAVE_VA + CAVE_RESERVE)} is not zeroed")

        # Extend executable PT_LOAD segment (flags=5, vaddr=0x328000)
        phoff, _, _, _, phentsize, phnum = struct.unpack_from("<QQIHHH", data, 32)
        extended = False
        for i in range(phnum):
            off = phoff + i * phentsize
            p_type, p_flags, _, p_vaddr, _, p_filesz, _, _ = struct.unpack_from("<IIQQQQQQ", data, off)
            if p_type == 1 and p_flags == 5 and p_vaddr == 0x328000:
                if p_vaddr + p_filesz != CAVE_VA:
                    raise RuntimeError(f"Unexpected PH[{i}] end {hex(p_vaddr + p_filesz)} != {hex(CAVE_VA)}")
                new_sz = p_filesz + CAVE_RESERVE
                if p_vaddr + new_sz > 0xE5C000:
                    raise RuntimeError("PT_LOAD extension overflows into next segment")
                struct.pack_into("<QQ", data, off + 32, new_sz, new_sz)
                extended = True
                break
        if not extended:
            raise RuntimeError("Executable PT_LOAD (flags=5, vaddr=0x328000) not found")

        # Capability & setPeerCodecCapabilities flags (keep stock default_config 0x21 at 0x10bddb)
        if data[0x10C3DB] != 0x29:
            raise RuntimeError(f"Unexpected byte at 0x10C3DB: {hex(data[0x10C3DB])}")
        data[0x10C3DB] = 0x2F

        if data[0x90D268 : 0x90D26C] != bytes.fromhex("2b018052"):
            raise RuntimeError(f"Unexpected insn at 0x90D268: {data[0x90D268:0x90D26C].hex()}")
        data[0x90D268 : 0x90D26C] = bytes.fromhex("eb018052")

        s_path = os.path.join(td, "cave.s")
        o_path = os.path.join(td, "cave.o")
        elf_path = os.path.join(td, "cave.elf")
        bin_path = os.path.join(td, "cave.bin")
        ld_path = os.path.join(td, "link.ld")

        Path(s_path).write_text(ASM_SRC, encoding="utf-8")
        Path(ld_path).write_text(LINKER_SCRIPT, encoding="utf-8")

        run(["aarch64-linux-gnu-as", s_path, "-o", o_path])
        run(["aarch64-linux-gnu-ld", "-T", ld_path, o_path, "-o", elf_path])
        run(["aarch64-linux-gnu-objcopy", "-O", "binary", "--only-section=.text", elf_path, bin_path])
        nm_out = run(["aarch64-linux-gnu-nm", "-n", elf_path], capture=True).stdout
        syms = {}
        for line in nm_out.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                syms[parts[2]] = int(parts[0], 16)

        cave_bytes = Path(bin_path).read_bytes()
        if len(cave_bytes) > CAVE_RESERVE:
            raise RuntimeError(f"Code cave overflow: {len(cave_bytes)} > {CAVE_RESERVE}")
        data[CAVE_VA : CAVE_VA + len(cave_bytes)] = cave_bytes

        hook_checks = [
            (0x90C9C8, bytes.fromhex("2a018052"), "hook1_chmask"),
            (0x90CB48, bytes.fromhex("28040037"), "hook2_place_c"),
            (0x911540, bytes.fromhex("15298052"), "hook3_rate_hi"),
            (0x911664, bytes.fromhex("b51c8052"), "hook4_rate_lo"),
        ]
        for va, expected, sym in hook_checks:
            actual = bytes(data[va : va + 4])
            if actual != expected:
                raise RuntimeError(f"Unexpected bytes at {hex(va)}: {actual.hex()} != {expected.hex()}")
            data[va : va + 4] = encode_arm64_b(va, syms[sym])

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        Path(out_path).write_bytes(data)
        return f"patched fresh libbluetooth_jni.so (cave={len(cave_bytes)}/{CAVE_RESERVE}B, md5={md5_file(out_path)})"


def extract_apk_sig_block(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    eocd = data.rfind(b"PK\x05\x06")
    if eocd == -1:
        raise RuntimeError(f"EOCD not found in {path}")
    cd_off = struct.unpack_from("<I", data, eocd + 16)[0]
    if data[cd_off - 16 : cd_off] != b"APK Sig Block 42":
        raise RuntimeError(f"Missing APK Sig Block 42 magic in {path}")
    bsize = struct.unpack_from("<Q", data, cd_off - 24)[0]
    start = cd_off - bsize - 8
    blk = data[start:cd_off]
    top_size = struct.unpack_from("<Q", blk, 0)[0]
    if top_size != bsize or len(blk) != bsize + 8:
        raise RuntimeError(f"Corrupt APK Sig Block 42 in {path}")
    return blk


def build_hybrid_apk(stock_path: str, replace_map: dict[str, bytes], out_path: str) -> None:
    sig_block = extract_apk_sig_block(stock_path)
    bio = io.BytesIO()
    with zipfile.ZipFile(stock_path, "r") as z_stock:
        with zipfile.ZipFile(bio, "w") as z_out:
            for src_info in z_stock.infolist():
                fn = src_info.filename
                payload = replace_map[fn] if fn in replace_map else z_stock.read(fn)

                zi = zipfile.ZipInfo(fn, date_time=src_info.date_time)
                zi.compress_type = src_info.compress_type
                zi.create_system = src_info.create_system
                zi.external_attr = src_info.external_attr
                zi.internal_attr = src_info.internal_attr

                if zi.compress_type == zipfile.ZIP_STORED:
                    cur_pos = bio.tell()
                    fname_len = len(zi._encodeFilenameFlags()[0])
                    hdr_end = cur_pos + 30 + fname_len
                    pad = (4 - (hdr_end % 4)) % 4
                    zi.extra = b"\x00" * pad
                    z_out.writestr(zi, payload, compress_type=zipfile.ZIP_STORED)
                else:
                    zi.extra = b""
                    z_out.writestr(zi, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)

    raw = bio.getvalue()
    eocd = raw.rfind(b"PK\x05\x06")
    if eocd == -1:
        raise RuntimeError("Built APK missing EOCD")
    cd_sz, cd_off = struct.unpack_from("<II", raw, eocd + 12)
    if cd_off + cd_sz != eocd:
        raise RuntimeError("Central directory offset/size mismatch")

    pad4k = (4096 - (cd_off % 4096)) % 4096
    new_cd_off = cd_off + pad4k + len(sig_block)

    eocd_patched = bytearray(raw[eocd:])
    struct.pack_into("<I", eocd_patched, 16, new_cd_off)

    final_bytes = (
        raw[:cd_off]
        + (b"\x00" * pad4k)
        + sig_block
        + raw[cd_off:eocd]
        + bytes(eocd_patched)
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(final_bytes)


def verify_hybrid_apk(stock_path: str, replace_map: dict[str, bytes], out_path: str) -> None:
    with zipfile.ZipFile(stock_path, "r") as zs, zipfile.ZipFile(out_path, "r") as zo:
        bad = zo.testzip()
        if bad is not None:
            raise RuntimeError(f"testzip failed on {out_path}: {bad}")
        s_names = [i.filename for i in zs.infolist()]
        o_names = [i.filename for i in zo.infolist()]
        if s_names != o_names:
            raise RuntimeError("Hybrid APK entry list mismatch")

        actual_diffs = set()
        with open(out_path, "rb") as fp:
            for si, oi in zip(zs.infolist(), zo.infolist()):
                if si.compress_type != oi.compress_type:
                    raise RuntimeError(f"Compression mismatch on {si.filename}")
                if oi.compress_type == zipfile.ZIP_STORED:
                    fp.seek(oi.header_offset + 26)
                    nl, el = struct.unpack("<HH", fp.read(4))
                    doff = oi.header_offset + 30 + nl + el
                    if doff % 4 != 0:
                        raise RuntimeError(f"Unaligned STORED {oi.filename} @ {doff}")
                if (si.CRC, si.file_size) != (oi.CRC, oi.file_size):
                    actual_diffs.add(oi.filename)
                    if oi.filename not in replace_map or zo.read(oi.filename) != replace_map[oi.filename]:
                        raise RuntimeError(f"Replaced entry mismatch: {oi.filename}")

        if actual_diffs != set(replace_map.keys()):
            raise RuntimeError(f"Unexpected APK diffs: {actual_diffs} != {set(replace_map.keys())}")
        if extract_apk_sig_block(out_path) != extract_apk_sig_block(stock_path):
            raise RuntimeError("APK Sig Block 42 mismatch after hybrid repack")


def verify_settings_apk_state(apk_path: str) -> tuple[bool, str | None]:
    """Return (already_patched_ok, classes_dex_name)."""
    with zipfile.ZipFile(apk_path, "r") as zf:
        if zf.testzip() is not None:
            raise RuntimeError(f"Corrupt ZIP: {apk_path}")
        for name in zf.namelist():
            if name.startswith("classes") and name.endswith(".dex"):
                dex = zf.read(name)
                if b"insertSbcXq" in dex and b"SBC XQ" in dex:
                    hdr_sz = struct.unpack_from("<I", dex, 32)[0]
                    if dex[:4] == b"dex\n" and hdr_sz == len(dex):
                        extract_apk_sig_block(apk_path)
                        return True, name
    return False, None


def smali_dir_to_dex_name(smali_dirname: str) -> str:
    if smali_dirname == "smali":
        return "classes.dex"
    m = re.fullmatch(r"smali_classes(\d+)", smali_dirname)
    if not m:
        raise RuntimeError(f"Unexpected smali directory name: {smali_dirname}")
    return f"classes{m.group(1)}.dex"


def patch_offload_controller_smali(text: str) -> str:
    pattern = re.compile(
        r"\.method private isA2dpOffloadPreferenceEnable\(Landroid/content/Context;\)Z\b.*?\.end method",
        re.DOTALL,
    )
    replacement = (
        ".method private isA2dpOffloadPreferenceEnable(Landroid/content/Context;)Z\n"
        "    .locals 0\n\n"
        "    const/4 p0, 0x0\n\n"
        "    return p0\n"
        ".end method"
    )
    patched, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Expected 1 isA2dpOffloadPreferenceEnable method, replaced {count}")
    return patched


def patch_settings_apk(src_apk: str, out_apk: str, apktool_cmd: list[str], force: bool = False) -> str:
    already_patched, existing_dex = verify_settings_apk_state(src_apk)
    if already_patched and not force:
        os.makedirs(os.path.dirname(out_apk), exist_ok=True)
        shutil.copy2(src_apk, out_apk)
        return f"reused verified patched Settings.apk ({existing_dex}, md5={md5_file(out_apk)})"

    with tempfile.TemporaryDirectory() as td:
        dec_dir = os.path.join(td, "settings_dec")
        run(apktool_cmd + ["d", "--no-res", "-f", src_apk, "-o", dec_dir], capture=True)

        offload_rel = "com/android/settings/development/BluetoothA2dpHwOffloadPreferenceController.smali"
        codec_rel = "com/android/settings/development/BluetoothAudioCodecPreferenceController.smali"

        offload_hits = sorted(glob.glob(os.path.join(dec_dir, "smali*", offload_rel)))
        codec_hits = sorted(glob.glob(os.path.join(dec_dir, "smali*", codec_rel)))
        if len(offload_hits) != 1 or len(codec_hits) != 1:
            raise RuntimeError(
                f"Expected 1 offload & 1 codec smali, found offload={offload_hits}, codec={codec_hits}"
            )

        offload_smali_dir = Path(offload_hits[0]).relative_to(dec_dir).parts[0]
        codec_smali_dir = Path(codec_hits[0]).relative_to(dec_dir).parts[0]
        if offload_smali_dir != codec_smali_dir:
            raise RuntimeError(f"Controllers split across {offload_smali_dir} and {codec_smali_dir}")

        offload_txt = Path(offload_hits[0]).read_text(encoding="utf-8")
        Path(offload_hits[0]).write_text(patch_offload_controller_smali(offload_txt), encoding="utf-8")
        Path(codec_hits[0]).write_text(PATCHED_BLUETOOTH_AUDIO_CODEC_SMALI, encoding="utf-8")

        # Compile only the modified DEX in an isolated api-34 minimal apktool project
        # (avoids Apktool 2.11.1 zeroing DEX headers when minSdkVersion=37).
        mini_dir = os.path.join(td, "mini_build")
        os.makedirs(mini_dir, exist_ok=True)
        Path(os.path.join(mini_dir, "apktool.yml")).write_text(
            "version: 2.11.1\n"
            "apkFileName: smali3.apk\n"
            "isFrameworkApk: false\n"
            "sdkInfo:\n"
            "  minSdkVersion: 34\n"
            "  targetSdkVersion: 34\n"
            "packageInfo:\n"
            "  forcedPackageId: 127\n"
            "versionInfo:\n"
            "  versionCode: 37\n"
            "  versionName: 17\n",
            encoding="utf-8",
        )
        Path(os.path.join(mini_dir, "AndroidManifest.xml")).write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.android.settings"/>\n',
            encoding="utf-8",
        )
        shutil.move(os.path.join(dec_dir, codec_smali_dir), os.path.join(mini_dir, "smali"))

        mini_apk = os.path.join(td, "mini_out.apk")
        run(apktool_cmd + ["b", "--api", "34", mini_dir, "-o", mini_apk], capture=True)

        with zipfile.ZipFile(mini_apk, "r") as z3:
            dex_bytes = z3.read("classes.dex")
        if dex_bytes[:4] != b"dex\n":
            raise RuntimeError(f"Invalid DEX magic: {dex_bytes[:8]!r}")
        hdr_size = struct.unpack_from("<I", dex_bytes, 32)[0]
        if hdr_size != len(dex_bytes):
            raise RuntimeError(f"Compiled DEX size mismatch: header={hdr_size}, actual={len(dex_bytes)}")
        if b"SBC XQ" not in dex_bytes or b"insertSbcXq" not in dex_bytes:
            raise RuntimeError("Compiled DEX missing SBC XQ symbols")

        target_dex_name = smali_dir_to_dex_name(codec_smali_dir)
        replace_map = {target_dex_name: dex_bytes}
        build_hybrid_apk(src_apk, replace_map, out_apk)
        verify_hybrid_apk(src_apk, replace_map, out_apk)
        return f"compiled {target_dex_name} + hybrid-packed Settings.apk (md5={md5_file(out_apk)})"


def apply_to_rom(
    rom_dir: str,
    libbt_out: str,
    settings_out: str,
    rc_out: str,
    run_sync_verify: bool,
    sync_script: str | None,
) -> None:
    rom_libbt = os.path.join(rom_dir, "system/system/lib64/sbc_xq/libbluetooth_jni.so")
    rom_rc = os.path.join(rom_dir, "system/system/etc/init/sbc_xq.rc")
    rom_settings = os.path.join(rom_dir, "system_ext/priv-app/Settings/Settings.apk")
    rom_settings_oat = os.path.join(rom_dir, "system_ext/priv-app/Settings/oat")

    os.makedirs(os.path.dirname(rom_libbt), exist_ok=True)
    os.makedirs(os.path.dirname(rom_rc), exist_ok=True)
    os.makedirs(os.path.dirname(rom_settings), exist_ok=True)

    shutil.copy2(libbt_out, rom_libbt)
    os.chmod(rom_libbt, 0o644)

    shutil.copy2(rc_out, rom_rc)
    os.chmod(rom_rc, 0o644)

    shutil.copy2(settings_out, rom_settings)
    os.chmod(rom_settings, 0o644)

    if os.path.exists(rom_settings_oat):
        shutil.rmtree(rom_settings_oat)
        print(f"Removed stale OAT dir: {rom_settings_oat}")

    print(f"ROM updated:\n  {rom_libbt}\n  {rom_rc}\n  {rom_settings}")

    if run_sync_verify:
        script = find_first(sync_script, DEFAULT_SYNC_VERIFY_GLOBS)
        if not script:
            raise FileNotFoundError("sync_and_verify.py not found; pass --sync-script")
        print(f"Running {script} ...")
        run(["python3", script])


def apply_to_adb(
    serial: str | None,
    libbt_out: str,
    settings_out: str,
    prop_sh_out: str,
    service_sh_out: str,
) -> None:
    require_tools(["adb"])
    base = ["adb"] + (["-s", serial] if serial else [])

    uid = run(base + ["shell", "su", "-c", "id -u"], capture=True).stdout.strip()
    if uid != "0":
        raise RuntimeError(f"Root shell check failed (uid={uid})")

    stage = "/data/local/tmp/sbc_xq_stage"
    run(base + ["shell", "rm", "-rf", stage])
    run(base + ["shell", "mkdir", "-p", stage])
    for path, name in [
        (libbt_out, "libbluetooth_jni.so"),
        (settings_out, "Settings.apk"),
        (prop_sh_out, "sbc_xq_prop.sh"),
        (service_sh_out, "sbc_xq.sh"),
    ]:
        run(base + ["push", path, f"{stage}/{name}"])

    with tempfile.NamedTemporaryFile("w", suffix=".prop", delete=False) as tf:
        tf.write(MODULE_PROP)
        mod_prop_tmp = tf.name
    try:
        run(base + ["push", mod_prop_tmp, f"{stage}/module.prop"])
    finally:
        os.unlink(mod_prop_tmp)

    install_cmd = (
        f"mkdir -p /data/adb/sbc_xq /data/adb/post-fs-data.d /data/adb/service.d "
        f"/data/adb/modules/sbc_xq/system_ext/priv-app/Settings/oat && "
        f"cp -f {stage}/libbluetooth_jni.so /data/adb/sbc_xq/libbluetooth_jni.so && "
        f"chmod 0644 /data/adb/sbc_xq/libbluetooth_jni.so && "
        f"chcon u:object_r:system_lib_file:s0 /data/adb/sbc_xq/libbluetooth_jni.so 2>/dev/null || true && "
        f"cp -f {stage}/sbc_xq_prop.sh /data/adb/post-fs-data.d/sbc_xq_prop.sh && "
        f"chmod 0755 /data/adb/post-fs-data.d/sbc_xq_prop.sh && "
        f"cp -f {stage}/sbc_xq.sh /data/adb/service.d/sbc_xq.sh && "
        f"chmod 0755 /data/adb/service.d/sbc_xq.sh && "
        f"cp -f {stage}/module.prop /data/adb/modules/sbc_xq/module.prop && "
        f"chmod 0644 /data/adb/modules/sbc_xq/module.prop && "
        f"cp -f {stage}/Settings.apk /data/adb/modules/sbc_xq/system_ext/priv-app/Settings/Settings.apk && "
        f"chmod 0644 /data/adb/modules/sbc_xq/system_ext/priv-app/Settings/Settings.apk && "
        f"chcon u:object_r:system_file:s0 /data/adb/modules/sbc_xq/system_ext/priv-app/Settings/Settings.apk 2>/dev/null || true && "
        f"touch /data/adb/modules/sbc_xq/system_ext/priv-app/Settings/oat/.replace && "
        f"chmod 0644 /data/adb/modules/sbc_xq/system_ext/priv-app/Settings/oat/.replace && "
        f"rm -rf {stage} /data/system/package_cache/* /data/data/com.android.settings/cache/* "
        f"/data/user_de/0/com.android.settings/cache/* && "
        f"am force-stop com.android.settings && "
        f"sh /data/adb/service.d/sbc_xq.sh"
    )
    run(base + ["shell", "su", "-c", install_cmd])
    print("ADB deployment finished. Note: a reboot is needed if nomount has not yet mounted Settings.apk.")


def resolve_inputs(rom_dir: str, settings_arg: str | None, libbt_arg: str | None) -> tuple[str, str]:
    parent_dir = os.path.dirname(os.path.abspath(rom_dir))

    if libbt_arg:
        src_libbt = libbt_arg
    else:
        candidates = [
            os.path.join(parent_dir, "patcher_files/libbluetooth_jni.so.orig"),
            os.path.join(rom_dir, "system/system/apex/com.android.bt.apex"),
        ]
        src_libbt = next((c for c in candidates if os.path.exists(c)), "")
    if not src_libbt or not os.path.exists(src_libbt):
        raise FileNotFoundError(f"Could not find stock libbluetooth_jni.so or com.android.bt.apex. Pass --libbt /path/to/libbluetooth_jni.so or --rom-dir /path/to/extracted_rom (tried rom_dir={rom_dir})")

    if settings_arg:
        src_settings = settings_arg
    else:
        candidates = [
            os.path.join(parent_dir, "patcher_files/Settings.apk.orig"),
            os.path.join(parent_dir, "patcher_files/Settings.apk"),
            os.path.join(rom_dir, "system_ext/priv-app/Settings/Settings.apk"),
        ]
        src_settings = next((c for c in candidates if os.path.exists(c)), "")
    if not src_settings or not os.path.exists(src_settings):
        raise FileNotFoundError(f"Could not find stock Settings.apk. Pass --settings /path/to/Settings.apk or --rom-dir /path/to/extracted_rom (tried rom_dir={rom_dir})")

    return src_libbt, src_settings


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automated SBC XQ + DevOpts Codec Toggle Builder/Deployer")
    p.add_argument("--rom-dir", default=DEFAULT_ROM_DIR, help=f"Extracted ROM directory (default: {DEFAULT_ROM_DIR})")
    p.add_argument("--out-dir", help="Output directory for generated artifacts")
    p.add_argument("--dry-run", action="store_true", help="Build and verify artifacts in --out-dir without modifying ROM or phone")
    p.add_argument("--apply-rom", action="store_true", help="Write patched files into --rom-dir")
    p.add_argument("--apply-adb", action="store_true", help="Deploy modules & scripts to rooted device over ADB")
    p.add_argument("--adb-serial", help="Target ADB device serial")
    p.add_argument("--settings", help="Explicit path to stock Settings.apk")
    p.add_argument("--libbt", help="Explicit path to stock libbluetooth_jni.so or com.android.bt.apex")
    p.add_argument("--apktool", help="Path to apktool.jar or apktool executable")
    p.add_argument("--force", action="store_true", help="Recompile Settings.apk even if already patched")
    p.add_argument("--run-sync-verify", action="store_true", help="Run sync_and_verify.py after --apply-rom")
    p.add_argument("--sync-script", help="Explicit path to sync_and_verify.py")
    p.add_argument("--build-module", nargs="?", const="sbc_xq_magisk_module.zip", help="Build a flashable Magisk/KernelSU ZIP module (default name: sbc_xq_magisk_module.zip)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not (args.dry_run or args.apply_rom or args.apply_adb):
        print("No mode selected; defaulting to --dry-run (pass --apply-rom and/or --apply-adb to deploy).")
        args.dry_run = True

    src_libbt, src_settings = resolve_inputs(args.rom_dir, args.settings, args.libbt)
    apktool_cmd = find_apktool(args.apktool)

    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.abspath(args.rom_dir)), "sbc_xq_out")
    os.makedirs(out_dir, exist_ok=True)

    libbt_out = os.path.join(out_dir, "libbluetooth_jni.so")
    settings_out = os.path.join(out_dir, "Settings.apk")
    rc_out = os.path.join(out_dir, "sbc_xq.rc")
    prop_sh_out = os.path.join(out_dir, "sbc_xq_prop.sh")
    service_sh_out = os.path.join(out_dir, "sbc_xq.sh")

    print(f"[1/4] Sources:\n  libbt:    {src_libbt}\n  settings: {src_settings}\n  apktool:  {' '.join(apktool_cmd)}")
    lib_status = patch_libbluetooth_jni(src_libbt, libbt_out, force=args.force)
    print(f"[2/4] {lib_status}")

    set_status = patch_settings_apk(src_settings, settings_out, apktool_cmd, force=args.force)
    print(f"[3/4] {set_status}")

    Path(rc_out).write_text(SBC_XQ_RC, encoding="utf-8")
    Path(prop_sh_out).write_text(SBC_XQ_PROP_SH, encoding="utf-8")
    Path(service_sh_out).write_text(SBC_XQ_SH, encoding="utf-8")
    os.chmod(prop_sh_out, 0o755)
    os.chmod(service_sh_out, 0o755)
    print(f"[4/4] Wrote boot scripts to {out_dir}")

    if args.apply_rom and not args.dry_run:
        apply_to_rom(args.rom_dir, libbt_out, settings_out, rc_out, args.run_sync_verify, args.sync_script)

    if args.apply_adb and not args.dry_run:
        apply_to_adb(args.adb_serial, libbt_out, settings_out, prop_sh_out, service_sh_out)

    if args.build_module:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from build_module import build_zip
            zip_target = args.build_module if isinstance(args.build_module, str) else "sbc_xq_magisk_module.zip"
            build_zip(libbt_out, settings_out if os.path.isfile(settings_out) else None, zip_target)
        except Exception as e:
            print(f"Failed to package Magisk module ZIP: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

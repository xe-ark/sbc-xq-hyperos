# HyperOS SBC XQ & Hybrid A2DP Offload Enabler

An automated patcher, ARM64 assembly code-cave injector, and Magisk/KernelSU module generator that enables **SBC XQ (SBC Dual Channel at 452 / 552 kbps)** on Android (HyperOS / AOSP / LineageOS) with Developer Options UI integration and **Hybrid A2DP Offload Routing**.

---

## 🎧 Overview

Standard Android implementations restrict the Subband Codec (SBC) to **Joint Stereo** mode with a maximum bitpool of 53, capping throughput around 328 kbps. 

**SBC Dual Channel (SBC XQ)** encodes the left and right audio channels completely independently at elevated bitpools (up to bitpool 52+), reaching bitrates of **452 kbps to 552 kbps**. This provides acoustic fidelity comparable to aptX HD or LDAC while preserving lower latency and avoiding proprietary licensing constraints.

Earlier implementations forced `persist.bluetooth.a2dp_offload.disabled=true` across the entire system. While this allowed SBC-XQ to function in software, it had a major side effect: it broke hardware DSP acceleration for **LDAC (990 kbps)**, **AAC**, and **LHDC**, forcing them into software decoding.

This project introduces a **Hybrid A2DP Offload Architecture**:
* **SBC & SBC-XQ:** Routed to the **Software A2DP HAL** (`A2DP_SOFTWARE_ENCODING_DATAPATH`) at full Dual Channel 552 kbps.
* **LDAC, AAC, LHDC:** Routed to **Hardware DSP Offload** (`A2DP_HARDWARE_OFFLOAD_ENCODING_DATAPATH`) at 96 kHz / 32-bit / 990 kbps with minimum CPU usage and zero jitter.

---

## 🛠 Architecture & How It Works

This project implements a multi-layer solution across the Android Bluetooth stack and audio HAL:

### 1. ARM64 Code Cave Injection (`libbluetooth_jni.so`)
The Bluetooth stack shared object inside `com.android.bt.apex` (or `/system/lib64`) is patched:
* **Capability Bitmask Expansion:** `a2dp_sbc_source_caps` is updated (`0x29 -> 0x2F`) to advertise `SBC_DUAL` alongside mono, stereo, and joint stereo modes.
* **Peer Negotiation Hook:** Intercepts `setPeerCodecCapabilities` to negotiate Dual Channel mode when supported by the receiving sink.
* **ARM64 Assembly Code Cave:** A dedicated section is appended to the executable load segment containing 4 core hooks:
  * `hook1_chmask`: Reads `codec_specific_3` (matching ASCII `0x5851` / `"XQ"`). If matched and the headphone advertises Dual Channel capability, forces channel mask `0x0C` (`0x0F` fallback).
  * `hook2_place_c`: Prioritizes Dual Channel placement in the codec table.
  * `hook3_rate_hi`: Intercepts high target bitrate computation. When `s16ChannelMode == 1` (Dual Channel), raises target bitrate from 328 kbps to **552 kbps**.
  * `hook4_rate_lo`: Raises low target bitrate fallback from 229 kbps to **328 kbps**.

### 2. Hybrid A2DP Offload Routing
Rather than disabling hardware offload globally, offload is kept enabled:
* **Offload State:** `persist.bluetooth.a2dp_offload.disabled=false`.
* **AIDL Capability Filter:** `persist.bluetooth.a2dp_offload.cap=aac-ldac-mihc` instructs the Bluetooth stack to register offloading capability exclusively for AAC, LDAC, and MIHC/LHDC. SBC capability registration is disabled.
* **Vendor Audio Policy Overlay (`audio_policy_configuration_a2dp_offload_enable_cg_enable.xml`):**
  Strips `AUDIO_FORMAT_SBC` from the primary hardware module mixports (`BT A2DP Out`, `BT A2DP Headphones`, `BT A2DP Speaker`). When SBC / SBC-XQ connects, `audioserver` finds no matching hardware offload profile and seamlessly routes the audio to the standard software A2DP HAL. When LDAC or AAC connects, it matches the primary module and streams directly via DSP hardware offload.

### 3. UI Codec Selection (`Settings.apk`)
* Injects an entry for "SBC XQ" directly under Developer Options (`BluetoothAudioCodecPreferenceController`) with `codecSpecific3 = 0x5851`.
* **Hybrid Signature Preservation:** Preserves original APK Signature Scheme Block 42 (ID `0x7109871a`) during dex modification, allowing seamless installation on verified system partitions without signature verification errors.

### 4. Namespace Bind-Mount Injection (KernelSU / Magisk / APatch)
Because Android isolates the APEX runtime in isolated mount namespaces, standard Magisk systemless overlays do not automatically replace files inside active APEX mounts:
* An init / service worker utilizes `nsenter --mount=/proc/$PID/ns/mnt` across PID 1, `zygote64`, and `usap64` to bind-mount the patched `libbluetooth_jni.so` over `/apex/com.android.bt/lib64/libbluetooth_jni.so`.
* The vendor audio policy XML is overlaid via module tree and verified via bind mount fallback.

---

## 📁 Repository Structure

```
.
├── apply_sbc_xq.py      # Automated CLI patcher and deployment tool
├── build_module.py      # Standalone Magisk / KernelSU flashable ZIP generator
├── asm/
│   ├── cave.s          # ARM64 assembly code cave source
│   └── link.ld         # Linker script with symbol definitions
├── smali/
│   ├── BluetoothA2dpHwOffloadPreferenceController.patch.diff
│   └── BluetoothAudioCodecPreferenceController.smali
├── magisk_module/       # Module template files
│   ├── module.prop
│   ├── system.prop      # persist.bluetooth.a2dp_offload.disabled=false
│   ├── post-fs-data.sh
│   ├── service.sh       # APEX namespace mounter + XML bind fallback
│   ├── customize.sh
│   └── system/vendor/etc/
│       └── audio_policy_configuration_a2dp_offload_enable_cg_enable.xml
└── scripts/
    ├── sbc_xq.rc        # Android init configuration for ROM builds
    ├── sbc_xq_prop.sh   # Early property injection script
    └── sbc_xq.sh        # Mount namespace injector script
```

---

## 🚀 Installation & Usage

### Prerequisites
* Python 3.9+
* AArch64 GNU toolchain (`aarch64-linux-gnu-as`, `aarch64-linux-gnu-ld`)
* `apktool` (for Settings.apk smali rebuilding)
* Root access (KernelSU, APatch, or Magisk) if installing to a live device

---

### Option A: Flashable Magisk / KernelSU ZIP

To build a standalone flashable ZIP module:
```bash
python3 build_module.py \
    --libbt /path/to/patched/libbluetooth_jni.so \
    --settings /path/to/patched/Settings.apk \
    --out sbc_xq_hybrid_module.zip
```
Flash `sbc_xq_hybrid_module.zip` via KernelSU or Magisk Manager and reboot.

---

### Option B: Automated ROM Kitchen Integration

If building or modifying a custom ROM (e.g. HyperOS / MIUI / AOSP):
```bash
python3 apply_sbc_xq.py \
    --rom-dir /path/to/extracted/rom \
    --apply-rom
```
This extracts the stock binaries from your ROM tree, applies the ARM64 code cave and hybrid Settings repack, updates `/system/etc/init/sbc_xq.rc`, integrates the hybrid audio policy XML, and writes the patched artifacts directly back to the ROM structure.

---

### Option C: Live Device Deployment via ADB

With your rooted phone connected over USB:
```bash
python3 apply_sbc_xq.py \
    --libbt /path/to/stock/libbluetooth_jni.so \
    --settings /path/to/stock/Settings.apk \
    --apply-adb
```

---

## 🎧 Verified Headphone Profiles

| Headphone Model | Negotiated Codec & Mode | Bitpool / Bitrate | Datapath / Offload Status |
| :--- | :--- | :--- | :--- |
| **SoundPEATS Space Pro** | SBC Dual Channel (XQ) | **Bitpool 52 / 552 kbps** | `A2DP_SOFTWARE_ENCODING_DATAPATH` (SW) |
| **Realme Buds Air 6 Pro** | LDAC High-Res | **96 kHz / 32-bit / 990 kbps** | `A2DP_HARDWARE_OFFLOAD_ENCODING_DATAPATH` (HW) |
| **Realme Buds Air 6 Pro** | SBC Dual Channel (XQ) | **Bitpool 39 / 452 kbps** | `A2DP_SOFTWARE_ENCODING_DATAPATH` (SW) |

---

## 🔍 Verification

After booting with the module installed:

### 1. Verify Hybrid Offload Status
```bash
adb shell getprop persist.bluetooth.a2dp_offload.disabled
# Expected output: false

adb shell getprop persist.bluetooth.a2dp_offload.cap
# Expected output: aac-ldac-mihc
```

### 2. Verify SBC-XQ (Software Routing)
Connect your headphone, select **SBC XQ** in Developer Options, and check the stream:
```bash
adb shell dumpsys bluetooth_manager | grep -E "(SessionType|mBitRate|mChannelMode|mCodecSpecific3)"
```
Expected output:
* `SessionType: 0` (`A2DP_SOFTWARE_ENCODING_DATAPATH`)
* `mChannelMode: 1` (Dual Channel)
* `mBitRate: 552000` (552 kbps)
* `mCodecSpecific3: 0x5851` ("XQ")

### 3. Verify LDAC (Hardware Offload Routing)
Connect an LDAC-supported headphone:
```bash
adb shell dumpsys bluetooth_manager | grep -E "(SessionType|mBitRate|CodecConfig)"
```
Expected output:
* `SessionType: 1` (`A2DP_HARDWARE_OFFLOAD_ENCODING_DATAPATH`)
* Sample Rate: 96000 Hz, 32 bits/sample, 990 kbps (ABR HW Offload)

---

## ⚖️ License

Distributed under the Apache License, Version 2.0. See `LICENSE` for details.

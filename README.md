# HyperOS SBC XQ (Dual Channel 552 kbps) Enabler & Patcher

An automated patcher, ARM64 assembly code-cave injector, and Magisk/KernelSU module generator that enables **SBC XQ (SBC Dual Channel at 452 / 552 kbps)** on Android (HyperOS / AOSP / LineageOS) with Developer Options UI integration.

---

## 🎧 Overview

Standard Android implementations restrict the Subband Codec (SBC) to **Joint Stereo** mode with a maximum bitpool of 53, capping throughput around 328 kbps. 

**SBC Dual Channel (SBC XQ)** encodes the left and right audio channels completely independently at elevated bitpools (up to bitpool 52+), reaching bitrates of **452 kbps to 552 kbps**. This provides acoustic fidelity comparable to aptX HD or LDAC (~660 kbps) while preserving lower latency and avoiding proprietary licensing constraints.

Because Qualcomm and MediaTek hardware audio DSP offload engines only implement standard AOSP SBC profiles in their closed firmware, enabling SBC XQ requires software A2DP stack routing combined with target binary hooks.

---

## 🛠 Architecture & How It Works

This project implements a multi-layer solution across the Android Bluetooth stack:

### 1. ARM64 Code Cave Injection (`libbluetooth_jni.so`)
The Bluetooth stack shared object inside `com.android.bt.apex` (or `/system/lib64`) is patched:
* **Capability Bitmask Expansion:** `a2dp_sbc_source_caps` is updated (`0x29 -> 0x2F`) to advertise `SBC_DUAL` alongside mono, stereo, and joint stereo modes.
* **Peer Negotiation Hook:** Intercepts `setPeerCodecCapabilities` to negotiate Dual Channel mode when supported by the receiving sink.
* **ARM64 Assembly Code Cave:** A dedicated section is appended to the executable load segment containing 4 core hooks:
  * `hook1_chmask`: Reads `codec_specific_3` (matching ASCII `0x5851` / `"XQ"`). If matched and the headphone advertises Dual Channel capability, forces channel mask `0x0C` (`0x0F` fallback).
  * `hook2_place_c`: Prioritizes Dual Channel placement in the codec table.
  * `hook3_rate_hi`: Intercepts high target bitrate computation. When `s16ChannelMode == 1` (Dual Channel), raises target bitrate from 328 kbps to **552 kbps**.
  * `hook4_rate_lo`: Raises low target bitrate fallback from 229 kbps to **328 kbps**.

### 2. UI Codec Selection (`Settings.apk`)
* Injects an entry for "SBC XQ" directly under Developer Options (`BluetoothAudioCodecPreferenceController`) with `codecSpecific3 = 0x5851`.
* Disables `BluetoothA2dpHwOffloadPreferenceController` to prevent HyperOS from silently reverting system offload properties.
* **Hybrid Signature Preservation:** Preserves original APK Signature Scheme Block 42 (ID `0x7109871a`) during dex modification, allowing seamless installation on verified system partitions without signature verification errors.

### 3. Namespace Bind-Mount Injection (KernelSU / Magisk)
Because Android isolates the APEX runtime in isolated mount namespaces, standard Magisk systemless overlays do not automatically replace files inside active APEX mounts:
* An init / service worker utilizes `nsenter --mount=/proc/$PID/ns/mnt` across PID 1, `zygote64`, and `usap64` to bind-mount the patched `libbluetooth_jni.so` over `/apex/com.android.bt/lib64/libbluetooth_jni.so`.
* Early `post-fs-data` scripts enforce `persist.bluetooth.a2dp_offload.disabled=true` before `audioserver` and vendor audio HAL initialize.

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
│   ├── system.prop
│   ├── post-fs-data.sh
│   ├── service.sh
│   └── customize.sh
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

To build a standalone flashable ZIP module using your patched library:
```bash
python3 build_module.py \
    --libbt /path/to/patched/libbluetooth_jni.so \
    --settings /path/to/patched/Settings.apk \
    --out sbc_xq_module.zip
```
Flash `sbc_xq_module.zip` via KernelSU or Magisk Manager and reboot.

---

### Option B: Automated ROM Kitchen Integration

If building or modifying a custom ROM (e.g. HyperOS / MIUI / AOSP):
```bash
python3 apply_sbc_xq.py \
    --rom-dir /path/to/extracted/rom \
    --apply-rom
```
This extracts the stock binaries from your ROM tree, applies the ARM64 code cave and hybrid Settings repack, updates `/system/etc/init/sbc_xq.rc`, and writes the patched artifacts directly back to the ROM structure.

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

| Headphone Model | Negotiated Channel Mode | Bitpool | Bitrate | Dual-Connect Status |
| :--- | :--- | :--- | :--- | :--- |
| **SoundPEATS Space Pro** | Dual Channel (XQ) | **52** | **552 kbps** | Supported |
| **Realme Buds Air 6 Pro** | Dual Channel (XQ) | **39** | **452 kbps** | Supported (Note: initial stream click on some firmwares; LDAC recommended for dual-connect) |

---

## 🔍 Verification

After booting with SBC XQ enabled:
1. Open **Developer Options** -> **Bluetooth Audio Codec**.
2. Select **SBC XQ**.
3. Verify stream bitrate via ADB:
   ```bash
   adb shell dumpsys bluetooth_manager | grep -A 10 "A2DP State"
   ```
4. Verify offload status:
   ```bash
   adb shell getprop persist.bluetooth.a2dp_offload.disabled
   # Should output: true
   ```

---

## ⚖️ License

Distributed under the Apache License, Version 2.0. See `LICENSE` for details.

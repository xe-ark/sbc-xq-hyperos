#!/usr/bin/env python3
"""
build_module.py - Build Magisk / KernelSU flashable ZIP for SBC XQ Enabler

Packages:
  - module.prop
  - system.prop
  - post-fs-data.sh
  - service.sh
  - customize.sh
  - libbluetooth_jni.so (ARM64 patched binary)
  - [Optional] Settings.apk (system_ext/priv-app/Settings/Settings.apk)

Usage:
  python3 build_module.py --libbt path/to/patched/libbluetooth_jni.so [--settings path/to/Settings.apk] [--out sbc_xq_module.zip]
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def build_zip(
    libbt_path: str,
    settings_path: str | None,
    out_zip: str,
    module_dir: str | Path | None = None,
) -> str:
    if not os.path.isfile(libbt_path):
        raise FileNotFoundError(f"Patched libbluetooth_jni.so not found: {libbt_path}")

    mod_dir = Path(module_dir or (SCRIPT_DIR / "magisk_module"))
    if not mod_dir.is_dir():
        raise FileNotFoundError(f"Module template dir not found: {mod_dir}")

    required_files = ["module.prop", "system.prop", "post-fs-data.sh", "service.sh", "customize.sh"]
    for rf in required_files:
        if not (mod_dir / rf).is_file():
            raise FileNotFoundError(f"Missing template file: {mod_dir / rf}")

    out_zip_path = Path(out_zip).resolve()
    out_zip_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Creating flashable ZIP: {out_zip_path}")
    with zipfile.ZipFile(out_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # Write module files
        for rf in required_files:
            file_data = (mod_dir / rf).read_bytes()
            # Ensure unix line endings
            file_data = file_data.replace(b"\r\n", b"\n")
            zi = zipfile.ZipInfo(rf)
            zi.external_attr = 0o755 << 16 if rf.endswith(".sh") else 0o644 << 16
            z.writestr(zi, file_data)
            print(f"  + {rf}")

        # Write patched libbluetooth_jni.so
        libbt_bytes = Path(libbt_path).read_bytes()
        zi = zipfile.ZipInfo("libbluetooth_jni.so")
        zi.external_attr = 0o644 << 16
        z.writestr(zi, libbt_bytes)
        print(f"  + libbluetooth_jni.so ({len(libbt_bytes):,} bytes)")

        # Optional Settings.apk
        if settings_path and os.path.isfile(settings_path):
            apk_bytes = Path(settings_path).read_bytes()
            apk_dest = "system_ext/priv-app/Settings/Settings.apk"
            zi_apk = zipfile.ZipInfo(apk_dest)
            zi_apk.external_attr = 0o644 << 16
            z.writestr(zi_apk, apk_bytes)
            print(f"  + {apk_dest} ({len(apk_bytes):,} bytes)")

            # oat/.replace marker to prevent stale ODEX/VDEX from crashing Settings
            zi_oat = zipfile.ZipInfo("system_ext/priv-app/Settings/oat/.replace")
            zi_oat.external_attr = 0o644 << 16
            z.writestr(zi_oat, b"")
            print("  + system_ext/priv-app/Settings/oat/.replace")

    print(f"Successfully generated: {out_zip_path} ({out_zip_path.stat().st_size:,} bytes)")
    return str(out_zip_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Magisk / KernelSU module ZIP for SBC XQ")
    parser.add_argument("--libbt", required=True, help="Path to patched libbluetooth_jni.so")
    parser.add_argument("--settings", help="Optional path to patched Settings.apk")
    parser.add_argument("--out", default="sbc_xq_module.zip", help="Output ZIP path (default: sbc_xq_module.zip)")
    parser.add_argument("--module-dir", help="Path to magisk_module directory")

    args = parser.parse_args()
    try:
        build_zip(args.libbt, args.settings, args.out, args.module_dir)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/system/bin/sh
MODDIR=${0%/*}
PATH=/data/adb/ksu/bin:/data/adb/ap/bin:/data/adb/magisk:$PATH

# Ensure Hybrid A2DP properties
resetprop -n persist.bluetooth.a2dp_offload.disabled false
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

# 1. Audio policy XML bind mount fallback (for kernels/managers without vendor overlayfs)
XML_SRC="$MODDIR/system/vendor/etc/audio_policy_configuration_a2dp_offload_enable_cg_enable.xml"
XML_DST="/vendor/etc/audio_policy_configuration_a2dp_offload_enable_cg_enable.xml"

if [ -f "$XML_SRC" ] && grep -q "AUDIO_FORMAT_SBC" "$XML_DST" 2>/dev/null; then
    chcon u:object_r:vendor_configs_file:s0 "$XML_SRC" 2>/dev/null
    chmod 0644 "$XML_SRC" 2>/dev/null
    mount -o bind "$XML_SRC" "$XML_DST"
fi

# 2. Namespace bind-mount for patched libbluetooth_jni.so (if bundled in module)
TGT=/apex/com.android.bt/lib64/libbluetooth_jni.so
SRC=$MODDIR/libbluetooth_jni.so
STAGE=/dev/sbc_xq/libbluetooth_jni.so

i=0
while [ ! -f "$TGT" ] && [ $i -lt 30 ]; do
    sleep 1
    i=$((i + 1))
done

if [ -f "$TGT" ] && [ -f "$SRC" ]; then
    mkdir -p /dev/sbc_xq
    cp -f "$SRC" "$STAGE"
    chmod 0644 "$STAGE"
    chcon u:object_r:system_lib_file:s0 "$STAGE" 2>/dev/null || true

    # Mount across mount namespaces (PID 1, zygote64, usap64)
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
fi

# 3. Post-boot verification loop
(
    j=0
    while [ "$(getprop sys.boot_completed)" != "1" ] && [ $j -lt 60 ]; do
        sleep 1
        j=$((j + 1))
    done
    resetprop -n persist.bluetooth.a2dp_offload.disabled false
    resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc
    if [ -f "$XML_SRC" ] && grep -q "AUDIO_FORMAT_SBC" "$XML_DST" 2>/dev/null; then
        mount -o bind "$XML_SRC" "$XML_DST"
    fi
) &

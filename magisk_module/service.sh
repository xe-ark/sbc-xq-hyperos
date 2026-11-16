#!/system/bin/sh
MODDIR=${0%/*}

# Ensure SBC XQ properties
resetprop -n persist.bluetooth.a2dp_offload.disabled true
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

TGT=/apex/com.android.bt/lib64/libbluetooth_jni.so
SRC=$MODDIR/libbluetooth_jni.so
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

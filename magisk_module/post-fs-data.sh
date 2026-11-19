#!/system/bin/sh
MODDIR=${0%/*}
PATH=/data/adb/ksu/bin:/data/adb/ap/bin:/data/adb/magisk:$PATH

# Enforce Hybrid A2DP Offload properties early before audioserver and audio HAL initialize
resetprop -n persist.bluetooth.a2dp_offload.disabled false
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

(
    i=0
    while [ "$(getprop apexd.status)" != "ready" ] && [ $i -lt 100 ]; do
        usleep 50000
        i=$((i + 1))
    done
    usleep 100000
    resetprop -n persist.bluetooth.a2dp_offload.disabled false
    resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc
) &

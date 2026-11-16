#!/system/bin/sh
# Set A2DP offload props in post-fs-data BEFORE audioserver & audio HAL start
resetprop -n persist.bluetooth.a2dp_offload.disabled true
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

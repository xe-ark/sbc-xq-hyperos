#!/system/bin/sh
MODDIR=${0%/*}

# Set A2DP offload props early before audioserver and audio HAL initialize
resetprop -n persist.bluetooth.a2dp_offload.disabled true
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

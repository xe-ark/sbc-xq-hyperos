#!/system/bin/sh

ui_print "*****************************************"
ui_print "*   HyperOS / Android SBC XQ Enabler   *"
ui_print "*      Dual Channel 452 / 552 kbps      *"
ui_print "*****************************************"

# Set permissions
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755

if [ -f "$MODPATH/libbluetooth_jni.so" ]; then
    set_perm "$MODPATH/libbluetooth_jni.so" 0 0 0644 "u:object_r:system_lib_file:s0"
    ui_print "- Patched libbluetooth_jni.so detected"
else
    ui_print "! Warning: libbluetooth_jni.so not bundled."
    ui_print "! Make sure you patch and copy your device's libbluetooth_jni.so"
    ui_print "! to this module folder or generate via apply_sbc_xq.py --build-module"
fi

ui_print "- Disabling A2DP Hardware Offload..."
resetprop -n persist.bluetooth.a2dp_offload.disabled true
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

ui_print "- Module installation complete. Reboot required!"

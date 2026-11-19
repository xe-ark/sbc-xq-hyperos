#!/system/bin/sh

ui_print "*****************************************"
ui_print "*   HyperOS / Android SBC XQ Enabler   *"
ui_print "*       & Hybrid A2DP Offload           *"
ui_print "*   (SBC SW 552k + LDAC/AAC HW 990k)    *"
ui_print "*****************************************"

# Set permissions
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/post-fs-data.sh" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755

if [ -f "$MODPATH/libbluetooth_jni.so" ]; then
    set_perm "$MODPATH/libbluetooth_jni.so" 0 0 0644 "u:object_r:system_lib_file:s0"
    ui_print "- Patched libbluetooth_jni.so detected"
fi

if [ -f "$MODPATH/system/vendor/etc/audio_policy_configuration_a2dp_offload_enable_cg_enable.xml" ]; then
    set_perm "$MODPATH/system/vendor/etc/audio_policy_configuration_a2dp_offload_enable_cg_enable.xml" 0 0 0644 "u:object_r:vendor_configs_file:s0"
    ui_print "- Hybrid audio policy XML overlay detected"
fi

ui_print "- Configuring Hybrid A2DP Offload properties..."
resetprop -n persist.bluetooth.a2dp_offload.disabled false
resetprop -n persist.bluetooth.a2dp_offload.cap aac-ldac-mihc

ui_print "- Module installation complete. Reboot required!"

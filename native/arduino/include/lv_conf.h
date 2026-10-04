// LVGL 9.6 settings for the bench env (only what differs from LVGL's defaults).
#ifndef LV_CONF_H   // LVGL checks this name to see the file was found
#define LV_CONF_H

#define LV_USE_STDLIB_MALLOC LV_STDLIB_CLIB   // the system heap: no static pool in scarce DRAM
#define LV_DEF_REFR_PERIOD 1000    // the bench refreshes with lv_refr_now(), not the timer
#define LV_USE_LOG 0

#endif

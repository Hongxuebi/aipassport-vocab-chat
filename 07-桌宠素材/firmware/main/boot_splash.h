// main/boot_splash.h
// 开机画面：从 boot 分区读帧包，2 倍最近邻放大铺满 240×320。
// 在 app_main 里、bsp_display_init() 之后、LVGL 起来之前调用一次。
#pragma once

#include "esp_err.h"

// 播放一遍并返回；失败只记日志不阻塞开机（没有 boot 分区时返回 ESP_ERR_NOT_FOUND）。
esp_err_t boot_splash_play(void);

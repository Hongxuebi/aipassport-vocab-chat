// main/boot_splash.c —— 开机画面播放（AI-Passport / ESP32-C3 / ST7789 240×320）
//
// 素材是 120×160（屏幕的 1/4 面积），播放时**整数 2 倍最近邻放大**满屏。
// 为什么不用 240×320：满屏 RGB565 一帧就是 150 KB，C3 只有 ~400 KB SRAM 还要养 LVGL；
// 而 120×160 只要 37.5 KB，能整帧放下，放大只是每行写两遍像素，代价极小。
//
// 帧包格式和桌宠完全一样（PET1），所以直接用 pet_anim_decode()。区别只有：
// 动作名固定 "boot"、sprite 是 120×160、索引色没有透明像素（索引 0 也会被画出来）。
#include "boot_splash.h"

#include <string.h>

#include "bsp_display.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "pet_anim.h"

static const char *TAG = "boot";

#define BOOT_PART_SUBTYPE 0x41
#define BOOT_ANIM_NAME "boot"

esp_err_t boot_splash_play(void) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, BOOT_PART_SUBTYPE, "boot");
    if (!part) {
        ESP_LOGW(TAG, "没有 boot 分区，跳过开机画面");
        return ESP_ERR_NOT_FOUND;
    }

    const void *map = NULL;
    esp_partition_mmap_handle_t h;
    esp_err_t err = esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &map, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mmap 失败: %s", esp_err_to_name(err));
        return err;
    }

    // 复用 pet_anim 的解析：把这块 mmap 当成一份资产
    const pet_assets_t *a = pet_assets_from_blob(map, part->size);
    if (!a) {
        ESP_LOGE(TAG, "boot 分区不是合法帧包");
        return ESP_ERR_INVALID_RESPONSE;
    }
    const pet_anim_t *anim = pet_anim_find(a, BOOT_ANIM_NAME);
    if (!anim || anim->frames == 0) {
        ESP_LOGE(TAG, "找不到动作 \"%s\"", BOOT_ANIM_NAME);
        return ESP_ERR_NOT_FOUND;
    }

    const uint16_t sw = pet_width(a);        // 120
    const uint16_t sh = pet_height(a);       // 160
    const uint16_t dw = sw * 2, dh = sh * 2; // 240 × 320
    const uint16_t screen_w = 240, screen_h = 320;
    if (dw > screen_w || dh > screen_h) {
        ESP_LOGE(TAG, "素材 %ux%u 放大后超过屏幕", sw, sh);
        return ESP_ERR_INVALID_SIZE;
    }
    const int ox = (screen_w - dw) / 2, oy = (screen_h - dh) / 2;

    uint16_t *frame = heap_caps_malloc((size_t)sw * sh * 2, MALLOC_CAP_8BIT);
    uint16_t *line  = heap_caps_malloc((size_t)dw * 4 * 2, MALLOC_CAP_8BIT);  // 4 行一起发
    if (!frame || !line) {
        free(frame); free(line);
        return ESP_ERR_NO_MEM;
    }

    esp_lcd_panel_handle_t panel = bsp_display_panel();
    const int fps = anim->fps ? anim->fps : 6;
    const int64_t frame_us = 1000000LL / fps;

    ESP_LOGI(TAG, "开机画面：%d 帧 @ %d fps（%ux%u → %ux%u）",
             anim->frames, fps, sw, sh, dw, dh);

    for (uint16_t f = 0; f < anim->frames; f++) {
        const int64_t t0 = esp_timer_get_time();

        pet_anim_decode(a, anim, f, 0x0000, frame);   // 无透明，transparent 用不上

        // 逐 4 行打包：横向每个像素写两遍，纵向同一行连发两次
        for (uint16_t y = 0; y < sh; y += 2) {
            uint16_t *p = line;
            for (uint16_t r = 0; r < 2 && (y + r) < sh; r++) {
                const uint16_t *src = frame + (size_t)(y + r) * sw;
                for (uint16_t x = 0; x < sw; x++) {
                    const uint16_t v = src[x];
                    *p++ = v; *p++ = v;          // 横向 2 倍
                }
                // 纵向 2 倍：同样的内容再写一遍
                memcpy(p, p - dw, dw * 2);
                p += dw;
            }
            const int y0 = oy + y * 2;
            esp_lcd_panel_draw_bitmap(panel, ox, y0, ox + dw, y0 + 4, line);
        }

        const int64_t used = esp_timer_get_time() - t0;
        const int64_t wait = frame_us - used;
        if (wait > 0) vTaskDelay(pdMS_TO_TICKS((wait + 999) / 1000));
        else taskYIELD();
    }

    free(frame);
    free(line);
    ESP_LOGI(TAG, "开机画面播完");
    return ESP_OK;
}

// main/demo_pet.c —— 大肥鱼桌宠（ESP32-C3 / 240x320 / 三键版）
//
// 和 PC 版一一对应的东西：
//   · 帧包       pack_pet.py 产出的 pet_assets.bin，烧进 Flash 的 pet 分区
//   · 动画链     播完按权重抽下一段（idle 10 / 小动作 80 / 转向 10），见 pick_next()
//   · 点它       随机播一段"点击回应"
//   · 换位置     屏幕四角 + 居中轮转，模拟 PC 上的漫游落点
// 没了的东西（硬件没有）：拖拽甩飞、全屏漂浮、WebAudio 音效、27 张表情包
//
// 三键映射（长按 OK 由 main.c 统一拦截为"返回菜单"，这里不碰）：
//   OK  单击   → 点它（点击回应动画）
//   OK  双击   → 循环切到下一段动作（相当于菜单里的"随便点播"）
//   UP  单击   → 上一个动作      UP  长按 → 换位置
//   DN  单击   → 下一个动作      DN  长按 → 静音/发声（预留，当前无音效）
#include <string.h>

#include "bsp_display.h"
#include "demo.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "pet_anim.h"
#include "ui_pixel.h"

static const char *TAG = "demo_pet";

#define PET_BG_COLOR   0x1A1A22u      // 桌面底色（透明像素填它）
#define PET_TICK_MS    83             // ≈12 fps
#define PET_HIT_FRAMES 5              // "点击回应"候选段数（按名字前缀挑）

static lv_obj_t *s_scr;
static lv_obj_t *s_img;
static lv_image_dsc_t s_dsc;
static uint16_t *s_frames;            // 双缓冲：解码进 A 时 LVGL 还在读 B
static uint8_t   s_buf_idx;
static TaskHandle_t s_task;
static volatile bool s_running;

static const pet_assets_t *s_assets;
static char     s_cur[48];            // 当前动作名（拷贝出来，别长期指 Flash 目录）
static uint16_t s_frame;
static uint16_t s_total;
static uint8_t  s_fps;
static uint32_t s_next_ms;

static int s_pos_idx;
static const struct { int16_t x, y; } POS[] = {
    { 144, 208 },   // 右下
    {  16, 208 },   // 左下
    {  16,  24 },   // 左上
    { 144,  24 },   // 右上
    {  72, 112 },   // 居中
};
#define POS_COUNT (int)(sizeof(POS) / sizeof(POS[0]))

// ---------------------------------------------------------------- 动画链
static int anim_total(void) { return pet_anim_count(s_assets); }

static void play(const char *name) {
    const pet_anim_t *a = pet_anim_find(s_assets, name);
    if (!a) return;
    strncpy(s_cur, a->name, sizeof(s_cur) - 1);
    s_cur[sizeof(s_cur) - 1] = '\0';
    s_total = a->frames;
    s_fps = a->fps ? a->fps : 12;
    s_frame = 0;
    s_next_ms = 0;      // 下一拍立刻解第一帧
}

// 播完抽下一段：以 80% 概率随机挑一个非 idle 动作，其余回到待机呼吸。
// 这就是 PC 版 animationWeights 的简化：idle 10 / 转 10 / 小动作 80。
static void pick_next(void) {
    const int n = anim_total();
    if (n <= 0) return;
    if ((esp_random() % 100u) < 20u) {
        play("待机呼吸休闲");
        return;
    }
    const pet_anim_t *a = pet_anim_at(s_assets, (int)(esp_random() % (uint32_t)n));
    if (a) play(a->name);
}

// 名字前缀匹配：找到第一段 "点击回应-*"
static void play_click_reaction(void) {
    const int n = anim_total();
    for (int i = 0; i < n; i++) {
        const pet_anim_t *a = pet_anim_at(s_assets, i);
        if (a && strncmp(a->name, "点击回应", strlen("点击回应")) == 0) {
            // 随机再挑一个，别每次都同一段
            int skip = (int)(esp_random() % (uint32_t)PET_HIT_FRAMES);
            for (int k = 0; k < n && skip > 0; k++) {
                const pet_anim_t *b = pet_anim_at(s_assets, (k + i) % n);
                if (b && strncmp(b->name, "点击回应", strlen("点击回应")) == 0) skip--;
                if (skip == 0) { play(b->name); return; }
            }
            play(a->name);
            return;
        }
    }
    pick_next();
}

static void step_anim(int dir) {
    const int n = anim_total();
    if (n <= 0) return;
    int cur = 0;
    for (int i = 0; i < n; i++) {
        const pet_anim_t *a = pet_anim_at(s_assets, i);
        if (a && strcmp(a->name, s_cur) == 0) { cur = i; break; }
    }
    cur = (cur + dir + n) % n;
    const pet_anim_t *a = pet_anim_at(s_assets, cur);
    if (a) play(a->name);
}

static void move_to(int idx) {
    s_pos_idx = (idx % POS_COUNT + POS_COUNT) % POS_COUNT;
    lv_obj_set_pos(s_img, POS[s_pos_idx].x, POS[s_pos_idx].y);
}

// ---------------------------------------------------------------- 推进任务
static void pet_task(void *arg) {
    (void)arg;
    while (s_running) {
        const uint32_t now = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
        if (now >= s_next_ms) {
            s_next_ms = now + 1000u / (s_fps ? s_fps : 12);
            if (!bsp_lvgl_lock(100)) {
                vTaskDelay(pdMS_TO_TICKS(PET_TICK_MS));
                continue;
            }
            uint16_t *buf = s_frames + (s_buf_idx ? 0 : (size_t)pet_width(s_assets) * pet_height(s_assets));
            s_buf_idx ^= 1;
            if (s_total > 0) {
                pet_anim_decode(s_assets, pet_anim_find(s_assets, s_cur), s_frame,
                                PET_BG_COLOR, buf);
                s_dsc.data = buf;
                lv_obj_invalidate(s_img);
                s_frame++;
                if (s_frame >= s_total) {
                    pick_next();
                }
            }
            bsp_lvgl_unlock();
        }
        vTaskDelay(pdMS_TO_TICKS(16));
    }
    s_task = NULL;
    vTaskDelete(NULL);
}

// ---------------------------------------------------------------- 页面
void demo_pet_enter(void) {
    s_assets = pet_assets();
    if (!s_assets && pet_assets_mount() == ESP_OK) s_assets = pet_assets();

    s_scr = ui_pixel_screen_create("PET");
    if (!s_assets) {
        lv_obj_t *lbl = lv_label_create(s_scr);
        lv_label_set_text(lbl, "没有帧包\n先烧 pet 分区");
        lv_obj_center(lbl);
        lv_screen_load(s_scr);
        return;
    }

    const uint16_t w = pet_width(s_assets);
    const uint16_t h = pet_height(s_assets);
    const size_t px = (size_t)w * h;
    if (!s_frames) {
        s_frames = heap_caps_malloc(px * 2 * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!s_frames) s_frames = malloc(px * 2 * sizeof(uint16_t));   // C3 没 PSRAM 时回落到内部 RAM
    }
    if (!s_frames) {
        lv_obj_t *lbl = lv_label_create(s_scr);
        lv_label_set_text_fmt(lbl, "内存不够\n需要 %u KB", (unsigned)(px * 4 / 1024));
        lv_obj_center(lbl);
        lv_screen_load(s_scr);
        return;
    }

    memset(&s_dsc, 0, sizeof(s_dsc));
    s_dsc.header.cf = LV_COLOR_FORMAT_RGB565;
    s_dsc.header.w = w;
    s_dsc.header.h = h;
    s_dsc.header.stride = w * 2;
    s_dsc.data_size = px * 2;
    s_dsc.data = (const void *)s_frames;

    s_img = lv_image_create(s_scr);
    lv_image_set_src(s_img, &s_dsc);
    s_pos_idx = 0;
    lv_obj_set_pos(s_img, POS[0].x, POS[0].y);

    play("待机呼吸休闲");
    lv_screen_load(s_scr);
    ESP_LOGI(TAG, "桌宠页就绪: %ux%u, %d 个动作", w, h, anim_total());
}

void demo_pet_exit(void) {
    if (s_scr) { lv_obj_delete(s_scr); s_scr = NULL; s_img = NULL; }
    // 帧缓冲留着：再次进入页面不必重新申请
}

esp_err_t demo_pet_start(void) {
    if (s_task) return ESP_OK;
    s_running = true;
    if (xTaskCreate(pet_task, "pet_anim", 4096, NULL, 4, &s_task) != pdPASS) {
        s_running = false;
        s_task = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t demo_pet_stop(void) {
    s_running = false;
    // 等任务自己退出（它每 16 ms 检查一次 s_running）
    for (int i = 0; i < 60 && s_task; i++) vTaskDelay(pdMS_TO_TICKS(10));
    return ESP_OK;
}

void demo_pet_key(bsp_btn_t btn, bsp_btn_ev_t ev) {
    if (!s_assets || !s_img) return;
    if (!bsp_lvgl_lock(200)) return;
    switch (ev) {
        case BSP_BTN_CLICK:
            if (btn == BSP_BTN_OK)        play_click_reaction();
            else if (btn == BSP_BTN_UP)   step_anim(-1);
            else                          step_anim(+1);
            break;
        case BSP_BTN_DOUBLE:
            if (btn == BSP_BTN_OK)        pick_next();     // 双击 OK = 换一段
            break;
        case BSP_BTN_LONG:
            if (btn == BSP_BTN_UP)        move_to(s_pos_idx + 1);
            break;
        default:
            break;
    }
    bsp_lvgl_unlock();
}

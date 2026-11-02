// components/pet/include/pet_anim.h
// 大肥鱼桌宠的帧包访问层：把 PC 上 pack_pet.py 打好的 pet_assets.bin 从 Flash 分区
// 直接 mmap 进来，按名字找动作、逐帧解成 RGB565 给 LVGL 用。
//
// 为什么用 mmap 而不是读进 RAM：106 个动作约 5.9 MB，而 C3 只有 400 KB SRAM。
// 资源必须留在 Flash、靠 cache 直接寻址，运行时零拷贝。
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct pet_assets pet_assets_t;

typedef struct {
    const char   *name;     // UTF-8，指向 Flash 里的目录项，不占 RAM
    uint16_t      frames;   // 帧数
    uint8_t       fps;      // 打包时抽帧的帧率
    uint32_t      bytes;    // 该动作压缩后总字节（便于预算/统计）
} pet_anim_t;

// 打开并校验 Flash 里的 pet 分区（subtype 0x40，label "pet"）。可重复调用。
esp_err_t pet_assets_mount(void);

// 把任意一块已映射的内存当成帧包解析（开机画面走 boot 分区时用）。
// 内部只保留少量静态槽位，返回 NULL 表示不是合法帧包或槽位用尽。
const pet_assets_t *pet_assets_from_blob(const void *base, size_t size);

// 取出已挂载的资产；未挂载返回 NULL。
const pet_assets_t *pet_assets(void);

uint16_t pet_width(const pet_assets_t *a);
uint16_t pet_height(const pet_assets_t *a);

// 调色板：256 项 RGB565 小端，索引 0 是透明占位（解码时会被 transparent 参数覆盖）。
const uint16_t *pet_palette(const pet_assets_t *a);

int                pet_anim_count(const pet_assets_t *a);
const pet_anim_t  *pet_anim_at(const pet_assets_t *a, int index);
// 按名字（UTF-8）查动作，找不到返回 NULL。名字直接和 Flash 里的字节比，不做转码。
const pet_anim_t  *pet_anim_find(const pet_assets_t *a, const char *name);

// 解一帧到 dst：dst 是 w*h 个 RGB565，**不是**索引色。
// transparent 用来填充索引 0 的像素（一般填桌面的底色）。
// 返回的 dst 可直接喂 lv_img / lv_canvas。
esp_err_t pet_anim_decode(const pet_assets_t *a, const pet_anim_t *anim,
                          uint16_t frame, uint16_t transparent, uint16_t *dst);

// 单帧解包需要的最坏字节数（PackBits 对随机数据会膨胀 1/128），用于申缓冲。
size_t pet_frame_max_bytes(const pet_assets_t *a);

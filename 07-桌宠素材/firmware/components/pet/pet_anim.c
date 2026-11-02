// components/pet/pet_anim.c
#include "pet_anim.h"

#include <string.h>

#include "esp_flash.h"
#include "esp_log.h"
#include "esp_partition.h"

static const char *TAG = "pet";

#define PET_MAGIC0 'P'
#define PET_MAGIC1 'E'
#define PET_MAGIC2 'T'
#define PET_MAGIC3 '1'
#define PET_HEADER_LEN 11u          // magic4 + ver1 + pal2 + w2 + h2
#define PET_PAL_MAX 256u
#define PET_PART_SUBTYPE 0x40

typedef struct pet_assets {
    uint8_t         version;
    uint16_t        pal_count;
    uint16_t        w, h;
    const uint16_t *pal;
    uint16_t        anim_count;
    const uint8_t  *dir;        // 目录区起点
    const uint8_t  *base;       // mmap 起点：off 都是相对它的绝对偏移
    size_t          size;
} pet_assets_t_impl;

static pet_assets_t_impl s_assets;
static bool s_mounted;

// ---- 小端读：mmap 指针不能假设对齐，统一走 memcpy ----
static inline uint16_t rd16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static inline uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }

static const uint8_t *dir_of(const pet_assets_t_impl *a) { return a->dir; }

/** 把一块内存按 PET1 格式填进 out；不是合法帧包返回 false。 */
static bool parse_blob(const void *base, size_t size, pet_assets_t_impl *out) {
    const uint8_t *b = (const uint8_t *)base;
    if (!b || size < PET_HEADER_LEN + PET_PAL_MAX * 2 + 2) return false;
    if (b[0] != PET_MAGIC0 || b[1] != PET_MAGIC1 || b[2] != PET_MAGIC2 || b[3] != PET_MAGIC3)
        return false;

    pet_assets_t_impl a = { 0 };
    a.version    = b[4];
    a.pal_count  = rd16(b + 5);
    a.w          = rd16(b + 7);
    a.h          = rd16(b + 9);
    if (a.version != 1 || a.pal_count != PET_PAL_MAX || a.w == 0 || a.h == 0) return false;

    a.pal = (const uint16_t *)(b + PET_HEADER_LEN);
    a.anim_count = rd16(b + PET_HEADER_LEN + PET_PAL_MAX * 2);
    a.dir  = b + PET_HEADER_LEN + PET_PAL_MAX * 2 + 2;
    a.base = b;
    a.size = size;
    *out = a;
    return true;
}

// pet_assets_from_blob 用的静态槽（pet 分区固定走 s_assets，不占这里）
static pet_assets_t_impl s_extra[2];
static int s_extra_n;

const pet_assets_t *pet_assets_from_blob(const void *base, size_t size) {
    if (s_extra_n >= (int)(sizeof(s_extra) / sizeof(s_extra[0]))) return NULL;
    if (!parse_blob(base, size, &s_extra[s_extra_n])) return NULL;
    return (const pet_assets_t *)&s_extra[s_extra_n++];
}

esp_err_t pet_assets_mount(void) {
    if (s_mounted) return ESP_OK;

    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, PET_PART_SUBTYPE, "pet");
    if (!part) {
        ESP_LOGE(TAG, "找不到 pet 分区（subtype 0x40）。检查 partitions.csv 与烧录。");
        return ESP_ERR_NOT_FOUND;
    }

    const void *map = NULL;
    esp_partition_mmap_handle_t handle;
    esp_err_t err = esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA,
                                       &map, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mmap 失败: %s", esp_err_to_name(err));
        return err;
    }

    if (!parse_blob(map, part->size, &s_assets)) {
        ESP_LOGE(TAG, "pet 分区里不是帧包（magic/版本不对），先跑 pack_pet.py 并重新烧录");
        return ESP_ERR_INVALID_RESPONSE;
    }

    s_mounted = true;
    ESP_LOGI(TAG, "帧包已挂载: %ux%u, %u 个动作, 分区 %u KB",
             s_assets.w, s_assets.h, s_assets.anim_count, (unsigned)(part->size / 1024));
    return ESP_OK;
}

const pet_assets_t *pet_assets(void) { return s_mounted ? (const pet_assets_t *)&s_assets : NULL; }

uint16_t pet_width(const pet_assets_t *a) { return a ? ((const pet_assets_t_impl *)a)->w : 0; }
uint16_t pet_height(const pet_assets_t *a) { return a ? ((const pet_assets_t_impl *)a)->h : 0; }
const uint16_t *pet_palette(const pet_assets_t *a) {
    return a ? ((const pet_assets_t_impl *)a)->pal : NULL;
}
int pet_anim_count(const pet_assets_t *a) {
    return a ? (int)((const pet_assets_t_impl *)a)->anim_count : 0;
}

size_t pet_frame_max_bytes(const pet_assets_t *a) {
    // 最坏情况：全字面量，128 字节数据带 1 字节头 → 1.008 倍，留足余量
    const pet_assets_t_impl *i = (const pet_assets_t_impl *)a;
    if (!i) return 0;
    return (size_t)i->w * i->h + (size_t)i->w * i->h / 64 + 64;
}

// 走一遍目录，返回第 index 项并回填它的目录项位置（供 find 复用）
static const uint8_t *anim_dir(const pet_assets_t_impl *a, int index,
                               const char **name, uint16_t *frames, uint8_t *fps) {
    const uint8_t *p = dir_of(a);
    for (int i = 0; i <= index; i++) {
        if (i == index) {
            uint8_t nl = *p;
            if (name)   *name = (const char *)(p + 1);
            if (frames) *frames = rd16(p + 1 + nl);
            if (fps)    *fps = (uint8_t)rd16(p + 1 + nl + 4);
            return p;
        }
        uint8_t nl = *p;
        uint16_t fc = rd16(p + 1 + nl);
        p += 1 + nl + 5 + 4u * fc;
    }
    return NULL;
}

const pet_anim_t *pet_anim_at(const pet_assets_t *a, int index) {
    const pet_assets_t_impl *i = (const pet_assets_t_impl *)a;
    static pet_anim_t out;      // 单实例桌宠，静态返回足够
    if (!i || index < 0 || index >= (int)i->anim_count) return NULL;
    const char *nm = NULL;
    uint16_t fc = 0;
    uint8_t fps = 0;
    anim_dir(i, index, &nm, &fc, &fps);
    out.name = nm;
    out.frames = fc;
    out.fps = fps;
    out.bytes = 0;
    return &out;
}

const pet_anim_t *pet_anim_find(const pet_assets_t *a, const char *name) {
    const pet_assets_t_impl *i = (const pet_assets_t_impl *)a;
    if (!i || !name) return NULL;
    const uint8_t *p = dir_of(i);
    for (uint16_t k = 0; k < i->anim_count; k++) {
        uint8_t nl = *p;
        if (strlen(name) == nl && memcmp(p + 1, name, nl) == 0) {
            return pet_anim_at(a, (int)k);
        }
        uint16_t fc = rd16(p + 1 + nl);
        p += 1 + nl + 5 + 4u * fc;
    }
    return NULL;
}

// 找 name 对应的目录项，拿它第 frame 帧的绝对偏移
static const uint8_t *frame_ptr(const pet_assets_t_impl *a, const char *name, uint16_t frame) {
    const uint8_t *p = dir_of(a);
    for (uint16_t k = 0; k < a->anim_count; k++) {
        uint8_t nl = *p;
        uint16_t fc = rd16(p + 1 + nl);
        if (strlen(name) == nl && memcmp(p + 1, name, nl) == 0) {
            if (frame >= fc) return NULL;
            uint32_t off = rd32(p + 1 + nl + 5 + 4u * frame);
            if (off + 2 > a->size) return NULL;
            return a->base + off;
        }
        p += 1 + nl + 5 + 4u * fc;
    }
    return NULL;
}

esp_err_t pet_anim_decode(const pet_assets_t *a, const pet_anim_t *anim,
                          uint16_t frame, uint16_t transparent, uint16_t *dst) {
    const pet_assets_t_impl *i = (const pet_assets_t_impl *)a;
    if (!i || !anim || !dst) return ESP_ERR_INVALID_ARG;
    const uint8_t *src = frame_ptr(i, anim->name, frame);
    if (!src) return ESP_ERR_INVALID_ARG;

    const uint16_t len = rd16(src);
    const uint8_t *p = src + 2;
    const uint8_t *end = p + len;
    const uint32_t total = (uint32_t)i->w * i->h;
    const uint16_t *pal = i->pal;
    uint32_t n = 0;

    while (n < total && p < end) {
        const uint8_t c = *p++;
        if (c < 0x80) {                       // 重复 c+1 次
            if (p >= end) break;
            const uint8_t v = *p++;
            const uint16_t px = v ? pal[v] : transparent;
            uint32_t cnt = (uint32_t)c + 1u;
            while (cnt-- && n < total) dst[n++] = px;
        } else {                              // 字面量 (c&0x7F)+1 字节
            uint32_t cnt = (uint32_t)(c & 0x7F) + 1u;
            while (cnt-- && n < total && p < end) {
                const uint8_t v = *p++;
                dst[n++] = v ? pal[v] : transparent;
            }
        }
    }
    while (n < total) dst[n++] = transparent;   // 数据不足时补底，不花屏
    return ESP_OK;
}

// dump_frame.c —— 主机侧跑**真的** pet_anim.c，把某一帧解成原始 RGB565 字节存盘
//
// 不是"照抄一遍算法"：这里直接 #include 固件里那份 pet_anim.c，编的就是设备上跑的代码。
//
// 两种跑法（关键）：
//   as-is   ：完全按固件调用方式 pet_anim_decode(a, pet_anim_find(a,name), ...)
//             动作名指向 Flash 目录项（**不带 NUL 结尾**），测的是真机行为
//   safe    ：把动作名复制成带 NUL 的 C 字符串再解，测的是"解码算法本身"
//   两者差值 = pet_anim 里 name 生命周期的问题，不是 PackBits 的问题。
//
// 用法：dump_frame <bin> <动作名|#序号> <帧号> <transparent hex> <输出前缀>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef PET_ANIM_C
#include PET_ANIM_C
#else
#include "../../firmware/components/pet/pet_anim.c"
#endif

// 按字节把目录里的名字抄成 NUL 结尾的 C 字符串
static const char *terminated_name(const pet_assets_t *a, const pet_anim_t *want, char *buf, size_t cap)
{
    const pet_assets_t_impl *i = (const pet_assets_t_impl *)a;
    const uint8_t *p = i->dir;
    for (uint16_t k = 0; k < i->anim_count; k++) {
        const uint8_t nl = *p;
        const uint16_t fc = rd16(p + 1 + nl);
        if (fc == want->frames && memcmp(p + 1, want->name, nl) == 0) {
            if ((size_t)nl + 1u > cap) return NULL;
            memcpy(buf, p + 1, nl);
            buf[nl] = '\0';
            return buf;
        }
        p += 1u + nl + 5u + 4u * fc;
    }
    return NULL;
}

int main(int argc, char **argv)
{
    if (argc != 6) {
        fprintf(stderr, "用法: %s <bin> <动作名|#序号> <帧号> <transparent hex> <输出前缀>\n", argv[0]);
        return 2;
    }
    const char *bin_path = argv[1];
    const char *which    = argv[2];
    const int   frame    = atoi(argv[3]);
    const unsigned transp = (unsigned)strtoul(argv[4], NULL, 16);
    const char *prefix   = argv[5];

    FILE *f = fopen(bin_path, "rb");
    if (!f) { perror("open bin"); return 3; }
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    unsigned char *blob = aligned_alloc(4096, ((size_t)sz + 4095u) & ~4095u);
    if (!blob) { fprintf(stderr, "oom\n"); return 3; }
    if (fread(blob, 1, (size_t)sz, f) != (size_t)sz) { fprintf(stderr, "short read\n"); return 3; }
    fclose(f);

    const pet_assets_t *a = pet_assets_from_blob(blob, (size_t)sz);
    if (!a) { fprintf(stderr, "不是合法帧包\n"); return 4; }

    const pet_anim_t *found = (which[0] == '#') ? pet_anim_at(a, atoi(which + 1))
                                                : pet_anim_find(a, which);
    if (!found) { fprintf(stderr, "找不到动作 %s\n", which); return 5; }
    if (frame < 0 || frame >= found->frames) { fprintf(stderr, "帧号越界(共 %u 帧)\n", found->frames); return 5; }

    const uint16_t w = pet_width(a), h = pet_height(a);
    uint16_t *dst = malloc((size_t)w * h * sizeof(uint16_t));
    if (!dst) { fprintf(stderr, "oom dst\n"); return 3; }

    char nbuf[128];
    const char *nt = terminated_name(a, found, nbuf, sizeof(nbuf));
    if (!nt) { fprintf(stderr, "目录项抄名失败\n"); return 5; }

    char path[512];

    // ---- A) 真机路径：name 直接指 Flash 目录项（无 NUL 结尾） ----
    memset(dst, 0xEE, (size_t)w * h * sizeof(uint16_t));
    const esp_err_t err_asis = pet_anim_decode(a, found, (uint16_t)frame, (uint16_t)transp, dst);
    snprintf(path, sizeof(path), "%s_asis.rgb565", prefix);
    FILE *o = fopen(path, "wb");
    fwrite(dst, sizeof(uint16_t), (size_t)w * h, o);
    fclose(o);

    // ---- B) 算法路径：name 是正常 NUL 结尾的 C 字符串 ----
    pet_anim_t fixed = *found;
    fixed.name = nt;
    memset(dst, 0xEE, (size_t)w * h * sizeof(uint16_t));
    const esp_err_t err_safe = pet_anim_decode(a, &fixed, (uint16_t)frame, (uint16_t)transp, dst);
    snprintf(path, sizeof(path), "%s_safe.rgb565", prefix);
    o = fopen(path, "wb");
    fwrite(dst, sizeof(uint16_t), (size_t)w * h, o);
    fclose(o);

    printf("动作=\"%s\"(len=%zu，目录项里无 NUL) 帧=%d/%u 精灵=%ux%u 透明=0x%04X\n",
           nt, strlen(nt), frame, found->frames, w, h, transp);
    printf("  as-is decode → %d   safe decode → %d\n", err_asis, err_safe);
    printf("  safe 前 8 像素: %04X %04X %04X %04X %04X %04X %04X %04X\n",
           dst[0], dst[1], dst[2], dst[3], dst[4], dst[5], dst[6], dst[7]);
    printf("  输出: %s_asis.rgb565 / %s_safe.rgb565\n", prefix, prefix);

    free(dst);
    free(blob);
    return (err_safe == ESP_OK) ? 0 : 6;
}

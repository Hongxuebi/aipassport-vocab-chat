// main/demo_wordcard.c —— 单词卡（竖屏 240×320）
//
// 放到 FoloToy ai-passport 仓库的 main/ 下，然后三处接线：
//   ① main/demo.h 里加三行声明
//   ② main/CMakeLists.txt 的 SRCS 里加 demo_wordcard.c
//   ③ main/demo.c 的 DEMOS[] 里加一行 { "WORD CARD", ... }
//
// 设计约束（都来自官方 AGENTS.md / 硬件指南，别违反）：
//   · LVGL 不是线程安全的：非 LVGL 任务访问对象必须 bsp_lvgl_lock()
//   · 按键回调必须非阻塞：这里只做存值与切卡，不发网络请求
//   · 本机 ESP32-C3 无 PSRAM，LVGL 池只有 24KB —— 不要建大图、不要双缓冲
//   · 中文字形必须来自自建 CJK 子集字库，Montserrat 没有中文（会显示方块）
//
// 因为 key 回调跑在 esp_timer 任务里，而 LVGL 操作要在锁内，
// 所以这里用最简单可靠的做法：回调里判断 -> 拿锁 -> 改 UI -> 放锁。

#include "demo.h"
#include "bsp_button.h"
#include "bsp_display.h"
#include "ui_pixel.h"
#include "lvgl.h"
#include <stdio.h>
#include <string.h>

// ---- 自建中文字库（生成方法见 04-AI Passport 竖屏版式与开发上手.md §5）----
// 没有就先注释掉，改用 lv_font_montserrat_20 看英文，验证完再接中文
LV_FONT_DECLARE(font_sc16);
LV_FONT_DECLARE(font_sc22);

// ---- 词库：第一版先硬编码，跑通后再改成从 flash 读文件/联网拉 ----
typedef struct {
    const char *w;      // 单词
    const char *p;      // 音标（拉丁字符，Montserrat 能显示，但用中文字库更统一）
    const char *pos;    // 词性
    const char *cn;     // 中文释义
    const char *ex;     // 例句
    const char *excn;   // 例句译文
    const char *tip;    // 提示
} card_t;

static const card_t CARDS[] = {
    { "dictionary", "/ˈdɪkʃənri/", "n.", "字典；词典",
      "Look it up in the dictionary.", "在字典里查一下。", "拼写长，分三段记" },
    { "dangerous", "/ˈdeɪndʒərəs/", "adj.", "危险的",
      "It is dangerous to swim here.", "在这里游泳很危险。", "名词 danger + ous" },
    { "favourite", "/ˈfeɪvərɪt/", "adj.", "最喜欢的",
      "Blue is my favourite colour.", "蓝色是我最喜欢的颜色。", "英式拼写，美式 favorite" },
    { "borrow", "/ˈbɒrəʊ/", "v.", "借入",
      "May I borrow your pen?", "我可以借你的笔吗？", "反义 lend 借出" },
};
#define CARD_N ((int)(sizeof(CARDS) / sizeof(CARDS[0])))

// ---- 版面坐标（240×320，竖屏）----
#define M            12          // 左右边距
#define Y_UNIT       56          // 单元 chip 顶
#define Y_WORD       100         // 单词顶
#define H_WORD       56
#define Y_PHONE      164         // 音标顶
#define Y_LINE       200         // 分隔线
#define Y_DEF        208         // 释义顶
#define Y_EX         268         // 例句顶
#define Y_EXCN       292         // 译文顶
#define Y_HINT       286         // 正面提示
#define Y_BAR        300         // 进度条

// ---- 状态 ----
static lv_obj_t *s_scr, *s_unit, *s_idx, *s_word, *s_phone, *s_line;
static lv_obj_t *s_def, *s_ex, *s_excn, *s_tip, *s_hint, *s_bar;
static int  s_cur   = 0;
static bool s_flip  = false;
static uint8_t s_grade[CARD_N];   // 0=不认识 1=模糊 2=认识 0xFF=未答
static int  s_done  = 0;

// 只建一次，之后靠显隐切换 —— 比每次删了重建省内存、也不闪
static lv_obj_t *mk_label(lv_obj_t *parent, const lv_font_t *font, uint32_t color) {
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    lv_label_set_text(l, "");
    return l;
}

static void render(void) {
    const card_t *c = &CARDS[s_cur];
    char buf[32];

    lv_label_set_text(s_unit, "Unit 4");
    snprintf(buf, sizeof(buf), "%d/%d", s_cur + 1, CARD_N);
    lv_label_set_text(s_idx, buf);
    lv_label_set_text(s_word, c->w);
    lv_label_set_text(s_phone, c->p);

    if (!s_flip) {
        // 正面：只露词和音标
        lv_obj_add_flag(s_def,   LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_ex,    LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_excn,  LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_tip,   LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_line, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_hint, LV_OBJ_FLAG_HIDDEN);
        lv_bar_set_value(s_bar, s_cur, LV_ANIM_OFF);
    } else {
        // 背面：露释义、例句、提示
        lv_obj_clear_flag(s_def,  LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_ex,   LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_excn, LV_OBJ_FLAG_HIDDEN);
        if (c->tip && c->tip[0]) lv_obj_clear_flag(s_tip, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(s_line, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(s_hint, LV_OBJ_FLAG_HIDDEN);

        snprintf(buf, sizeof(buf), "%s %s", c->pos, c->cn);
        lv_label_set_text(s_def, buf);
        lv_label_set_text(s_ex, c->ex);
        lv_label_set_text(s_excn, c->excn);
        if (c->tip) {
            snprintf(buf, sizeof(buf), "提示：%s", c->tip);
            lv_label_set_text(s_tip, buf);
        }
        lv_bar_set_value(s_bar, s_cur + 1, LV_ANIM_OFF);
    }
}

static void grade(int g) {
    s_grade[s_cur] = (uint8_t)g;
    s_done++;
    // 第一版：只在本页内存里记账；接后端时改成往 NVS/队列里写，
    // 由 worker 任务异步 POST，绝不能在这里发网络请求（会卡按键任务）
    s_cur = (s_cur + 1) % CARD_N;
    s_flip = false;
    render();
}

void demo_wordcard_enter(void) {
    s_scr = ui_pixel_screen_create("WORD CARD");

    s_unit  = mk_label(s_scr, &font_sc16, UI_SKY_DARK);
    lv_obj_set_pos(s_unit, M, Y_UNIT);

    s_idx   = mk_label(s_scr, &font_sc16, 0x6B757D);
    lv_obj_align(s_idx, LV_ALIGN_TOP_RIGHT, -M, Y_UNIT);

    s_word  = mk_label(s_scr, &font_sc22, UI_INK);
    lv_obj_set_width(s_word, 240 - 2 * M);
    lv_obj_set_pos(s_word, M, Y_WORD);
    lv_obj_set_style_text_align(s_word, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(s_word, LV_LABEL_LONG_MODE_WRAP);   // 长词自动换行

    s_phone = mk_label(s_scr, &font_sc16, UI_SKY_DARK);
    lv_obj_set_width(s_phone, 240 - 2 * M);
    lv_obj_set_pos(s_phone, M, Y_PHONE);
    lv_obj_set_style_text_align(s_phone, LV_TEXT_ALIGN_CENTER, 0);

    s_line  = lv_obj_create(s_scr);
    lv_obj_remove_style_all(s_line);
    lv_obj_set_size(s_line, 240 - 2 * M, 2);
    lv_obj_set_pos(s_line, M, Y_LINE);
    lv_obj_set_style_bg_color(s_line, lv_color_hex(UI_MUTED), 0);
    lv_obj_set_style_bg_opa(s_line, LV_OPA_COVER, 0);

    s_def   = mk_label(s_scr, &font_sc22, UI_INK);
    lv_obj_set_width(s_def, 240 - 2 * M);
    lv_obj_set_pos(s_def, M, Y_DEF);
    lv_label_set_long_mode(s_def, LV_LABEL_LONG_MODE_WRAP);

    s_ex    = mk_label(s_scr, &font_sc16, UI_INK);
    lv_obj_set_width(s_ex, 240 - 2 * M);
    lv_obj_set_pos(s_ex, M, Y_EX);
    lv_label_set_long_mode(s_ex, LV_LABEL_LONG_MODE_WRAP);

    s_excn  = mk_label(s_scr, &font_sc16, 0x6B757D);
    lv_obj_set_width(s_excn, 240 - 2 * M);
    lv_obj_set_pos(s_excn, M, Y_EXCN);

    s_tip   = mk_label(s_scr, &font_sc16, UI_SKY_DARK);
    lv_obj_set_width(s_tip, 240 - 2 * M);
    lv_obj_set_pos(s_tip, M, 306);

    s_hint  = mk_label(s_scr, &font_sc16, 0x6B757D);
    lv_obj_set_width(s_hint, 240 - 2 * M);
    lv_obj_set_pos(s_hint, M, Y_HINT);
    lv_obj_set_style_text_align(s_hint, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(s_hint, "● 想好再按中间键看答案");

    s_bar = lv_bar_create(s_scr);
    lv_obj_set_size(s_bar, 240 - 2 * M, 6);
    lv_obj_set_pos(s_bar, M, Y_BAR);
    lv_bar_set_range(s_bar, 0, CARD_N);
    lv_obj_set_style_bg_color(s_bar, lv_color_hex(UI_MUTED), LV_PART_MAIN);
    lv_obj_set_style_bg_color(s_bar, lv_color_hex(UI_SKY), LV_PART_INDICATOR);
    lv_obj_set_style_radius(s_bar, 3, LV_PART_MAIN);
    lv_obj_set_style_radius(s_bar, 3, LV_PART_INDICATOR);

    memset(s_grade, 0xFF, sizeof(s_grade));
    s_cur = 0;
    s_flip = false;
    s_done = 0;
    render();

    lv_screen_load(s_scr);
}

void demo_wordcard_exit(void) {
    // 本页没有 worker / timer，直接删屏即可。
    // 一旦你加了网络或音频 worker，必须先有 stop 钩子并等它退干净再删。
    if (s_scr) {
        lv_obj_delete(s_scr);
        s_scr = s_unit = s_idx = s_word = s_phone = s_line = NULL;
        s_def = s_ex = s_excn = s_tip = s_hint = s_bar = NULL;
    }
}

void demo_wordcard_key(bsp_btn_t btn, bsp_btn_ev_t ev) {
    // 只认单击。长按 OK 留给框架做"返回"，别抢。
    if (ev != BSP_BTN_EV_CLICK && ev != BSP_BTN_EV_SHORT) return;   // 事件名按你仓库里的枚举改
    if (!bsp_lvgl_lock(200)) return;

    switch (btn) {
    case BSP_BTN_UP:
        grade(0);            // 不认识
        break;
    case BSP_BTN_DOWN:
        grade(2);            // 认识
        break;
    case BSP_BTN_OK:
        s_flip = !s_flip;    // 翻面
        render();
        break;
    default:
        break;
    }
    bsp_lvgl_unlock();
}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预览日记.py —— 把「她的日记」页渲成 240×320 的图（离线、零真机）

和固件同一套：读 `main/fonts/fusion_ui16.c` 的位图 + `demo_diary.c` 里的坐标常量。
不是照片：圆角/描边是近似的。

用法：
  python3 _工具/预览日记.py                     # 用内置样例
  python3 _工具/预览日记.py "10-30" "正文……"     # 指定日期与正文
"""
import importlib.util
import os
import sys

ROOT = os.path.expanduser("~/dev/ai-passport")
TOOLS = os.path.join(ROOT, "_工具")
OUT = os.path.join(ROOT, "图/日记预览.png")

# ---- 和 demo_diary.c / ui_pixel.c 对齐的坐标 ----
CARD_X, CARD_Y, CARD_W, CARD_H = 2, 42, 236, 240
BODY_X, BODY_Y, BODY_W = 6, 52, 224
LINES, CPL, LINE_H = 8, 13, 18
PN_X, PN_Y = 186, 206 + CARD_Y - CARD_Y      # 相对卡片（和固件一致：206 是卡内坐标）
HINT_X, HINT_Y = 6, 220
DATE_X, DATE_Y, DATE_W = 164, 13, 68
PLATE = (0x08, 0x14, 0x1C)
EDGE = (0x1E, 0x3A, 0x47)
BG = (0x05, 0x09, 0x0F)
INK = (0xE6, 0xF9, 0xFF)
DIM = (0x3F, 0xA8, 0xC4)
MUTE = (0x8F, 0xA4, 0xB8)
CY = (0x7F, 0xE7, 0xFF)

TAIL = "，。！？、；：）】》」』…"

sys.path.insert(0, TOOLS)
_spec = importlib.util.spec_from_file_location("yulan", os.path.join(TOOLS, "预览版面.py"))
yulan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yulan)


def wrap(text, lines=LINES, cpl=CPL):
    """和固件 wrap_screen() 同规则：每行 cpl 字，行首若是句读就挤到上一行（最多 cpl+1）。"""
    out, cur = [], ""
    for ch in text:
        if len(cur) >= cpl:
            if ch in TAIL and len(cur) < cpl + 1:
                cur += ch
                continue
            if len(out) >= lines - 1:
                break
            out.append(cur)
            cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out[:lines]


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "10-30"
    body = sys.argv[2] if len(sys.argv) > 2 else (
        "屏幕光晃得我眼酸。单词念到第三遍就轻了，我数着数着就记下了。"
        "声音越来越小，到第三遍「abandon」就没声了。")

    f16 = yulan.Font("fusion_ui16.c")
    f12 = yulan.Font("fusion_ui12.c")
    cv = yulan.Canvas(240, 320, bg=BG)

    # 顶栏标题牌（ui_pixel_screen_create2）
    cv.rect(10, 7, 151, 28, PLATE)
    cv.hline(10, 160, 7, EDGE)
    cv.hline(10, 160, 34, EDGE)
    for y in range(7, 35):
        cv.px[y][10] = EDGE
        cv.px[y][160] = EDGE
    cv.draw_text(19, 10, "她的日记", f16, CY)
    cv.draw_text(DATE_X + DATE_W - f12.text_width(date), DATE_Y, date, f12, DIM)

    # 卡片
    cv.rect(CARD_X, CARD_Y, CARD_W, CARD_H, PLATE)
    cv.hline(CARD_X, CARD_X + CARD_W - 1, CARD_Y, EDGE)
    cv.hline(CARD_X, CARD_X + CARD_W - 1, CARD_Y + CARD_H - 1, EDGE)
    for y in range(CARD_Y, CARD_Y + CARD_H):
        cv.px[y][CARD_X] = EDGE
        cv.px[y][CARD_X + CARD_W - 1] = EDGE

    for i, line in enumerate(wrap(body)):
        cv.draw_text(BODY_X, BODY_Y + i * LINE_H, line, f16, INK)
    cv.draw_text(HINT_X, CARD_Y + HINT_Y, "▲▼ 翻页   ● 返回", f12, MUTE)
    cv.draw_text(PN_X + 44 - f12.text_width("1/2"), CARD_Y + PN_Y, "1/2", f12, DIM)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    yulan.write_png(OUT, cv)
    print("画好了：%s" % OUT)
    print("正文 %d 字 → %d 行" % (len(body), len(wrap(body))))
    miss = [c for c in body if not f16.glyph(ord(c))]
    print("缺字：%s" % ("无" if not miss else "".join(miss)))


if __name__ == "__main__":
    main()

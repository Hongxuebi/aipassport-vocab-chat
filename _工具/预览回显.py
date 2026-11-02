#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预览回显.py —— 把「桌宠页 + 她说的气泡 + 你说的那行」渲染成 240×320 的图（离线、零真机）。

为什么要有它（2026-10-29 v0.92）：
  屏上回显靠"刷到真机再看"，改一个折行位置就要刷一次机（两分钟）；而且我在命令行里
  **看不见屏幕**，只能靠日志判断"设进去了没有"，判不了"看起来对不对"。
  这里直接读**固件里同一份字库位图**（fusion_ui16.c）和**同一份立绘**（faces.bin），
  按固件里的坐标画出来 —— 像素级接近真机，但**不是照片**（LVGL 的圆角、阴影这类没有）。

用法：
  python3 _工具/预览回显.py                                   # 默认两句
  python3 _工具/预览回显.py "你好呀" "总算来了呢，我还以为你把我忘了"
  python3 _工具/预览回显.py --check                           # 只验折行规则（闸里跑，不画图）
"""
import importlib.util
import os
import struct
import sys

ROOT = os.path.expanduser("~/dev/ai-passport")
TOOLS = os.path.join(ROOT, "_工具")
FACES = os.path.join(ROOT, "_固件备份/v0.90-m2/faces.bin")      # 37 张（pet 分区那一份）
OUT = os.path.join(ROOT, "图/气泡预览.png")

FACE_W, FACE_H = 140, 130
FACE_X, FACE_Y = (240 - FACE_W) // 2, 52                        # 与 demo_pet.c 的 FACE_* 一致
LVL_X, LVL_Y, LVL_W = 164, 13, 68                               # 关系账读数：顶栏右侧（v0.95 挪上来的）
ECHO_X, ECHO_Y = 8, 250                                         # 我说的（v0.95：250..304）
BUB_X, BUB_Y, BUB_W, BUB_H = 4, 184, 232, 62                    # 气泡底板（v0.95：3 行）
BUB_TX, BUB_TY = 6, 4                                           # 气泡里的字（相对底板）
TEXT_W, TEXT_H = 224, 3 * 18                                    # 两块标签的固定尺寸（3 行）
LVL_TEXT = "L1 · 26"    # 调试口子：和 lvl_paint_locked() 同格式（12px 字库，有「·」）
PET_TITLE = "大肥鱼"                                      # 顶栏标题（和 demo_pet.c 的 PET_TITLE 一致）
BG = (0x05, 0x09, 0x0F)
ECHO_INK = (0x7C, 0x93, 0xA8)
HER_INK = (0xE6, 0xF9, 0xFF)
LVL_INK = (0x5C, 0x7F, 0xA6)
BUB_BG = (0x0E, 0x1A, 0x24)
BUB_EDGE = (0x1E, 0x3A, 0x4A)
PLATE = (0x08, 0x14, 0x1C)                                      # 标题牌 UI_PANEL
PLATE_EDGE = (0x1E, 0x3A, 0x4A)                                 # 标题牌描边 UI_PANEL_EDGE

sys.path.insert(0, TOOLS)
_spec = importlib.util.spec_from_file_location("yulan", os.path.join(TOOLS, "预览版面.py"))
yulan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yulan)          # 它有自己的 __main__ 守卫，导入不会渲图


def faces_list():
    """读 FAC1：头 16 + 目录 40×n + 像素；返回 [(名字, bytes)]。"""
    blob = open(FACES, "rb").read()
    assert blob[:4] == b"FAC1", "不是 FAC1 表情包"
    count = blob[5]
    w, h = struct.unpack("<HH", blob[6:10])
    dir_entry = struct.unpack("<H", blob[12:14])[0]
    assert (w, h) == (FACE_W, FACE_H), f"尺寸不对 {w}×{h}"
    out = []
    for i in range(count):
        e = 16 + i * dir_entry
        name = blob[e:e + 28].split(b"\0")[0].decode("utf-8", "replace")
        off, ln = struct.unpack("<II", blob[e + 28:e + 36])
        out.append((name, blob[off:off + ln]))
    return out


def blit_face(cv, pixels):
    """RGB565 小端 → 画到画布（和固件里 LV_COLOR_FORMAT_RGB565 同一条换算）。"""
    for yy in range(FACE_H):
        for xx in range(FACE_W):
            i = (yy * FACE_W + xx) * 2
            v = pixels[i] | (pixels[i + 1] << 8)
            r = (v >> 11) & 0x1F
            g = (v >> 5) & 0x3F
            b = v & 0x1F
            cv.px[FACE_Y + yy][FACE_X + xx] = ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


TAIL_PUNCT = "，。！？、；：）】》」』…"


WRAP_W = 224          # 一行硬上限（px）—— 固件 TEXT_W，不是"14 个字"
ELLIPSIS = "…"


def _adv(f16, ch):
    """和固件 `lv_font_get_glyph_width` 同一条换算：(adv_w + 8) >> 4（四舍五入到 px）。"""
    g = f16.glyph(ord(ch))
    return ((g["adv"] + 8) >> 4) if g else (f16.line_height // 2)


def wrap_echo(text, prefix="你：", lines=3, font=None):
    """和固件 `wrap_lines()` 同一套规则（v0.109 **按像素**折，不按字数）：
       ① 行宽按 px（224），字宽取字模 adv_w（比例字：i=5px / a=8px / W=13px / 汉字=16px）
       ② 英文优先在空格后折，不拆单词（单个词比一行还宽才硬拆）
       ③ 中文任意折 + 避头尾（行首不留 ，。！？、；：）」』…）
       ④ 自然行数 N≤3 时按 ceil(总宽/N) 重排均分；>3 行则按 total/3 重排、末行补「…」
    返回：行列表（每行是字符串）。"""
    f16 = font or yulan.Font("fusion_ui16.c")
    s = prefix + text
    gl = [(_adv(f16, ch), ch) for ch in s]
    total = sum(w for w, _ in gl)
    n = len(gl)

    def run(target, cap):
        spans, i = [], 0
        while i < n and len(spans) < cap:
            start, width, last_space = i, 0, -1
            while i < n and width + gl[i][0] <= target:
                width += gl[i][0]
                if gl[i][1] == " ":
                    last_space = i
                i += 1
            if i == start:
                width += gl[i][0]
                i += 1
            if i < n:
                brk = last_space + 1 if last_space >= start else i
                if brk < n and gl[brk][1] in TAIL_PUNCT and brk > start + 1 and width + gl[brk][0] <= target + 16:
                    brk += 1
                while brk < n and gl[brk][1] == " ":
                    brk += 1
                brk = max(brk, start + 1)
                i = brk
            spans.append((start, i))
        return spans

    def _cjk_ratio(t):
        cjk = all_ = 0
        for ch in t:
            if ch in " \t":
                continue
            all_ += 1
            if "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef":
                cjk += 1
        return cjk * 100 // all_ if all_ else 0

    spans = run(WRAP_W, lines + 1)
    mostly_cjk = _cjk_ratio(s) >= 30
    if len(spans) > 1 and (len(spans) > lines or mostly_cjk):
        want = lines if len(spans) > lines else len(spans)
        base = (total + want - 1) // want
        for delta in (0, 8, 16, 24, 32):
            target = min(WRAP_W, base + delta)
            s2 = run(target, lines + 1)
            if len(s2) <= want:
                spans = s2
                break

    spans = spans[:lines]
    cut = spans and spans[-1][1] < n
    ell_w = _adv(f16, ELLIPSIS)
    out = []
    for k, (a, b) in enumerate(spans):
        line = "".join(ch for _, ch in gl[a:b])
        if cut and k == len(spans) - 1:
            while len(line) > 1 and sum(_adv(f16, c) for c in line) + ell_w > WRAP_W:
                line = line[:-1]
            line += ELLIPSIS
        out.append(line)
    return out


CHECK_CASES = [
    ("你好呀", "总算来了呢，我还以为你把我忘了"),
    ("我觉得这个单词好像在哪里见过", "哼，总算来了。今天记得挺快。"),
    ("", "今天的番茄你做了三个啦，比昨天多一个，要不要歇一会儿再背单词"),
    ("abcdefghijklmnop", "嗯嗯"),
    ("", "好。"),
    # ↓ 2026-10-31 实测报的那条：英文回复只在左半块、还被截短
    ("", "I had a long day today. You should rest and drink some water."),
    ("", "Let's rest together. 说 rest together 更顺。"),
    ("I have a long day today.", "Long day, huh. You should rest."),
    ("", "Yeah. You rest, I'll keep the rice warm."),
]


def _word_at_break(l1, l2):
    """两行交界处是不是把英文单词劈开了？返回那个词（没劈开返回空串）。"""
    if not l1 or not l2:
        return ""
    if not (l1[-1].isascii() and l1[-1].isalnum() and l2[0].isascii() and l2[0].isalnum()):
        return ""
    joined = l1 + l2
    i = len(l1)
    a = i
    while a > 0 and joined[a - 1].isascii() and joined[a - 1].isalnum():
        a -= 1
    b = i
    while b < len(joined) and joined[b].isascii() and joined[b].isalnum():
        b += 1
    return joined[a:b]


def check():
    """只验折行规则，不画图（闸里跑，**不刷机就能挡回退步**）。
    规则和固件 `wrap_lines()`（v0.109 按像素）一致：
      ① 每行像素宽 ≤ 224（不是"≤14 个字"—— 英文一个 e 只 8px）
      ② 行首不许是句读/收尾标点、不许是空格
      ③ 不许把英文单词劈到两行（除非那个词本身就比一行还宽）
      ④ 被截断时末行以「…」结尾，且 ≤3 行
      ⑤ 没截断时内容必须完整（去掉前缀后与原文一致）"""
    f16 = yulan.Font("fusion_ui16.c")
    bad = 0
    for his, hers in CHECK_CASES:
        for who, text in (("你", his), ("她", hers)):
            if not text:
                continue
            prefix = who + "："
            lines = wrap_echo(text, prefix, font=f16)
            name = "%s「%s」" % (who, text[:16] + ("…" if len(text) > 16 else ""))
            if not lines:
                print("  ❌ %s 一行都没出" % name); bad += 1; continue
            for i, line in enumerate(lines, 1):
                w = sum(_adv(f16, c) for c in line)
                if w > WRAP_W:
                    print("  ❌ %s 第%d行 %dpx（>%d，会被裁）" % (name, i, w, WRAP_W)); bad += 1
                if line and line[0] in TAIL_PUNCT:
                    print("  ❌ %s 第%d行以标点「%s」开头（避头尾失效）" % (name, i, line[0])); bad += 1
                if line.startswith(" "):
                    print("  ❌ %s 第%d行以空格开头" % (name, i)); bad += 1
            for i in range(len(lines) - 1):
                word = _word_at_break(lines[i], lines[i + 1])
                if word and sum(_adv(f16, c) for c in word) <= WRAP_W:
                    print("  ❌ %s 把英文词「%s」劈到两行" % (name, word)); bad += 1
            joined = "".join(lines)
            truncated = joined.endswith(ELLIPSIS)
            body = joined[:-1] if truncated else joined
            if truncated:
                if len(lines) > 3:
                    print("  ❌ %s 截断后仍有 %d 行" % (name, len(lines))); bad += 1
                if len(lines[-1]) < 2:
                    print("  ❌ %s 截断后末行太短" % name); bad += 1
                if not (prefix + text).startswith(body):
                    print("  ❌ %s 截断后和原文前缀对不上" % name); bad += 1
            elif body != prefix + text:
                print("  ❌ %s 没截断却和原文对不上：%r != %r" % (name, body, prefix + text)); bad += 1
    if bad:
        print("❌ 折行规则 %d 处不合格" % bad)
        return 1
    print("✅ 折行规则通过（%d 组：每行≤%dpx / 不劈英文词 / 行首无标点 / 截断补… / 未截断内容完整）"
          % (len(CHECK_CASES), WRAP_W))
    return 0


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    his  = sys.argv[1] if len(sys.argv) > 1 else "你好呀"
    hers = sys.argv[2] if len(sys.argv) > 2 else "昨天的番茄你做了三个，比前天多一个，累不累？"
    f16 = yulan.Font("fusion_ui16.c")
    faces = faces_list()
    cv = yulan.Canvas(240, 320, bg=BG)

    # 顶栏（近似真机）：标题牌 10..161 + 右侧关系账读数。pet 页 band=false，只有一块纯色底
    cv.rect(10, 7, 151, 28, PLATE)
    cv.hline(10, 10 + 150, 7, PLATE_EDGE)
    cv.hline(10, 10 + 150, 7 + 27, PLATE_EDGE)
    for y in range(7, 7 + 28):
        cv.px[y][10] = PLATE_EDGE
        cv.px[y][10 + 150] = PLATE_EDGE
    cv.draw_text(19, 10, PET_TITLE, f16, (0x7F, 0xE7, 0xFF))   # UI_CY
    # 关系账：真机是 montserrat_14（≈6 px/字符），这里拿 fusion_ui12（也是 6 px/字符）代一下，右对齐到 236
    f12 = yulan.Font("fusion_ui12.c")
    cv.draw_text(LVL_X + LVL_W - f12.text_width(LVL_TEXT), LVL_Y, LVL_TEXT, f12, LVL_INK)

    blit_face(cv, faces[0][1])

    # 气泡底板（近似：方角 + 1px 描边；真机是 6px 圆角，预览里画不出来）
    cv.rect(BUB_X, BUB_Y, BUB_W, BUB_H, BUB_BG)
    cv.hline(BUB_X, BUB_X + BUB_W, BUB_Y, BUB_EDGE)
    cv.hline(BUB_X, BUB_X + BUB_W, BUB_Y + BUB_H - 1, BUB_EDGE)
    for y in range(BUB_Y, BUB_Y + BUB_H):
        cv.px[y][BUB_X] = BUB_EDGE
        cv.px[y][BUB_X + BUB_W - 1] = BUB_EDGE

    lines_her = wrap_echo(hers, "她：")
    for i, line in enumerate(lines_her):
        cv.draw_text(BUB_X + BUB_TX, BUB_Y + BUB_TY + i * f16.line_height, line, f16, HER_INK)
    lines_his = wrap_echo(his, "你：")
    for i, line in enumerate(lines_his):
        cv.draw_text(ECHO_X, ECHO_Y + i * f16.line_height, line, f16, ECHO_INK)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    yulan.write_png(OUT, cv)
    print("画好了：%s  （%d×%d）" % (OUT, cv.w, cv.h))
    print("版面：气泡 %d,%d %d×%d（3 行）｜我说的 %d,%d ｜关系账 %d,%d（顶栏）"
          % (BUB_X, BUB_Y, BUB_W, BUB_H, ECHO_X, ECHO_Y, LVL_X, LVL_Y))
    print("立绘：%s（第 1 张 %s）" % (os.path.relpath(FACES, ROOT), faces[0][0]))
    print("  她说：")
    for i, line in enumerate(lines_her, 1):
        print("     第%d行[%d字 %dpx] %s" % (i, len(line), sum(_adv(f16, c) for c in line), line))
    print("  我说：")
    for i, line in enumerate(lines_his, 1):
        print("     第%d行[%d字 %dpx] %s" % (i, len(line), sum(_adv(f16, c) for c in line), line))
    missing = sorted({ch for ch in (his + hers) if not f16.glyph(ord(ch))})
    print("缺字：%s" % ("（无）" if not missing else "".join(missing)))


if __name__ == "__main__":
    main()

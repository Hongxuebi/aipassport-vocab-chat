#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预览版面：把 LVGL 字库（生成的 .c）渲染成 240×320 屏幕图，零依赖纯 stdlib。

为什么要有它：改字号/换版面不该靠"刷到真机再看"。字库 .c 里就有全部位图，
直接解析出来拼成屏幕，**和屏上是同一份像素**（点阵字体尤其准）。

用法：
  python3 _工具/预览版面.py            # 出四联对比图 → 图/版面预览.png
"""
import os
import re
import struct
import zlib

ROOT = os.path.expanduser("~/dev/ai-passport")
FONTS = os.path.join(ROOT, os.environ.get("AIP_FW", "_参考仓库/folotoy-ai-passport"), "main/fonts")
OUTDIR = os.path.join(ROOT, "图")

INK = (23, 32, 42)
MUTED = (107, 117, 125)
PAPER = (244, 244, 234)
SKY = (8, 114, 201)
GRID = (200, 210, 214)

SCREEN_W, SCREEN_H = 240, 320


# ---------------------------------------------------------------- 字体解析
class Font:
    def __init__(self, name):
        path = os.path.join(FONTS, name)
        if not os.path.exists(path):                       # 36px 这类"预览专用"字体在 04-字体/_预览/
            path = os.path.join(ROOT, "04-字体/_预览", name)
        s = open(path, encoding="utf-8", errors="replace").read()
        self.name = name
        self.line_height = int(re.search(r"\.line_height = (\d+)", s).group(1))
        self.base_line = int(re.search(r"\.base_line = (-?\d+)", s).group(1))
        self.bpp = int(re.search(r"\.bpp = (\d+)", s).group(1))

        bm = re.search(r"glyph_bitmap\[\] = \{(.*?)\n\};", s, re.S).group(1)
        self.bitmap = bytes(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", bm))

        gd = re.search(r"glyph_dsc\[\] = \{(.*?)\n\};", s, re.S).group(1)
        self.glyphs = [dict(zip(("bi", "adv", "bw", "bh", "ox", "oy"),
                                (int(v) for v in m)))
                       for m in re.findall(
            r"\.bitmap_index = (\d+), \.adv_w = (\d+), \.box_w = (\d+), \.box_h = (\d+), "
            r"\.ofs_x = (-?\d+), \.ofs_y = (-?\d+)", gd)]

        # unicode_list_N 数组
        lists = {}
        for m in re.finditer(r"static const uint16_t unicode_list_(\d+)\[\] = \{([^}]*)\}", s):
            lists[int(m.group(1))] = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", m.group(2))]

        # cmap 表：把每个码点映射到 gid
        self.cmap = {}
        for m in re.finditer(
                r"\.range_start = (\d+), \.range_length = (\d+), \.glyph_id_start = (\d+),"
                r"\s*\.unicode_list = (unicode_list_(\d+)|NULL), \.glyph_id_ofs_list = (NULL|\w+),"
                r" \.list_length = (\d+)", s):
            start, length, g0 = int(m.group(1)), int(m.group(2)), int(m.group(3))
            ul = lists.get(int(m.group(5)), []) if m.group(4) != "NULL" else []
            if ul:
                for i, off in enumerate(ul):
                    self.cmap[start + off] = g0 + i
            else:                                   # FORMAT0_TINY：连续
                for i in range(length):
                    self.cmap[start + i] = g0 + i

    def glyph(self, cp):
        gid = self.cmap.get(cp)
        if gid is None or gid >= len(self.glyphs):
            return None
        return self.glyphs[gid]

    def text_width(self, text):
        w = 0
        for ch in text:
            g = self.glyph(ord(ch))
            w += (g["adv"] // 16) if g else self.line_height // 2
        return w


# ---------------------------------------------------------------- 画布
class Canvas:
    def __init__(self, w=SCREEN_W, h=SCREEN_H, bg=PAPER):
        self.w, self.h = w, h
        self.px = [[bg for _ in range(w)] for _ in range(h)]

    def hline(self, x0, x1, y, color):
        for x in range(max(0, x0), min(self.w, x1)):
            if 0 <= y < self.h:
                self.px[y][x] = color

    def rect(self, x, y, w, h, color):
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                self.px[yy][xx] = color

    def draw_text(self, x, y_line_top, text, font, color, max_w=None):
        """返回画了几行。y_line_top 是第一行的行顶。"""
        lines = []
        if max_w is None:
            lines = [text]
        else:
            cur = ""
            for ch in text:
                if font.text_width(cur + ch) > max_w and cur:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                lines.append(cur)
        base_off = font.line_height - font.base_line
        for li, line in enumerate(lines):
            pen = x
            ytop_line = y_line_top + li * font.line_height
            for ch in line:
                g = font.glyph(ord(ch))
                if not g:
                    pen += font.line_height // 2
                    continue
                gw, gh = g["bw"], g["bh"]
                gx = pen + g["ox"]
                gy = ytop_line + base_off - gh - g["oy"]
                self._blit(g, gx, gy, color, font.bpp, font.bitmap)
                pen += g["adv"] // 16
        return len(lines)

    def _blit(self, g, gx, gy, color, bpp, bitmap):
        bi, bw, bh = g["bi"], g["bw"], g["bh"]
        if bw == 0 or bh == 0:
            return
        if bpp == 1:
            # 🔴 1bpp 是**连续比特流**（LVGL 的 stride=0 语义）：位序跨行接着排，不是每行按字节补零。
            #    一开始按"每行 (bw+7)//8 字节"算，整块图乱掉（2026-10-25 预览踩过）。
            for yy in range(bh):
                for xx in range(bw):
                    idx = yy * bw + xx
                    off = bi + idx // 8
                    if off < len(bitmap):
                        bit = (bitmap[off] >> (7 - (idx % 8))) & 1
                        if bit:
                            px, py = gx + xx, gy + yy
                            if 0 <= px < self.w and 0 <= py < self.h:
                                self.px[py][px] = color
        else:                                        # 4bpp：只画不透明的部分
            for yy in range(bh):
                for xx in range(bw):
                    idx = yy * bw + xx
                    off = bi + idx // 2
                    if off >= len(bitmap):
                        continue
                    byte = bitmap[off]
                    a = (byte >> 4) if idx % 2 == 0 else (byte & 0xF)
                    if a >= 8:
                        px, py = gx + xx, gy + yy
                        if 0 <= px < self.w and 0 <= py < self.h:
                            self.px[py][px] = color


# ---------------------------------------------------------------- 屏上真版式
def render_card(word, ipa, cn, cn_font, title="背单词", prog="1/20", dbg="D1 新"):
    c = Canvas()
    f12 = Font("fusion_ui12.c")
    f16 = Font("fusion_ui16.c")
    l28 = Font("latin28.c")
    l20 = Font("latin20.c")
    l16 = Font("latin16.c")

    # 标题栏（跟 ui_pixel_screen_create 近似的观感）
    c.rect(0, 0, SCREEN_W, 34, SKY)
    c.draw_text(10, 6, title, f16, (255, 255, 255))
    c.draw_text(SCREEN_W - 10 - l16.text_width(prog), 8, prog, l16, (255, 255, 255))

    # 单词（按长度选 28/20/16，和固件一致）
    wf = l28 if len(word) <= 10 else (l20 if len(word) <= 18 else l16)
    wx = (SCREEN_W - wf.text_width(word)) // 2
    c.draw_text(wx, 50, word, wf, INK)

    # 音标
    ix = (SCREEN_W - l16.text_width(ipa)) // 2
    c.draw_text(ix, 122, ipa, l16, MUTED)

    # 释义（被测字号）
    lines = c.draw_text(8, 162, cn, cn_font, INK, max_w=224)

    # 按键提示
    c.hline(20, SCREEN_W - 20, 242, GRID)
    c.draw_text(18, 250, "▲熟知 ●认识 ▼模糊 长▼忘记", f12, MUTED)
    c.draw_text(10, SCREEN_H - 18, dbg, f12, (154, 165, 171))
    return c, lines


# ---------------------------------------------------------------- PNG
def write_png(path, canvas):
    raw = b""
    for row in canvas.px:
        raw += b"\x00" + b"".join(bytes(p) for p in row)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", canvas.w, canvas.h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    open(path, "wb").write(png)


def stitch(panels, labels, gap=8, label_h=26):
    total_w = sum(p.w for p in panels) + gap * (len(panels) + 1)
    total_h = label_h + panels[0].h + gap
    c = Canvas(total_w, total_h, bg=(232, 236, 238))
    x = gap
    f = Font("latin16.c")
    for p, lab in zip(panels, labels):
        c.draw_text(x, 4, lab, f, (40, 48, 56))
        for yy in range(p.h):
            for xx in range(p.w):
                c.px[label_h + yy][x + xx] = p.px[yy][xx]
        x += p.w + gap
    return c


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    f24 = Font("fusion_ui24.c")
    f36 = Font("fusion_ui36.c")
    f16 = Font("fusion_ui16.c")

    panels, labels = [], []

    LONG = "意大利（人）的，意大利语的；意大利语；意大利人"

    # A 现在：24px + 短释义（96% 的卡都是这种）
    p, n = render_card("friendship", "/ˈfrendʃɪp/", "友谊", f24)
    panels.append(p); labels.append(f"A  now 24px short  ({n} line)")

    # B 36px + 短释义
    p, n = render_card("friendship", "/ˈfrendʃɪp/", "友谊", f36)
    panels.append(p); labels.append(f"B  36px short  ({n} line)")

    # C 36px + 最长释义（会压到按键提示）
    p, n = render_card("Italian", "/ɪˈtæliən/", LONG, f36, prog="12/20", dbg="D3 复")
    panels.append(p); labels.append(f"C  36px longest  ({n} lines!)")

    # D 混合：短释义 36px、长释义自动降 24px
    p, n = render_card("Italian", "/ɪˈtæliən/", LONG, f24, prog="12/20", dbg="D3 复")
    panels.append(p); labels.append(f"D  long->24px  ({n} lines)")

    img = stitch(panels, labels)
    out = os.path.join(OUTDIR, "版面预览-释义字号.png")
    write_png(out, img)
    print(f"✅ {out}  （{img.w}×{img.h}）")


if __name__ == "__main__":
    main()

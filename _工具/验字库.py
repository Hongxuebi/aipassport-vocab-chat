#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线验证 LVGL 字库：**不发到设备**就能查出"缺字/位图被截断/压缩格式不兼容"。

为什么要有（2026-10-25 一晚踩了三次）：
  ① 4bpp 字体被 lv_font_conv 默认压缩（bitmap_format=1），固件没开 LV_USE_FONT_COMPRESSED
     → 取位图返回 NULL，屏上英文一片空白。
  ② 字库是**子集**：新文案（"按任意键看释义"）里的字没加进去 → 方块。
  ③ 只打印前 8 位字符串时，编译/链接器会把剩下的字符折掉（令牌尾巴消失）。

用法：
  python3 _工具/验字库.py                       # 全部检查
  python3 _工具/验字库.py --text "按任意键看释义"  # 额外检查这些字有没有
"""
import argparse
import os
import re
import sys

ROOT = os.path.expanduser("~/dev/ai-passport")
FONTS = os.path.join(ROOT, os.environ.get("AIP_FW", "_参考仓库/folotoy-ai-passport"), "main/fonts")
PREVIEW = os.path.join(ROOT, "04-字体/_预览")


def load(path):
    s = open(path, encoding="utf-8", errors="replace").read()
    bm = re.search(r"glyph_bitmap\[\] = \{(.*?)\n\};", s, re.S)
    gd = re.search(r"glyph_dsc\[\] = \{(.*?)\n\};", s, re.S)
    if not bm or not gd:
        return None
    bitmap = bytes(int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", bm.group(1)))
    glyphs = [tuple(int(v) for v in m) for m in re.findall(
        r"\.bitmap_index = (\d+), \.adv_w = (\d+), \.box_w = (\d+), \.box_h = (\d+)", gd.group(1))]
    bpp = int(re.search(r"\.bpp = (\d+)", s).group(1))
    fmt = re.search(r"\.bitmap_format = (\d)", s)
    line_h = int(re.search(r"\.line_height = (\d+)", s).group(1))

    # cmap：连续区间 + 稀疏表
    lists = {}
    for m in re.finditer(r"static const uint16_t unicode_list_(\d+)\[\] = \{([^}]*)\}", s):
        lists[int(m.group(1))] = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", m.group(2))]
    cmap = set()
    for m in re.finditer(
            r"\.range_start = (\d+), \.range_length = (\d+), \.glyph_id_start = (\d+),"
            r"\s*\.unicode_list = (unicode_list_(\d+)|NULL)", s):
        start, length = int(m.group(1)), int(m.group(2))
        ul = lists.get(int(m.group(5)), []) if m.group(4) != "NULL" else []
        if ul:
            cmap.update(start + off for off in ul)
        else:
            cmap.update(range(start, start + length))
    return {"bitmap": bitmap, "glyphs": glyphs, "bpp": bpp,
            "fmt": int(fmt.group(1)) if fmt else -1, "line_h": line_h, "cmap": cmap}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="", help="额外要检查的文案")
    a = ap.parse_args()

    bad = 0
    names = sorted([f for f in os.listdir(FONTS) if re.match(r"(fusion|latin)", f) and f.endswith(".c")])
    names += [f for f in sorted(os.listdir(PREVIEW)) if f.endswith(".c")] if os.path.isdir(PREVIEW) else []
    if not names:
        sys.exit("❌ 一个字体文件都没找到")

    print(f"{'字体':<16}{'bpp':>4}{'格式':>5}{'字形':>6}{'位图B':>8}  结论")
    for n in names:
        p = os.path.join(FONTS, n)
        if not os.path.exists(p):
            p = os.path.join(PREVIEW, n)
        f = load(p)
        if not f:
            print(f"{n:<16}  �� 解析失败"); bad += 1; continue
        need = 0
        for (bi, aw, bw, bh) in f["glyphs"]:
            need = max(need, bi + (bw * bh * f["bpp"] + 7) // 8)
        ok_fmt = f["fmt"] == 0                     # 0 = PLAIN；1 = RLE，固件没开支持就画不出
        ok_size = need <= len(f["bitmap"])
        verdict = "✅" if (ok_fmt and ok_size) else []
        notes = []
        if not ok_fmt:
            notes.append("❌ bitmap_format=%d（压缩！固件未开 LV_USE_FONT_COMPRESSED → 屏上空白）" % f["fmt"])
        if not ok_size:
            notes.append(f"❌ 位图被截断：需要 {need} B，实际 {len(f['bitmap'])} B")
        if not notes:
            notes.append("✅ 未压缩、位图完整")
        print(f"{n:<16}{f['bpp']:>4}{f['fmt']:>5}{len(f['glyphs']):>6}{len(f['bitmap']):>8}  {' '.join(notes)}")
        bad += 0 if (ok_fmt and ok_size) else 1

    if a.text:
        # 用最小的中文字库做"有没有这个字"的检查
        ref = load(os.path.join(FONTS, "fusion_ui12.c"))
        miss = [c for c in a.text if ord(c) > 0x7E and ord(c) not in ref["cmap"]]
        print(f"\n文案检查「{a.text}」：{'✅ 字都在' if not miss else '❌ 缺字 ' + ''.join(miss) + '（会显示方块，需重跑 04-字体/生成UI字库.py）'}")
        bad += 1 if miss else 0

    print("\n" + ("✅ 字库体检通过" if bad == 0 else f"❌ {bad} 处问题"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

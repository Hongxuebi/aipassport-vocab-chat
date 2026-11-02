#!/usr/bin/env python3
"""把 图鉴/sprites/NNN.gif 转成设备用的 4bpp 精灵文件。

文件格式（设备端 assets 分区里直接读）：
    'D','X' | u8 w | u8 h | u8 fmt(1=4bpp 调色板) | u8 npal | u8 pad[2] | pal[npal] (RGB565 小端，2B 一个) | 像素数据
    像素：每字节两个像素，**高 4 位是左边**那个；索引 0 = 透明（调色板第 0 项弃用）
用法：
    python3 _工具/图鉴转4bpp.py --ids 1-60 --out ./_图鉴4bpp
"""
import argparse, os, sys, json
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPR = os.path.join(ROOT, "图鉴", "sprites")

def rgb888_to_565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

def convert_anim(gif_path, size=48, colors=15, frames=6):
    """把 GIF 等间隔采样成 N 帧，写成一个动画文件：
       'D','A' | w | h | fmt(1) | npal | frames | delay_ms | pad | pal | frame0 | frame1 | ..."""
    im = Image.open(gif_path)
    n = getattr(im, "n_frames", 1)
    step = max(1, n // frames)
    out = bytearray()
    out += b"DA" + bytes([size, size, 1, colors + 1, frames, 40, 0])
    pal_bytes = bytearray(b"\x00\x00")
    body = bytearray()
    first_pal = None
    for k in range(frames):
        idx_no = min(n - 1, k * step)
        im.seek(idx_no)
        fr = im.convert("RGBA")
        bg = Image.new("RGBA", fr.size, (0, 0, 0, 255))
        fr = Image.alpha_composite(bg, fr).convert("RGB").resize((size, size), Image.NEAREST)
        q = fr.quantize(colors=colors, method=Image.MEDIANCUT)
        pal = q.getpalette()[:] + [0] * (colors * 3 - len(q.getpalette() or []))
        if first_pal is None:
            first_pal = pal
            for i in range(colors):
                r, g, b = pal[i * 3:i * 3 + 3]
                v = rgb888_to_565(r, g, b)
                pal_bytes += bytes([v & 0xFF, v >> 8])
        pix = q.tobytes()
        for y in range(size):
            row = pix[y * size:(y + 1) * size]
            for x in range(0, size, 2):
                a = row[x] + 1
                b2 = row[x + 1] + 1 if x + 1 < size else 0
                body.append(((a & 0xF) << 4) | (b2 & 0xF))
    out += bytes(pal_bytes) + bytes(body)
    return bytes(out)


def convert(gif_path, size=96, colors=15):
    im = Image.open(gif_path)
    im.seek(0)                                  # 第一帧
    im = im.convert("RGBA")
    # 透明像素 → 黑色（设备端按调色板 0 当透明处理，这里先给黑）
    bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
    im = Image.alpha_composite(bg, im).convert("RGB")
    im = im.resize((size, size), Image.NEAREST)
    q = im.quantize(colors=colors, method=Image.MEDIANCUT)   # 15 色（0 号留给透明）
    pal = q.getpalette()[:]
    pal += [0] * (colors * 3 - len(pal))        # 小图调色板可能不足 16 色，补 0
    idx = q.tobytes()               # 每像素 1 字节索引（0..colors-1）
    out = bytearray()
    out += b"DX" + bytes([size, size, 1, colors + 1]) + b"\x00\x00"   # 6B 头 + 2B 对齐 = 8B
    out += b"\x00\x00"              # 调色板 0（透明槽）
    for i in range(colors):
        r, g, b = pal[i*3:i*3+3]
        v = rgb888_to_565(r, g, b)
        out += bytes([v & 0xFF, v >> 8])
    # 逐像素：4bpp 水平打包（每字节两个像素，高 4 位左）
    for y in range(size):
        row = idx[y*size:(y+1)*size]
        for x in range(0, size, 2):
            a = row[x] + 1                      # +1：0 号留给透明
            b = row[x+1] + 1 if x + 1 < size else 0
            out.append(((a & 0xF) << 4) | (b & 0xF))
    return bytes(out)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="1-60", help="1-60 / 1,4,7 / all")
    ap.add_argument("--out", default=os.path.join(ROOT, "_图鉴4bpp"))
    ap.add_argument("--size", type=int, default=96)
    ap.add_argument("--anim", type=int, default=0, help=">0 时额外生成 anim/fNNNN.bin（N 帧，默认 48×48）")
    ap.add_argument("--anim-size", type=int, default=48)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.ids == "all":
        ids = list(range(1, 650))
    elif "-" in a.ids and "," not in a.ids:
        s, e = a.ids.split("-"); ids = list(range(int(s), int(e) + 1))
    else:
        ids = [int(x) for x in a.ids.split(",")]
    total = 0
    for i in ids:
        src = os.path.join(SPR, f"{i:03d}.gif")
        if not os.path.exists(src):
            print(f"跳过 {i:03d}（没有图）"); continue
        data = convert(src, a.size)
        dst = os.path.join(a.out, f"f{i:04d}.bin")
        open(dst, "wb").write(data)
        total += len(data)
        if a.anim > 0:
            os.makedirs(os.path.join(a.out, "anim"), exist_ok=True)
            anim = convert_anim(src, size=a.anim_size, frames=a.anim)
            open(os.path.join(a.out, "anim", f"f{i:04d}.bin"), "wb").write(anim)
            total_anim = globals().get("total_anim", 0) + len(anim)
            globals()["total_anim"] = total_anim
    # 顺带生成设备用的名称索引（只有 id/中文名/属性，一行一只，≤40 字节）
    import re as _re
    js = open(os.path.join(ROOT, "图鉴", "index.js"), encoding="utf-8").read()
    entries = _re.findall(r'\{"id":(\d+),"n":"([^"]+)","e":"[^"]*","t":\[([^\]]*)\]', js)
    lines = []
    for sid, name, types in entries:
        if int(sid) not in ids:
            continue
        t = "".join(x.strip().strip('"') for x in types.split(","))
        lines.append(f"{sid.zfill(3)}#{name}#{t}")
    open(os.path.join(a.out, "index.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"名称索引 index.txt：{len(lines)} 条（{os.path.getsize(os.path.join(a.out,'index.txt'))} 字节）")

    n = len(ids)
    print(f"转了 {n} 只，单只 {total//max(n,1)} B，合计 {total/1024:.0f} KB → {a.out}")
    ta = globals().get("total_anim", 0)
    if a.anim > 0 and ta:
        print(f"动画：{a.anim} 帧 @ {a.anim_size}×{a.anim_size}，单只 {ta//max(n,1)} B，合计 {ta/1024:.0f} KB")
    if n:
        print(f"推算 649 只（4bpp {a.size}x{a.size}）：{total//max(n,1)*649/1024/1024:.2f} MB")

if __name__ == "__main__":
    main()

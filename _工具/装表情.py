#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""装表情.py —— 把 12 张立绘打成一个 `faces.bin`（FAC1），烧到 `pet` 分区（0x210000）。

为什么有它：桌宠从 85 段动图（4.7 MB）换成 12 张静态脸（437 KB），固件要的是
**mmap 直读、零解码**的定长帧包 —— 这个包只该由脚本生成，手搓必错。

用法：
  python3 _工具/装表情.py 05-表情/大肥鱼-12表情/设备140x130/ -o faces.bin
  python3 _工具/装表情.py <目录> -o faces.bin --lvl 3=0,7=1,9=2   # 覆盖亲密度门槛

约定（跟 65 §8.1 一致）：
  · 画面 **140×130**，标准 **RGB565 小端**（R 在高 5 位）—— 和 `07-桌宠素材/tools/pack_pet.py` 同一套，
    真机已验。鲸语固件那套 BGR565（B 在高 5 位）**只用于改它的 bin**，两套别混。
  · 文件名 `NN-名字.png`：`NN` = face_id（0..11），`名字` 进目录做日志用。
  · 目录项 40 B：name[28] | off u32 | size u32 | layer u8 | lvl u8 | pad u16
  · layer：0..5 = 表层，6..11 = 里层；lvl：0/1/2 = 需要的亲密度等级

输出判据：
  · 文件大小 = 16 + 40×n + 36,400×n（n=12 → **437,296 B**）
  · 每项 size=36400；每个 off % 8 == 0
"""
import argparse
import os
import re
import struct
import sys

from PIL import Image

W, H = 140, 130
PX = W * H * 2                     # 36,400
DIR_ENTRY = 40
MAGIC = b'FAC1'
CF_RGB565 = 0x12
# 里层默认门槛（表层一律 0）：6 开心 L0 / 7 心软 L1 / 8 害羞 L2 / 9 求别走 L2 / 10 大哭 L2 / 11 迷糊 全员
DEFAULT_LVL = {6: 0, 7: 1, 8: 2, 9: 2, 10: 2, 11: 0}


def fit(im: Image.Image) -> Image.Image:
    """任意尺寸 → 140×130：**先按比例居中裁**（别硬拉，脸会变形）再 Lanczos 缩。"""
    if im.size == (W, H):
        return im
    w, h = im.size
    tar = W / H
    ar = w / h
    if abs(ar - tar) > 0.01:
        if ar > tar:                       # 太宽：裁左右
            nw = max(1, round(h * tar))
            x0 = (w - nw) // 2
            im = im.crop((x0, 0, x0 + nw, h))
        else:                              # 太高：裁上下
            nh = max(1, round(w / tar))
            y0 = (h - nh) // 2
            im = im.crop((0, y0, w, y0 + nh))
    return im.resize((W, H), Image.LANCZOS)


def rgb565(path: str) -> bytes:
    """→ 标准 RGB565 小端（R 高 5 位）。**截断量化**，跟 LVGL 的 lv_color_to_u16 同一条路 ——
    这样"图片底色"和"页面底色"量化后是同一个值，接缝才真的是 0。"""
    im = fit(Image.open(path).convert('RGB'))
    out = bytearray(PX)
    i = 0
    for r, g, b in im.getdata():
        # 截断（不是四舍五入）：跟 pack_pet.py / LVGL 一致；顺带不会出现 255+4>>3 溢出成 32 的事故
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[i] = v & 0xFF
        out[i + 1] = (v >> 8) & 0xFF
        i += 2
    return bytes(out)


def scan(src: str):
    files = [f for f in os.listdir(src) if f.lower().endswith(('.png', '.webp', '.jpg'))]
    items = []
    for f in files:
        m = re.match(r'^(\d+)[-_](.+)\.\w+$', f)
        if not m:
            print(f'[skip] 名字不是 NN-xxx.png：{f}')
            continue
        items.append((int(m.group(1)), m.group(2), os.path.join(src, f)))
    items.sort()
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('-o', '--out', default='faces.bin')
    ap.add_argument('--lvl', default='', help='覆盖门槛，例：3=0,7=1,9=2')
    ap.add_argument('--layer', default='', help='覆盖图层（默认 id≥6 判里层），例：12=0,13=0')
    a = ap.parse_args()

    items = scan(a.src)
    if not items:
        print('!! 目录里没有 NN-名字.png'); return 2

    ids = [i for i, _, _ in items]
    if ids != list(range(len(items))):
        print(f'!! face_id 必须从 0 连续：{ids}'); return 3

    layer_override = {}
    for kv in filter(None, a.layer.split(',')):
        k, v = kv.split('=')
        layer_override[int(k)] = int(v)

    lvl_override = {}
    for kv in filter(None, a.lvl.split(',')):
        k, v = kv.split('=')
        lvl_override[int(k)] = int(v)

    body = bytearray()
    dirs = bytearray()
    base = 16 + DIR_ENTRY * len(items)
    for fid, name, path in items:
        nb = name.encode('utf-8')
        if len(nb) > 27:
            print(f'[warn] 名字太长会截断：{name}')
            nb = nb[:27]
        px = rgb565(path)
        if len(px) != PX:
            print(f'!! {name} 像素数不对 {len(px)}'); return 4
        off = base + len(body)
        assert off % 8 == 0, off
        # 默认：0..5 表层、6.. 里层；张嘴三档（12/13）靠 --layer 12=0,13=0 钉回表层
        layer = layer_override.get(fid, 0 if fid <= 5 else 1)
        lvl = lvl_override.get(fid, DEFAULT_LVL.get(fid, 0))
        dirs += nb.ljust(28, b'\0') + struct.pack('<IIBBH', off, len(px), layer, lvl, 0)
        body += px

    head = MAGIC + struct.pack('<BBHHBBH', 1, len(items), W, H, CF_RGB565, 0, DIR_ENTRY)
    head += b'\0\0'                       # 凑到 16
    blob = head + bytes(dirs) + bytes(body)

    with open(a.out, 'wb') as f:
        f.write(blob)

    print(f'[ok] {a.out}  {len(blob):,} B  ({len(items)} 张，头 16 + 目录 {DIR_ENTRY*len(items)} + 像素 {len(body):,})')
    print(f'     期望 {16 + DIR_ENTRY*len(items) + PX*len(items):,} B')
    for fid, name, _ in items:
        print(f'     {fid:>2} {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

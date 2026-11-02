#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_pet.py —— 解回 pack_pet.py 生成的 pet_assets.bin，验证帧包能被正确还原。

这份解码逻辑就是 C 端 pet_anim.c 的参考实现：
  索引色(0=透明) → PackBits 解包 → 查 RGB565 调色板 → 贴到背景

用法：
  python verify_pet.py <pet_assets.bin> [--out preview.png] [--anim 名字]
"""
import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAGIC = b"PET1"


def unpackbits(buf: bytes, expected: int) -> bytes:
    """pack_pet.py 的 PackBits：<0x80 重复 n+1 次；>=0x80 字面量 (n&0x7F)+1 字节。"""
    out = bytearray()
    i, n = 0, len(buf)
    while i < n and len(out) < expected:
        c = buf[i]; i += 1
        if c < 0x80:
            cnt = c + 1
            if i >= n:
                break
            out.extend(bytes([buf[i]]) * cnt)
            i += 1
        else:
            cnt = (c & 0x7F) + 1
            out.extend(buf[i:i + cnt])
            i += cnt
    return bytes(out[:expected])


def parse(bin_path: Path):
    d = bin_path.read_bytes()
    if d[:4] != MAGIC:
        raise SystemExit("不是 pet_assets.bin（magic 不对）")
    ver, pal_n, w, h = struct.unpack_from("<BHHH", d, 4)
    off = 11
    pal565 = np.frombuffer(d, dtype="<u2", count=pal_n, offset=off).copy()
    off += pal_n * 2
    (anim_n,) = struct.unpack_from("<H", d, off); off += 2
    anims = []
    for _ in range(anim_n):
        nl = d[off]; off += 1
        name = d[off:off + nl].decode("utf-8"); off += nl
        fcount, fps, _rsv = struct.unpack_from("<HHB", d, off); off += 5
        offs = list(struct.unpack_from(f"<{fcount}I", d, off)); off += 4 * fcount
        anims.append({"name": name, "count": fcount, "fps": fps, "offsets": offs})
    return {"ver": ver, "pal": pal565, "w": w, "h": h, "anims": anims, "blob": d}


def pal_to_rgb(pal565):
    r = ((pal565 >> 11) & 0x1F).astype(np.uint16) * 255 // 31
    g = ((pal565 >> 5) & 0x3F).astype(np.uint16) * 255 // 63
    b = (pal565 & 0x1F).astype(np.uint16) * 255 // 31
    return np.stack([r, g, b], axis=1).astype(np.uint8)


def decode_frame(pk, blob, w, h):
    (ln,) = struct.unpack_from("<H", blob, pk)
    packed = blob[pk + 2: pk + 2 + ln]
    idx = np.frombuffer(unpackbits(packed, w * h), dtype=np.uint8)
    if len(idx) < w * h:
        idx = np.pad(idx, (0, w * h - len(idx)))
    return idx.reshape(h, w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bin")
    ap.add_argument("--out", default="preview.png")
    ap.add_argument("--anim", default=None)
    a = ap.parse_args()

    p = parse(Path(a.bin))
    rgb = pal_to_rgb(p["pal"])
    w, h = p["w"], p["h"]
    print(f"版本 {p['ver']}  精灵 {w}x{h}  动作 {len(p['anims'])}  "
          f"文件 {len(p['blob'])/1024:.0f} KB")

    pick = p["anims"][0] if not a.anim else next(
        (x for x in p["anims"] if x["name"] == a.anim), p["anims"][0])
    print(f"取动作：{pick['name']}  {pick['count']} 帧 @ {pick['fps']}fps")

    # 解码全部帧，铺成一张 contact sheet（每格带棋盘格底，方便看透明）
    cols = min(pick["count"], 8)
    rows = (pick["count"] + cols - 1) // cols
    sheet = np.zeros((rows * h, cols * w, 3), np.uint8)
    sheet[:, :, :] = 40
    # 棋盘格
    yy, xx = np.mgrid[0:rows * h, 0:cols * w]
    chk = ((yy // 8 + xx // 8) % 2) * 25 + 30
    sheet = np.dstack([chk, chk, chk]).astype(np.uint8)

    stats = []
    for i, off in enumerate(pick["offsets"]):
        (ln,) = struct.unpack_from("<H", p["blob"], off)
        idx = np.frombuffer(unpackbits(p["blob"][off + 2:off + 2 + ln], w * h), dtype=np.uint8)
        if len(idx) < w * h:
            idx = np.pad(idx, (0, w * h - len(idx)))
        idx = idx.reshape(h, w)
        stats.append(ln)
        mask = idx > 0
        tile = sheet[(i // cols) * h:(i // cols) * h + h, (i % cols) * w:(i % cols) * w + w]
        tile[mask] = rgb[idx[mask]]
    Image.fromarray(sheet).save(a.out)
    print(f"平均 {sum(stats)/len(stats)/1024:.2f} KB/帧   "
          f"最大 {max(stats)/1024:.2f} KB   最小 {min(stats)/1024:.2f} KB")
    print(f"预览已存：{a.out}")


if __name__ == "__main__":
    main()

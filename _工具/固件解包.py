#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固件解包.py —— 一把梭：AI Passport / ESP32 整片固件 → 分区表、app 段表、ELF、内嵌 LVGL 图片

为什么有它：2026-10-27 解鲸语(09)时踩了两个坑（地址映射基准算错、通道是 BGR565 不是 RGB565），
每个都白花一小时。这个脚本把那套流程固化成一条命令，跑在开发机或出图机上（纯标准库，无第三方依赖）。

用法：
  探测（只看有什么）      python3 固件解包.py 09.bin --probe
  全解（ELF + 所有图）    python3 固件解包.py 09.bin --out ./_解包
  只要 ELF                python3 固件解包.py 09.bin --elf-only
  指定分区偏移           python3 固件解包.py 09.bin --app 0x10000

输出：
  <out>/segments.txt        段表
  <out>/app.elf             Ghidra 可吃的 ELF32/Xtensa
  <out>/img/0001_rgb565.png 每张图两版（RGB565 / BGR565），挑颜色对的那版
"""
import argparse
import os
import struct
import sys
import zlib

APP_MAGIC = 0xE9


# ────────────────────────────── 基础解析 ──────────────────────────────
def read_partitions(b):
    """0x8000 处的分区表，每项 32 字节，到 0xAA50 结束或空项为止"""
    out = []
    off = 0x8000
    while off + 32 <= len(b):
        e = b[off:off + 32]
        if e[:2] != b'\xaa\x50':
            break
        typ, sub = e[2], e[3]
        addr, size = struct.unpack_from('<II', e, 4)
        name = e[12:28].rstrip(b'\x00').decode('utf-8', 'replace')
        out.append({'name': name, 'type': typ, 'sub': sub, 'off': addr, 'size': size})
        off += 32
    return out


def find_app(b, hint=None):
    """定位 app 镜像（0xE9 魔数）；hint 优先，其次 factory 分区，最后扫 0x10000 起的对齐位"""
    cands = []
    if hint is not None:
        cands.append(hint)
    for p in read_partitions(b):
        if p['type'] == 0:
            cands.append(p['off'])
    cands += [0x10000, 0x20000, 0x130000, 0x0]
    for c in cands:
        if 0 <= c < len(b) and b[c] == APP_MAGIC:
            return c
    for c in range(0, min(len(b), 0x400000), 0x1000):
        if b[c] == APP_MAGIC and b[c + 1] in (2, 3, 4, 5, 6, 7, 8, 9):
            return c
    raise SystemExit("❌ 找不到 app 镜像（0xE9）")


def parse_app(b, app_off):
    """段表：header 之后 6 段，每段 8 字节头(addr,len) + 数据"""
    nseg = b[app_off + 1]
    entry = struct.unpack_from('<I', b, app_off + 4)[0]
    p = app_off + 24
    segs = []
    for _ in range(nseg):
        addr, ln = struct.unpack_from('<II', b, p)
        p += 8
        segs.append({'addr': addr, 'len': ln, 'file': p, 'data': b[p:p + ln]})
        p += ln
    return entry, segs, p


def flash_base(segs):
    """从 seg0 反推：flash 地址 = 文件偏移 + base   ←←← 2026-10-27 踩过的坑，别再用 0x3c000000 硬编码"""
    s = segs[0]
    return s['addr'] - s['file']


# ────────────────────────────── ELF ──────────────────────────────
def build_elf(entry, segs):
    phnum = len(segs)
    ehsize, phentsize = 52, 32
    off = (ehsize + phentsize * phnum + 3) & ~3
    phdrs, blobs, cur = [], [], off
    for s in segs:
        if cur & 3:
            blobs.append(b'\x00' * (4 - (cur & 3)))
            cur += 4 - (cur & 3)
        a = s['addr']
        flags = 5 if (a >> 24) in (0x3c, 0x42) or (a & 0xFFFF0000) == 0x40380000 else 6
        phdrs.append(struct.pack('<IIIIIIII', 1, cur, a, a, s['len'], s['len'], flags, 4))
        blobs.append(s['data'])
        cur += s['len']
    ehdr = (b'\x7fELF' + bytes([1, 1, 1, 0]) + b'\x00' * 8
            + struct.pack('<HHIIIIIHHHHHH', 2, 94, 1, entry, ehsize, 0, 0,
                          ehsize, phentsize, phnum, 0, 0, 0))
    pad = b'\x00' * (off - ehsize - phentsize * phnum)
    return ehdr + b''.join(phdrs) + pad + b''.join(blobs)


# ────────────────────────────── 图片 ──────────────────────────────
def write_png(path, w, h, rgb):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgb[y * w * 3:(y + 1) * w * 3]

    def chunk(t, d):
        return (struct.pack('>I', len(d)) + t + d
                + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff))
    hdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', hdr)
                           + chunk(b'IDAT', zlib.compress(bytes(raw), 6)) + chunk(b'IEND', b''))


def carve_files(b, outdir, min_size=1024):
    """从整片里"雕"出 JPEG/PNG/GIF —— 有些固件把图压成 JPEG 塞在文件系统里"""
    os.makedirs(outdir, exist_ok=True)
    made = []

    def add(name, data):
        if len(data) < min_size:
            return
        path = os.path.join(outdir, name)
        open(path, 'wb').write(data)
        made.append(path)

    # JPEG：每个 SOI 找最近的 EOI；从 SOI+2 继续找，嵌套/内嵌缩略图都会成候选
    seen, i, n = set(), 0, 0
    while True:
        i = b.find(b'\xff\xd8\xff', i)
        if i < 0:
            break
        j = b.find(b'\xff\xd9', i + 4)
        if j < 0:
            break
        key = (i, j)
        if key not in seen:
            seen.add(key)
            if j + 2 - i >= min_size:
                n += 1
                add('jpeg_%03d.jpg' % n, b[i:j + 2])
        i += 2
    # PNG
    i, n = 0, 0
    while True:
        i = b.find(b'\x89PNG\r\n\x1a\n', i)
        if i < 0:
            break
        j = b.find(b'IEND\xaeB`\x82', i)
        if j < 0:
            break
        n += 1
        add('png_%03d.png' % n, b[i:j + 8])
        i = j + 8
    return made


def scan_lvgl(b, base, lo, hi):
    """LVGL v9 图像描述符：magic 0x19, cf, w, h, stride, reserved, data_size, data_ptr"""
    hits = []
    for off in range(lo, hi - 24):
        if b[off] != 0x19:
            continue
        cf = b[off + 1]
        if not (0x01 <= cf <= 0x24):
            continue
        w, h, stride, _resv = struct.unpack_from('<HHHH', b, off + 4)
        if not (8 <= w <= 480 and 8 <= h <= 480):
            continue
        size, ptr = struct.unpack_from('<II', b, off + 12)
        if size not in (w * h * 2, w * h * 3, w * h * 4, w * h):
            continue
        fo = ptr - base
        if not (0 <= fo < len(b) or 0 <= fo + size <= len(b)):
            continue
        hits.append({'dsc': off, 'w': w, 'h': h, 'cf': cf, 'stride': stride,
                     'size': size, 'ptr': ptr, 'data_off': fo})
    return hits


def export_images(b, hits, outdir):
    os.makedirs(outdir, exist_ok=True)
    made = []
    for i, d in enumerate(hits, 1):
        w, h, size, fo = d['w'], d['h'], d['size'], d['data_off']
        if not (0 <= fo and fo + size <= len(b)):
            continue
        px = b[fo:fo + size]
        if size == w * h * 2:                      # 16 位
            for tag, swap in (('rgb565', False), ('bgr565', True)):
                rgb = bytearray()
                for k in range(0, w * h * 2, 2):
                    v = px[k] | (px[k + 1] << 8)
                    r = (v >> 11) & 31
                    g = (v >> 5) & 63
                    bb = v & 31
                    if swap:
                        r, bb = bb, r
                    rgb += bytes((r * 255 // 31, g * 255 // 63, bb * 255 // 31))
                p = os.path.join(outdir, '%04d_%s.png' % (i, tag))
                write_png(p, w, h, rgb)
                made.append(p)
        elif size == w * h:                        # 8 位灰度/alpha
            rgb = bytearray()
            for v in px:
                rgb += bytes((v, v, v))
            p = os.path.join(outdir, '%04d_gray8.png' % i)
            write_png(p, w, h, rgb)
            made.append(p)
    return made


# ────────────────────────────── 主流程 ──────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('firmware')
    ap.add_argument('--out', default='_解包')
    ap.add_argument('--app', default=None, help='app 镜像偏移，如 0x10000')
    ap.add_argument('--probe', action='store_true', help='只打印分区/段/图像清单')
    ap.add_argument('--elf-only', action='store_true')
    ap.add_argument('--carve', action='store_true', help='顺带雕出内嵌 JPEG/PNG')
    a = ap.parse_args()

    b = open(a.firmware, 'rb').read()
    print('固件 %s  %.2f MB' % (a.firmware, len(b) / 1048576))

    parts = read_partitions(b)
    if parts:
        print('\n分区表 @0x8000：')
        for p in parts:
            print('  %-10s type=%d sub=%d off=0x%06x size=0x%06x (%dK)'
                  % (p['name'], p['type'], p['sub'], p['off'], p['size'], p['size'] // 1024))
    else:
        print('分区表：空/未找到')

    app = find_app(b, int(a.app, 0) if a.app else None)
    entry, segs, end = parse_app(b, app)
    base = flash_base(segs)
    print('\napp 头 @0x%06x  entry=0x%08x  段数=%d  数据到 0x%x' % (app, entry, len(segs), end))
    print('★ 地址映射：flash 地址 = 文件偏移 + 0x%08x   ← 必须用它，别硬编码 0x3c000000' % base)
    print('\n段表：')
    for i, s in enumerate(segs):
        print('  seg%d  load=0x%08x  len=0x%06x  file=0x%06x' % (i, s['addr'], s['len'], s['file']))

    hits = scan_lvgl(b, base, app, min(len(b), end))
    print('\nLVGL 图像描述符：%d 个' % len(hits))
    for i, d in enumerate(hits, 1):
        print('  #%d dsc@0x%06x  %dx%d cf=0x%02x stride=%d size=%d data@0x%06x'
              % (i, d['dsc'], d['w'], d['h'], d['cf'], d['stride'], d['size'], d['data_off']))

    if a.probe:
        return

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'segments.txt'), 'w') as f:
        f.write('app_off=0x%x entry=0x%x base=0x%x\n' % (app, entry, base))
        for i, s in enumerate(segs):
            f.write('seg%d load=0x%08x len=0x%06x file=0x%06x\n' % (i, s['addr'], s['len'], s['file']))

    elf = os.path.join(a.out, 'app.elf')
    open(elf, 'wb').write(build_elf(entry, segs))
    print('\n✅ ELF  %s  %.2f MB' % (elf, os.path.getsize(elf) / 1048576))

    if a.carve:
        made = carve_files(b, os.path.join(a.out, 'carve'))
        print('✅ 雕出 %d 个内嵌图（JPEG/PNG）→ %s' % (len(made), os.path.join(a.out, 'carve')))

    if not a.elf_only and hits:
        made = export_images(b, hits, os.path.join(a.out, 'img'))
        print('✅ 图片 %d 个 → %s' % (len(made), os.path.join(a.out, 'img')))
        print('   每张两版：rgb565 / bgr565 —— 颜色对的那版才是真的（鲸语是 bgr565）')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""ESP32 整片镜像 → 可给 Ghidra 吃的 ELF32(Xtensa)

用法: esp32_bin2elf.py <in.bin> <out.elf> [app_offset]
默认在 0x10000 找 app 头（0xE9），解析 6 段，按 load 地址铺 PT_LOAD。
"""
import struct, sys, os

E_MACHINE_XTENSA = 94

def parse_app(b, app_off):
    assert b[app_off] == 0xE9, "app magic 不是 0xE9"
    segs = b[app_off + 1]
    entry = struct.unpack_from('<I', b, app_off + 4)[0]
    p = app_off + 24
    out = []
    for i in range(segs):
        addr, ln = struct.unpack_from('<II', b, p); p += 8
        data = b[p:p + ln]; p += ln
        out.append((addr, ln, data))
    return entry, out, p

def build_elf(entry, segs):
    phnum = len(segs)
    ehsize, phentsize = 52, 32
    off = ehsize + phentsize * phnum
    off = (off + 3) & ~3
    phdrs, blobs = [], []
    cur = off
    for addr, ln, data in segs:
        align = cur & 3
        if align:
            blobs.append(b'\x00' * (4 - align)); cur += 4 - align
        # R-X 还是 RW：看地址段（0x3c/0x42 是 flash 只读；0x3f/0x4038 是 RAM）
        flags = 5 if ((addr >> 24) in (0x3c, 0x42) or (addr & 0xFFFF0000) == 0x40380000) else 6
        phdrs.append(struct.pack('<IIIIIIII', 1, cur, addr, addr, ln, ln, flags, 4))
        blobs.append(data)
        cur += ln
    ehdr = (b'\x7fELF' + bytes([1, 1, 1, 0]) + b'\x00' * 8
            + struct.pack('<HHIIIIIHHHHHH', 2, E_MACHINE_XTENSA, 1, entry, ehsize, 0, 0,
                          ehsize, phentsize, phnum, 0, 0, 0))
    body = b''.join(blobs)
    pad = b'\x00' * (off - ehsize - phentsize * phnum)
    return ehdr + b''.join(phdrs) + pad + body

def main():
    src, dst = sys.argv[1], sys.argv[2]
    app_off = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x10000
    b = open(src, 'rb').read()
    entry, segs, end = parse_app(b, app_off)
    elf = build_elf(entry, segs)
    open(dst, 'wb').write(elf)
    print(f"✅ {dst}  {len(elf)/1024/1024:.2f} MB")
    print(f"   entry=0x{entry:08x}  段数={len(segs)}  app 数据到文件 0x{end:x}")
    for i, (a, l, _) in enumerate(segs):
        print(f"   seg{i}: load=0x{a:08x} len=0x{l:06x}")

if __name__ == '__main__':
    main()

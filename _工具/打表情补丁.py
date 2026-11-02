# -*- coding: utf-8 -*-
"""把生成的新表情（140×130 PNG）打进鲸语(09)固件的 6 个 LVGL 图像槽。

用法：
  python3 打表情补丁.py <09固件.bin> <输出.bin> <槽号=PNG> [<槽号=PNG> ...]
例：
  python3 打表情补丁.py 09.bin out.bin 3=开心.png 4=害羞.png 5=惊讶.png 6=困倦.png

槽号 1..6 对应固件里 6 个描述符（按文件偏移从低到高）。
注意：鲸语固件是 **BGR565**（解包脚本实测），这里按 BGR565 写。
"""
import struct, sys, os
from PIL import Image

DESC_OFFS = [0x02e81c, 0x037668, 0x0404b4, 0x049300, 0x05214c, 0x05af98]


def find_slots(b):
    slots = []
    for off in DESC_OFFS:
        cf = b[off + 1]
        w, h, stride, _ = struct.unpack_from('<HHHH', b, off + 4)
        size, ptr = struct.unpack_from('<II', b, off + 12)
        slots.append({'dsc': off, 'w': w, 'h': h, 'cf': cf, 'stride': stride,
                      'size': size, 'data_off': ptr - 0x3c120000})
    return slots


def to_bgr565(png, w, h):
    im = Image.open(png).convert('RGB').resize((w, h), Image.LANCZOS)
    out = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b = im.getpixel((x, y))
            # 鲸语存的是「低 5 位=R、高 5 位=B」的 565，小端
            v = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
            out += bytes((v & 0xFF, (v >> 8) & 0xFF))
    return bytes(out)


def main():
    if len(sys.argv) < 4:
        print(__doc__); return 4
    src, dst = sys.argv[1], sys.argv[2]
    b = bytearray(open(src, 'rb').read())
    slots = find_slots(b)
    print('槽位：')
    for i, s in enumerate(slots, 1):
        print('  #%d %dx%d size=%d data@0x%06x' % (i, s['w'], s['h'], s['size'], s['data_off']))
    for arg in sys.argv[3:]:
        k, png = arg.split('=', 1)
        i = int(k) - 1
        s = slots[i]
        px = to_bgr565(png, s['w'], s['h'])
        if len(px) != s['size']:
            print('!! 大小不对 %s: %d != %d' % (png, len(px), s['size'])); return 5
        b[s['data_off']:s['data_off'] + s['size']] = px
        print('  槽#%d ← %s' % (i + 1, os.path.basename(png)))
    open(dst, 'wb').write(b)
    print('写出', dst, len(b), '字节')
    return 0


if __name__ == '__main__':
    sys.exit(main())

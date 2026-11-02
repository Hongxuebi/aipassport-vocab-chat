# -*- coding: utf-8 -*-
"""修 ESP 应用镜像的 checksum + 追加 SHA256（改过镜像里的资源后必须跑）。

用法：
  python3 修镜像校验.py <固件.bin> [--app 0x10000]

规则（2026-10-28 在鲸语 09 上实测出来）：
  · 镜像从 app 偏移开始，头 24 字节
  · 6 个段之后是 padding，checksum 字节落在 (pos-app)%16==15 的位置
  · checksum = 0xEF ^ XOR(镜像[app+24 : pos])
  · hash_appended=1 时，后面跟 32 字节 SHA256 = sha256(镜像[app : pos+1])
"""
import hashlib, struct, sys


def fix(path, app=0x10000):
    b = bytearray(open(path, 'rb').read())
    assert b[app] == 0xE9, '不是 ESP 镜像：magic=%02x' % b[app]
    segs = b[app + 1]
    p = app + 24
    for _ in range(segs):
        _addr, ln = struct.unpack_from('<II', b, p)
        p += 8 + ln
    data_end = p
    pos = data_end
    while (pos - app) % 16 != 15:
        pos += 1
    c = 0xEF
    for x in b[app + 24:pos]:
        c ^= x
    old = b[pos]
    b[pos] = c
    h = hashlib.sha256(bytes(b[app:pos + 1])).digest()
    b[pos + 1:pos + 33] = h
    open(path, 'wb').write(b)
    print('checksum %02x -> %02x @%#x ；SHA256 已重算 %s' % (old, c, pos, h.hex()[:16]))
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(4)
    app = 0x10000
    if '--app' in sys.argv:
        app = int(sys.argv[sys.argv.index('--app') + 1], 0)
    sys.exit(fix(sys.argv[1], app))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打单文件.py —— 把一个 HTML 里的本地图片内联成 data URI，产出可随手发人的单文件版。

为什么有它：`网页体验/` 里的预览都靠相对路径引图，**发给手机 / 第二台设备 / 群里就裂了**。
（2026-10-29 做「大肥鱼-表情台」时又手动 base64 了一遍；这活儿不该每次现写。）

用法：
  python3 _工具/打单文件.py 网页体验/大肥鱼-表情台.html
      → 同目录生成 大肥鱼-表达式-单文件.html
  python3 _工具/打单文件.py a.html -o /tmp/a.html

规则：
  · 只处理 <img src="..."> 和 CSS url(...) 里的**本地相对/绝对路径**
  · http(s):// 与 data: 原样不动
  · 找不到的文件只告警、不中断（留着相对路径，肉眼能看出来哪张裂了）
"""
import argparse
import base64
import mimetypes
import os
import re
import sys

IMG_TAG = re.compile(r'src="([^"]+)"')
# 页面里写 /* INLINE:DIR <目录> */ 紧跟着 const FACE_IMG = {};  ⇒ 把该目录下所有图片打成 {文件名: dataURI}
INLINE_DIR = re.compile(r'/\* INLINE:DIR (\S+) \*/\s*\nconst FACE_IMG = \{\};')
CSS_URL = re.compile(r'url\((["\']?)([^)"\']+)\1\)')


def is_local(u: str) -> bool:
    return not (u.startswith('http://') or u.startswith('https://')
                or u.startswith('data:') or u.startswith('#') or u.startswith('//'))


def data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    with open(path, 'rb') as f:
        b = base64.b64encode(f.read()).decode('ascii')
    return f'data:{mime};base64,{b}'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('html')
    ap.add_argument('-o', '--out')
    a = ap.parse_args()

    src = os.path.abspath(a.html)
    root = os.path.dirname(src)
    out = a.out or os.path.join(root, os.path.splitext(os.path.basename(src))[0] + '-单文件.html')

    text = open(src, encoding='utf-8').read()
    cache, miss, done = {}, [], 0

    def repl(u: str) -> str:
        nonlocal done
        if '${' in u:            # JS 模板字符串，静态内联管不了，交给页面自己算
            return u
        if not is_local(u) or u in cache:
            return cache.get(u, u)
        p = os.path.normpath(os.path.join(root, u))
        if not os.path.isfile(p):
            miss.append(u)
            return u
        cache[u] = data_uri(p)
        done += 1
        return cache[u]

    def dir_map(m):
        d = os.path.normpath(os.path.join(root, m.group(1)))
        items = {}
        if os.path.isdir(d):
            for n in sorted(os.listdir(d)):
                if n.lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                    items[n] = data_uri(os.path.join(d, n))
                    done_ink[0] += 1
        else:
            print(f'[warn] INLINE:DIR 不存在：{d}')
        import json
        return ('/* INLINE:DIR 已内联 */\nconst FACE_IMG = '
                + json.dumps(items, ensure_ascii=False) + ';')

    done_ink = [0]
    text = INLINE_DIR.sub(dir_map, text)
    text = IMG_TAG.sub(lambda m: f'src="{repl(m.group(1))}"', text)
    text = CSS_URL.sub(lambda m: f'url({m.group(1)}{repl(m.group(2))}{m.group(1)})', text)

    open(out, 'w', encoding='utf-8').write(text)
    print(f'[ok] {out}')
    print(f'     内联 {done} 个资源，输出 {os.path.getsize(out):,} B')
    if miss:
        print(f'[warn] {len(miss)} 个找不到（保留原路径）：')
        for u in miss[:10]:
            print('   ', u)
    return 0


if __name__ == '__main__':
    sys.exit(main())

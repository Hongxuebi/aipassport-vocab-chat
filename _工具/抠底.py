#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抠底.py —— 把白底立绘抠成"直接长在页面底色上"的一张图（给设备用，不带 alpha）。

为什么有它：AI 出图是**白底**（拍证件照那种），设备页底是 `#05090F` 深空，
白底一上屏就是一块白板，跟鲸语固件那种"她直接长在黑底上"完全两个味道。

做法（四步，每步都有原因）：
  1) **按颜色全局取样**（边框一圈中位色，色差 ≤ tol 算背景）+ **只留最大连通块**：
     白底图上的零散白点/毛刺是 1–4 px 的小块，不切掉就是"撒了一把盐"。
  1.5) **填内部空洞**：被人物包住的白（水手服领子/胸口）是她的，不是背景。
  2) **缩 1 px**：AI 图边缘有一圈"被白底污染的过渡像素"，不削掉就是白边。
  3) **去溢色**：白底混进边缘 ⇒ 按 C = αF + (1-α)·255 反解 F = (C-(1-α)·255)/α。这是白边的真凶。
  4) **羽化 0.7 px** 后合到页面底色（默认 `#05090F` = UI_SC_BG）。

⚠️ 底色必须和固件里页面底色**逐字节一致**，而且 `装表情.py` 用的是**截断量化**（同 LVGL 的
`lv_color_to_u16`）—— 两边都走同一条路，接缝才是 0 像素差。改这里之前先看 65 §8.1。

用法：
  python3 _工具/抠底.py 05-表情/大肥鱼-12表情/设备140x130 -o 05-表情/大肥鱼-12表情/设备140x130-融合
  python3 _工具/抠底.py <目录> --tol 6 --bg 5,9,15 --preview 拼图.png
"""
import argparse
import os
import sys
from collections import deque

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BG_DEFAULT = (5, 9, 15)          # UI_SC_BG 0x05090F


def _largest_component(fg: np.ndarray) -> np.ndarray:
    """只留最大的那块前景。白底图上零散的白点/毛刺都是 1–4 px 的小块，一刀切掉。"""
    h, w = fg.shape
    lab = np.zeros((h, w), np.int32)
    best_lab, best_size, n = 0, 0, 0
    for y in range(h):
        for x in range(w):
            if not fg[y, x] or lab[y, x]:
                continue
            n += 1
            q = deque([(x, y)])
            lab[y, x] = n
            size = 0
            while q:
                cx, cy = q.popleft()
                size += 1
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and fg[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = n
                        q.append((nx, ny))
            if size > best_size:
                best_lab, best_size = n, size
    return lab == best_lab


def _fill_holes(keep: np.ndarray) -> np.ndarray:
    """把被人物包住的"空洞"补回前景 —— 白色水手服的领子、胸口、手臂之间的白，都是她的，不是背景。"""
    h, w = keep.shape
    hole = ~keep
    seen = np.zeros((h, w), bool)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if hole[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if hole[y, x] and not seen[y, x]:
                seen[y, x] = True
                q.append((x, y))
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < w and 0 <= ny < h and hole[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                q.append((nx, ny))
    return keep | (hole & ~seen)


def cut(src: str, tol: int, bg, erode: int, feather: float, fade: int) -> Image.Image:
    im = Image.open(src).convert('RGB')
    w, h = im.size
    a = np.asarray(im).astype(np.int16)

    # 1) 背景色 = 边框一圈的中位数（每张图的底不完全一样）；色差 ≤ tol 才算背景
    border = np.concatenate([a[0, :], a[-1, :], a[:, 0], a[:, -1]])
    seed = np.median(border, axis=0)
    fg = np.abs(a - seed).max(axis=2) > tol

    # 2) 只留最大连通块（切掉零散白点）
    keep = _largest_component(fg)
    # 3) 填内部空洞（白水手服还在她身上）
    keep = _fill_holes(keep)

    alpha = keep.astype(np.float32) * 255.0
    if erode:
        alpha = np.asarray(Image.fromarray(alpha.astype(np.uint8))
                           .filter(ImageFilter.MinFilter(3))).astype(np.float32)

    # 4) 去溢色：C = αF + (1-α)·seed  ⇒  F = (C-(1-α)·seed)/α
    al = np.clip(alpha / 255.0, 1e-3, 1.0)[..., None]
    F = np.clip((a.astype(np.float32) - (1.0 - al) * seed[None, None, :]) / al, 0, 255)

    alpha = np.asarray(Image.fromarray(alpha.astype(np.uint8))
                       .filter(ImageFilter.GaussianBlur(feather))).astype(np.float32) / 255.0

    if fade:                                    # 底部渐隐（她若压到底边，别被一刀切）
        ramp = np.ones(h, np.float32)
        for i in range(min(fade, h)):
            ramp[h - fade + i] = 1.0 - (i + 1.0) / fade
        alpha = alpha * ramp[:, None]

    out = F * alpha[..., None] + np.array(bg, np.float32) * (1 - alpha[..., None])
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('-o', '--out', required=True)
    ap.add_argument('--tol', type=int, default=4)
    ap.add_argument('--erode', type=int, default=1)
    ap.add_argument('--feather', type=float, default=0.7)
    ap.add_argument('--fade', type=int, default=0)
    ap.add_argument('--bg', default=','.join(map(str, BG_DEFAULT)))
    ap.add_argument('--preview', default='')
    a = ap.parse_args()

    bg = tuple(int(v) for v in a.bg.split(','))
    os.makedirs(a.out, exist_ok=True)

    files = sorted(f for f in os.listdir(a.src) if f.lower().endswith('.png'))
    if not files:
        print('!! 目录里没有 png'); return 2

    outs = []
    for f in files:
        im = cut(os.path.join(a.src, f), a.tol, bg, a.erode, a.feather, a.fade)
        p = os.path.join(a.out, f)
        im.save(p)
        outs.append((f, im))
        print(f'  {f}  →  {p}')

    if a.preview:
        w, h = outs[0][1].size
        n = len(outs)
        sheet = Image.new('RGB', (w * n, h + 18), bg)
        d = ImageDraw.Draw(sheet)
        for i, (f, im) in enumerate(outs):
            sheet.paste(im, (i * w, 18))
            d.text((i * w + 4, 4), f'{i:02d}', fill=(150, 210, 230))
        sheet.save(a.preview)
        print(f'拼图: {a.preview}')

    print(f'✅ {len(files)} 张，底色 {bg}，tol={a.tol} erode={a.erode} feather={a.feather}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

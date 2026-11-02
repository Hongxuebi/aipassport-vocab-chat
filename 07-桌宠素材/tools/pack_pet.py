#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pack_pet.py —— 大肥鱼桌宠：webm(VP9-alpha) → ESP32 可用的索引色帧包

管线：
  ffmpeg 解 alpha 帧 → 全动作并集包围盒裁切 → 等比缩放贴到方形精灵画布
  → 全局 255 色调色板(索引 0 专供透明) → PackBits 逐帧压缩 → 打包成 .bin

产物：
  pet_assets.bin   直接 EMBED 进固件 .rodata，运行时零拷贝、按名字查动作
  pet_assets.h     给 C 侧一个符号声明

用法：
  python pack_pet.py --src <assets/webm> --out <outdir> [--size 128] [--fps 12]
                     [--max-frames 20] [--only "涮火锅,荡秋千"] [--all]
"""
import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

try:                       # 控制台是 GBK 时中文别乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAGIC = b"PET1"
VERSION = 1
ALPHA_CUT = 128          # 低于它的像素判为透明


# ---------------------------------------------------------------- ffmpeg 抽帧
def extract_frames(webm: Path, fps: float, tmp: Path):
    """
    返回 RGBA ndarray 列表。

    这两个参数是必须的，缺一个 alpha 就全变 255：
      -c:v libvpx-vp9      ffmpeg 内置的 vp9 解码器不认 WebM 的 BlockAdditional alpha
      -strict experimental  libvpx 的 alpha 输出格式是 gbrap，不加 strict 会直接拒绝
    """
    for f in tmp.glob("*.png"):
        f.unlink()
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-c:v", "libvpx-vp9", "-strict", "experimental",
        "-i", str(webm),
        "-vf", f"fps={fps}",
        "-pix_fmt", "rgba",
        str(tmp / "%04d.png"),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败 {webm.name}: {r.stderr.decode('utf-8', 'ignore')[:400]}")
    frames = []
    for p in sorted(tmp.glob("*.png")):
        im = Image.open(p).convert("RGBA")
        frames.append(np.array(im, dtype=np.uint8))
    return frames


def subject_bbox(frames, lo=8.0, hi=92.0, pad=4):
    """
    主体包围盒：逐轴取分位数，而不是全帧并集。

    并集会跟着"幅度最大那一帧"膨胀（放烟花、被落叶淹没），结果每个动作各自撑满画布，
    角色大小在动作之间忽大忽小。取 8%~92% 分位能留住绝大多数帧的完整轮廓，
    只裁掉极端飞出去的手臂/特效。
    """
    xs0, ys0, xs1, ys1 = [], [], [], []
    for f in frames:
        a = f[:, :, 3] > 8
        if not a.any():
            continue
        ys, xs = np.where(a)
        xs0.append(int(xs.min())); xs1.append(int(xs.max()) + 1)
        ys0.append(int(ys.min())); ys1.append(int(ys.max()) + 1)
    if not xs0:
        h, w = frames[0].shape[:2]
        return (0, 0, w, h)
    x0 = max(0, int(np.percentile(xs0, lo)) - pad)
    y0 = max(0, int(np.percentile(ys0, lo)) - pad)
    x1 = int(np.percentile(xs1, hi)) + pad
    y1 = int(np.percentile(ys1, hi)) + pad
    if x1 <= x0: x1 = x0 + 1
    if y1 <= y0: y1 = y0 + 1
    return (x0, y0, x1, y1)


def fit_uniform(frame, bbox, size, scale):
    """
    用**全局统一比例**缩放后贴进 size×size 画布，底部对齐、水平居中。

    scale 由全部动作主体高度的中位数算出，不按每段自己的 bbox 撑满 ——
    否则幅度小的动作角色巨大、幅度大的动作角色袖珍。
    超出画布的部分居中裁掉（留脚底，裁头顶/两侧）。
    """
    x0, y0, x1, y1 = bbox
    crop = Image.fromarray(frame[y0:y1, x0:x1], "RGBA")
    w, h = crop.size
    if w <= 0 or h <= 0:
        return np.zeros((size, size, 4), np.uint8)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    crop = crop.resize((nw, nh), Image.LANCZOS)
    if nw > size or nh > size:
        left = max(0, (nw - size) // 2)
        top = max(0, nh - size)          # 底部对齐：多出来的从头顶裁
        crop = crop.crop((left, top, left + min(nw, size), top + min(nh, size)))
        nw, nh = crop.size
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(crop, ((size - nw) // 2, size - nh), crop)
    return np.array(canvas, dtype=np.uint8)


# ---------------------------------------------------------------- 调色板
def palette_picks(square_frames, cap=20000):
    """从若干帧里采不透明像素，供全局调色板使用。"""
    picks = []
    step = max(1, len(square_frames) // 8)
    for f in square_frames[::step]:
        rgb = f[:, :, :3]
        a = f[:, :, 3] > ALPHA_CUT
        if not a.any():
            continue
        px = rgb[a]
        if len(px) > cap:
            px = px[np.random.default_rng(1).choice(len(px), cap, replace=False)]
        picks.append(px)
    return picks


def build_global_palette(px, n=255):
    """把收集好的像素样本中位切分成 n 色调色板。"""
    if px is None or len(px) == 0:
        return np.zeros((n, 3), np.uint8)
    if len(px) > 200_000:
        idx = np.random.default_rng(0).choice(len(px), 200_000, replace=False)
        px = px[idx]
    im = Image.fromarray(px.reshape(-1, 1, 3).astype(np.uint8), "RGB")
    q = im.quantize(colors=n, method=Image.MEDIANCUT, dither=Image.NONE)
    pal = np.array(q.getpalette()[: n * 3], np.uint8).reshape(-1, 3)
    if len(pal) < n:
        pad = np.zeros((n - len(pal), 3), np.uint8)
        pal = np.vstack([pal, pad])
    return pal


def quantize_frames(square_frames, gpal):
    """
    每帧先用中位切分自适应量化，再把它的局部调色板逐色映射到全局调色板。
    索引 0 留给透明，真实颜色用 1..255。
    """
    out = []
    n = len(gpal)
    gp = gpal.astype(np.int32)
    for f in square_frames:
        rgb = f[:, :, :3]
        alpha = f[:, :, 3]
        im = Image.fromarray(rgb, "RGB")
        q = im.quantize(colors=min(n, 255), method=Image.MEDIANCUT, dither=Image.NONE)
        local = np.array(q.getpalette()[:255 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        idx = np.array(q, dtype=np.uint8)
        # 局部色 → 全局色 的最近邻查表
        d = ((local[:, None, :] - gp[None, :, :]) ** 2).sum(axis=2)
        near = d.argmin(axis=1).astype(np.uint8) + 1     # 1..255
        lut = np.zeros(256, np.uint8)
        lut[: len(near)] = near
        res = lut[idx]
        res[alpha < ALPHA_CUT] = 0
        out.append(res)
    return out


# ---------------------------------------------------------------- PackBits
def packbits(data: np.ndarray) -> bytes:
    """标准 PackBits：0x00-0x7F = 重复 n+1 次；0x81-0xFF = 后面 n-127 个字面量。"""
    a = data.reshape(-1).astype(np.uint8)
    n = len(a)
    out = bytearray()
    i = 0
    while i < n:
        # 找重复游程
        j = i + 1
        while j < n and j - i < 128 and a[j] == a[i]:
            j += 1
        run = j - i
        if run >= 2:
            out.append(run - 1)
            out.append(int(a[i]))
            i = j
            continue
        # 字面量游程
        j = i
        lit = bytearray()
        while j < n and len(lit) < 128:
            if j + 1 < n and a[j] == a[j + 1]:
                break
            lit.append(int(a[j]))
            j += 1
        if not lit:
            lit.append(int(a[i])); j = i + 1
        out.append(0x80 | (len(lit) - 1))
        out.extend(lit)
        i = j
    return bytes(out)


def rgb565(c):
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# ---------------------------------------------------------------- 打包
def pack_assets(src: Path, out: Path, size: int, fps: float, max_frames: int,
                only=None, limit=None, exclude=None):
    webms = sorted(src.glob("*.webm"), key=lambda p: p.stem)
    if exclude:
        ex = {s.strip() for s in exclude if s.strip()}
        webms = [p for p in webms if p.stem not in ex]
    if only:
        want = [s.strip() for s in only.split(",") if s.strip()]
        webms = [p for p in webms if p.stem in want]
    if limit:
        webms = webms[:limit]
    if not webms:
        print("没找到 webm", file=sys.stderr)
        return None

    tmp = Path(tempfile.mkdtemp(prefix="petframes_"))
    anims = []
    total_raw = 0
    total_packed = 0
    try:
        # ---------- pass 1：抽帧 + 并集包围盒 + 收集调色板样本 ----------
        print("[pass 1/3] 抽帧、算主体包围盒")
        plans = {}
        picks_all = []
        heights = []
        for webm in webms:
            raw_frames = extract_frames(webm, fps, tmp)
            if not raw_frames:
                print(f"  [skip] {webm.stem} 抽不到帧", file=sys.stderr)
                continue
            if len(raw_frames) > max_frames:
                pick = np.linspace(0, len(raw_frames) - 1, max_frames).round().astype(int)
                picked = [raw_frames[i] for i in pick]
            else:
                picked = raw_frames
            bbox = subject_bbox(picked)
            heights.append(bbox[3] - bbox[1])
            # 调色板样本直接取自原始帧：缩放只改像素分布，不改色域
            step = max(1, len(picked) // 4)
            for f in picked[::step]:
                a = f[:, :, 3] > ALPHA_CUT
                if not a.any():
                    continue
                px = f[:, :, :3][a]
                if len(px) > 8000:
                    px = px[np.random.default_rng(2).choice(len(px), 8000, replace=False)]
                picks_all.append(px)
            plans[webm.stem] = {"bbox": bbox, "n": len(picked)}
        if not plans:
            return None

        # 全局统一比例：所有动作主体高度的中位数 → 让它铺满 sprite
        h_ref = float(np.median(heights)) if heights else float(size)
        scale = size / max(1.0, h_ref)
        print(f"[pass 2/3] 统一缩放比例：主体高度中位数 {h_ref:.0f}px → scale {scale:.3f}")

        gpal = build_global_palette(
            np.concatenate(picks_all, axis=0) if picks_all else None)
        print(f"  全局调色板：{len(gpal)} 色（索引 0 留空给透明）")

        # ---------- pass 3：统一比例缩放 + 统一调色板量化 + PackBits ----------
        print("[pass 3/3] 统一比例缩放、量化、压缩")
        for k, webm in enumerate(webms, 1):
            if webm.stem not in plans:
                continue
            bbox = plans[webm.stem]["bbox"]
            raw_frames = extract_frames(webm, fps, tmp)
            if len(raw_frames) > max_frames:
                pick = np.linspace(0, len(raw_frames) - 1, max_frames).round().astype(int)
                raw_frames = [raw_frames[i] for i in pick]
            squares = [fit_uniform(f, bbox, size, scale) for f in raw_frames]
            idx_frames = quantize_frames(squares, gpal)
            # 不做帧间差分：实测 XOR 后角色内部变随机值，PackBits 反而更差（59.2 vs 56.6 KB/动作）
            packed = [packbits(f) for f in idx_frames]
            anims.append({
                "name": webm.stem,
                "frames": packed,
                "count": len(packed),
                "raw": size * size * len(packed),
                "packed_bytes": sum(len(p) for p in packed),
                "bbox": bbox,
            })
            total_raw += size * size * len(packed)
            total_packed += sum(len(p) for p in packed)
            avg = sum(len(p) for p in packed) / max(1, len(packed))
            print(f"  [{k}/{len(webms)}] {webm.stem}: {len(packed)} 帧, "
                  f"平均 {avg/1024:.1f} KB/帧, 合计 {sum(len(p) for p in packed)/1024:.0f} KB")
        if not anims:
            return None

        # 全部动作共用这一份全局调色板；索引 0 = 透明，1..255 = 颜色
        pal = gpal
        full_pal = np.vstack([np.zeros((1, 3), np.uint8), pal])   # 256 项
        assert len(full_pal) == 256, len(full_pal)

        # ---- 组装二进制 ----
        head = bytearray()
        head += MAGIC
        head += struct.pack("<BHHH", VERSION, 256, size, size)
        table = bytearray()
        blob = bytearray()
        # 先算数据区起点：头 + 调色板 + 动作数 + 每动作目录
        dir_size = 0
        for a in anims:
            nb = a["name"].encode("utf-8")
            dir_size += 1 + len(nb) + 2 + 2 + 1 + 4 * a["count"]
        data_off = len(head) + 256 * 2 + 2 + dir_size

        for a in anims:
            nb = a["name"].encode("utf-8")
            table += struct.pack("<B", len(nb)) + nb
            table += struct.pack("<HHB", a["count"], int(fps), 0)   # count, fps, reserve
            for i, p in enumerate(a["frames"]):
                off = data_off + len(blob)
                table += struct.pack("<I", off)
                blob += struct.pack("<H", len(p))
                blob += p
        out_bin = head + b"".join(struct.pack("<H", rgb565(c)) for c in full_pal) \
            + struct.pack("<H", len(anims)) + bytes(table) + bytes(blob)

        out.mkdir(parents=True, exist_ok=True)
        (out / "pet_assets.bin").write_bytes(out_bin)
        hdr = (
            "// 由 pack_pet.py 生成，勿手改\n"
            "#pragma once\n#include <stdint.h>\n"
            'extern const uint8_t pet_assets_bin_start[] asm("_binary_pet_assets_bin_start");\n'
            'extern const uint8_t pet_assets_bin_end[]   asm("_binary_pet_assets_bin_end");\n'
        )
        (out / "pet_assets.h").write_text(hdr, encoding="utf-8")

        report = {
            "sprite": [size, size],
            "fps_sampled": fps,
            "max_frames": max_frames,
            "anim_count": len(anims),
            "bin_bytes": len(out_bin),
            "raw_bytes": total_raw,
            "packed_bytes": total_packed,
            "ratio": round(total_raw / max(1, total_packed), 2),
            "per_anim_kb": [round(a["packed_bytes"] / 1024, 1) for a in anims],
            "names": [a["name"] for a in anims],
        }
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        print(f"\n总打包 {len(out_bin)/1024:.0f} KB  ({len(anims)} 个动作, "
              f"压缩比 {report['ratio']}:1)")
        return report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=r"H:\AI工具\dafeiyu-pet\assets\webm")
    ap.add_argument("--out", default=r"H:\AI工具\dafeiyu-pet\_esp32\out")
    ap.add_argument("--size", type=int, default=128, help="精灵正方形边长 px")
    ap.add_argument("--fps", type=float, default=12.0, help="抽帧帧率")
    ap.add_argument("--max-frames", type=int, default=20, help="单动作最多保留帧数")
    ap.add_argument("--only", default=None, help="只处理这些动作(逗号分隔)")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 个动作(按名字排序)")
    ap.add_argument("--exclude", default=None, help="排除这些动作(逗号分隔)")
    ap.add_argument("--exclude-file", default=None, help="排除清单文件，每行一个名字，# 注释")
    a = ap.parse_args()

    ex = []
    if a.exclude:
        ex += [s.strip() for s in a.exclude.split(",") if s.strip()]
    if a.exclude_file:
        for line in Path(a.exclude_file).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ex.append(line)
    if ex:
        print(f"排除 {len(ex)} 个动作")
    r = pack_assets(Path(a.src), Path(a.out), a.size, a.fps, a.max_frames,
                    a.only, a.limit, ex)
    if r:
        print(json.dumps({k: v for k, v in r.items() if k not in ("names",)},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pack_boot.py —— 把一段视频做成 ESP32 的开机画面帧包。

产物刻意沿用 pet_assets.bin 的 PET1 格式（sprite = 屏幕尺寸，一个名为 "boot" 的动作），
这样固件那边的 pet_anim_decode() 不用改一行就能播开机画面。

和桌宠精灵的区别：
  · 精灵是 96×96 带透明，开机画面是 240×320 **满屏不透明**
  · 因为满屏，C3 上不能整帧进 RAM（240×320×2 = 150 KB），必须分块流式刷屏，见 README

用法：
  python pack_boot.py --video <xxx.mp4> --crop "1180,211,1664,856" \
      --frames 48 --out <outdir>
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pack_pet import packbits, build_global_palette, rgb565, ALPHA_CUT  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAGIC = b"PET1"
VERSION = 1
ANIM_NAME = "boot"


def extract(video: Path, tmp: Path):
    for f in tmp.glob("*.png"):
        f.unlink()
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video),
           "-pix_fmt", "rgb24", str(tmp / "%04d.png")]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore")[:400])
    return sorted(tmp.glob("*.png"))


def quantize_frames(frames_rgb, gpal):
    """和桌宠同一套：逐帧自适应量化 → 映射到全局调色板，索引 0 留空。"""
    out = []
    gp = gpal.astype(np.int32)
    for f in frames_rgb:
        im = Image.fromarray(f, "RGB")
        q = im.quantize(colors=min(len(gp), 255), method=Image.MEDIANCUT, dither=Image.NONE)
        local = np.array(q.getpalette()[:255 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        idx = np.array(q, dtype=np.uint8)
        d = ((local[:, None, :] - gp[None, :, :]) ** 2).sum(axis=2)
        near = d.argmin(axis=1).astype(np.uint8) + 1
        lut = np.zeros(256, np.uint8)
        lut[: len(near)] = near
        out.append(lut[idx])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--crop", required=True, help="left,top,right,bottom（源视频像素）")
    ap.add_argument("--w", type=int, default=240)
    ap.add_argument("--h", type=int, default=320)
    ap.add_argument("--frames", type=int, default=48, help="总共保留多少帧")
    ap.add_argument("--fps", type=float, default=0,
                    help="播放帧率；0 = 铺满 8 秒（帧数/8）")
    a = ap.parse_args()

    x0, y0, x1, y1 = [int(v) for v in a.crop.split(",")]
    tmp = Path(tempfile.mkdtemp(prefix="bootframes_"))
    try:
        files = extract(Path(a.video), tmp)
        n = len(files)
        print(f"源视频 {n} 帧，抽到 {a.frames} 帧")
        if n == 0:
            raise SystemExit("抽不到帧")
        pick = (np.linspace(0, n - 1, a.frames).round().astype(int)
                if n > a.frames else np.arange(n))

        frames = []
        for i in pick:
            im = Image.open(files[int(i)]).convert("RGB").crop((x0, y0, x1, y1))
            frames.append(np.array(im.resize((a.w, a.h), Image.LANCZOS), dtype=np.uint8))
        print(f"裁切 {x1-x0}×{y1-y0} → {a.w}×{a.h}")

        # 全局调色板：从所有帧采样
        picks = []
        rng = np.random.default_rng(3)
        for f in frames:
            px = f.reshape(-1, 3)
            if len(px) > 12000:
                px = px[rng.choice(len(px), 12000, replace=False)]
            picks.append(px)
        gpal = build_global_palette(np.concatenate(picks, axis=0))
        print(f"全局调色板 {len(gpal)} 色")

        idx_frames = quantize_frames(frames, gpal)
        packed = [packbits(f) for f in idx_frames]
        sizes = [len(p) for p in packed]
        print(f"平均 {sum(sizes)/len(sizes)/1024:.1f} KB/帧，"
              f"合计 {sum(sizes)/1024:.0f} KB，最大 {max(sizes)/1024:.1f} KB")

        full_pal = np.vstack([np.zeros((1, 3), np.uint8), gpal])
        assert len(full_pal) == 256

        head = bytearray()
        head += MAGIC
        head += struct.pack("<BHHH", VERSION, 256, a.w, a.h)
        nb = ANIM_NAME.encode("utf-8")
        dir_size = 1 + len(nb) + 2 + 2 + 1 + 4 * len(packed)
        data_off = len(head) + 256 * 2 + 2 + dir_size

        table = bytearray()
        blob = bytearray()
        table += struct.pack("<B", len(nb)) + nb
        play_fps = a.fps if a.fps > 0 else round(len(packed) / 8.0)
        table += struct.pack("<HHB", len(packed), int(play_fps), 0)
        for p in packed:
            table += struct.pack("<I", data_off + len(blob))
            blob += struct.pack("<H", len(p))
            blob += p

        out_bin = head + b"".join(struct.pack("<H", rgb565(c)) for c in full_pal) \
            + struct.pack("<H", 1) + bytes(table) + bytes(blob)

        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "boot_assets.bin").write_bytes(out_bin)
        rep = {
            "sprite": [a.w, a.h],
            "frames": len(packed),
            "bin_bytes": len(out_bin),
            "avg_kb": round(sum(sizes) / len(sizes) / 1024, 2),
            "total_kb": round(sum(sizes) / 1024, 1),
            "crop": [x0, y0, x1, y1],
            "fps": int(play_fps),
            "play_seconds": round(len(packed) / float(play_fps), 1),
            "source_seconds": 8.0,
        }
        (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
        print(f"\n总打包 {len(out_bin)/1024:.0f} KB / {len(packed)} 帧 "
              f"→ {out/'boot_assets.bin'}")
        print(f"播放 {rep['fps']} fps，共 {rep['play_seconds']} 秒"
              f"（源片 {rep['source_seconds']} 秒）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

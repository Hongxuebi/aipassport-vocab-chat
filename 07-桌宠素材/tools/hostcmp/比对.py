#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
帧比对：C 解码器（真固件源码编出来的） ↔ Python 解码器（verify_pet.py 参考实现）

判定口径（作者定的）：
  · 逐字节一致  → 解码没问题，锅在渲染/缓冲复用
  · 整体差一次字节交换 → 修 decode 的输出字节序
  · 差零星几个字节 → 报偏移，回来再查

两种 C 跑法都要看：
  safe —— 把动作名复制成 NUL 结尾再解（= 算法本身）
  as-is —— 完全按固件调用（名字直接指 Flash 目录项）。若它返回非 0，说明
           pet_anim 的动作名生命周期有问题，帧根本解不出来，跟字节序无关。

用法：
  python3 比对.py            # 项目里那两份真实素材，第 0 帧
  python3 比对.py --all      # 再加：全部帧逐帧比
  python3 比对.py --bin X --anim boot --frame 3 --transp 0
"""
import argparse
import struct
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
sys.path.insert(0, str(TOOLS))
from verify_pet import parse, unpackbits  # noqa: E402  解码语义只认这一份参考实现

WORK = Path("/tmp/petdiff")
ROOT = TOOLS.parent


def python_frame(bin_path: Path, which: str, frame: int, transp: int):
    """参考实现：解出 RGB565 原生字节（小端，与 C 的 native 写一致）。"""
    p = parse(bin_path)
    if which.startswith("#"):
        anim = p["anims"][int(which[1:])]
    else:
        anim = next((a for a in p["anims"] if a["name"] == which), None)
        if anim is None:
            raise SystemExit(f"找不到动作 {which}")
    if not (0 <= frame < anim["count"]):
        raise SystemExit(f"帧号越界：{which} 共 {anim['count']} 帧")

    w, h = p["w"], p["h"]
    off = anim["offsets"][frame]
    (ln,) = struct.unpack_from("<H", p["blob"], off)
    idx = unpackbits(p["blob"][off + 2: off + 2 + ln], w * h)
    if len(idx) < w * h:
        idx = idx + b"\x00" * (w * h - len(idx))
    idx = idx[:w * h]
    pal = p["pal"]
    raw = bytearray()
    for v in idx:
        raw += struct.pack("<H", int(pal[v]) if v else (transp & 0xFFFF))
    return w, h, bytes(raw), idx


def run_c(bin_path: Path, which: str, frame: int, transp: int, tag: str):
    exe = WORK / "dump_frame"
    if not exe.exists():
        raise SystemExit(f"缺 {exe}，先跑 编译.sh")
    pref = WORK / f"c_{tag}"
    r = subprocess.run([str(exe), str(bin_path), which, str(frame), f"{transp:X}", str(pref)],
                       capture_output=True, text=True)
    out = (r.stdout or "").strip().splitlines()
    err = None
    for line in out:
        if "as-is decode" in line:
            # 形如：  as-is decode → 258   safe decode → 0
            err = line.strip()
    safe = pref.with_name(pref.name + "_safe.rgb565")
    asis = pref.with_name(pref.name + "_asis.rgb565")
    if r.returncode != 0 and not err:
        raise SystemExit(f"C 跑挂了：{r.stderr.strip()}")
    return (safe.read_bytes() if safe.exists() else b""), \
           (asis.read_bytes() if asis.exists() else b""), err, out


def verdict(c_raw: bytes, py_raw: bytes, idx: bytes):
    if c_raw == py_raw:
        return "identical", 0
    n = min(len(c_raw), len(py_raw))
    diffs = [i for i in range(n) if c_raw[i] != py_raw[i]]
    swapped = bytes(c_raw[i ^ 1] for i in range(len(c_raw)))
    if swapped == py_raw:
        return "byte-swapped", len(diffs)
    zero_bytes = set()
    for px, v in enumerate(idx):
        if v == 0:
            zero_bytes.add(px * 2); zero_bytes.add(px * 2 + 1)
    if diffs and all(b in zero_bytes for b in diffs):
        return "zero-only", len(diffs)
    return "diff", len(diffs)


def one_case(bin_path: Path, which: str, frame: int, transp: int, tag: str, quiet=False):
    if not bin_path.exists():
        print(f"  [!] 缺文件 {bin_path}")
        return None
    w, h, py_raw, idx = python_frame(bin_path, which, frame, transp)
    c_safe, c_asis, errline, stdout = run_c(bin_path, which, frame, transp, f"{tag}_f{frame}")

    v, ndiff = verdict(c_safe, py_raw, idx)
    if not quiet:
        print(f"\n[{tag}] {bin_path.name}  动作={which} 帧={frame}  {w}x{h} / {len(py_raw)} 字节")
        print(f"  调用方式：{errline}")
        print(f"  C(算法) 前 4 像素: " + " ".join(f"{struct.unpack_from('<H', c_safe, i*2)[0]:04X}" for i in range(4)))
        print(f"  Py      前 4 像素: " + " ".join(f"{struct.unpack_from('<H', py_raw, i*2)[0]:04X}" for i in range(4)))
        if v == "identical":
            print(f"  ✅ 逐字节一致（{len(py_raw)} 字节全等）")
        elif v == "byte-swapped":
            print(f"  ⚠️ 整体差一次字节交换（{ndiff}/{len(py_raw)} 字节不同）")
        elif v == "zero-only":
            print(f"  ⚠️ 只差索引 0 像素（{ndiff} 字节），透明色语义差，不是解码差")
        else:
            print(f"  ❌ 不一致：{ndiff}/{len(py_raw)} 字节不同")

    asis_bad = (c_asis == bytes([0xEE]) * (w * h * 2))
    return {
        "tag": tag, "bin": str(bin_path), "anim": which, "frame": frame,
        "verdict": v, "diff": ndiff, "bytes": len(py_raw),
        "asis": "解不出来(缓冲区原样 0xEE)" if asis_bad else "写进去了",
        "errline": errline,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin")
    ap.add_argument("--anim", default=None)
    ap.add_argument("--frame", type=int, default=0)
    ap.add_argument("--transp", default=None)
    ap.add_argument("--all", action="store_true", help="全部帧逐帧比")
    a = ap.parse_args()

    WORK.mkdir(exist_ok=True)
    cases = []
    if a.bin:
        cases.append((Path(a.bin), a.anim or "#0", a.frame,
                      int(a.transp, 16) if a.transp else 0, "custom"))
    else:
        cases += [
            (ROOT / "boot_final/boot_assets.bin", "boot", 0, 0x0000, "boot"),
            (ROOT / "out_85/pet_assets_85.bin", "#0", 0, 0x18C4, "pet85"),
            (ROOT / "out_final/pet_assets.bin", "#0", 0, 0x0000, "petfinal"),
        ]

    print("=" * 78)
    print("C 解码器（固件源码，主机编译） ↔ Python 解码器（verify_pet.py）逐字节比对")
    print("=" * 78)

    res = []
    for b, which, fr, tr, tag in cases:
        r = one_case(b, which, fr, tr, tag)
        if r:
            res.append(r)

    if a.all:
        print("\n" + "-" * 78)
        print("全部帧逐帧比（只看结论行）")
        print("-" * 78)
        for b, which, fr, tr, tag in cases:
            if not b.exists():
                continue
            p = parse(b)
            anim = p["anims"][int(which[1:])] if which.startswith("#") else \
                next(x for x in p["anims"] if x["name"] == which)
            bad = 0
            for f in range(anim["count"]):
                r = one_case(b, which, f, tr, tag, quiet=True)
                if r and r["verdict"] not in ("identical", "zero-only"):
                    bad += 1
            print(f"  {tag:<9} {anim['name']:<10} 共 {anim['count']:>2} 帧  "
                  f"不一致 {bad} 帧  {'✅' if bad == 0 else '❌'}")

    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)
    name_map = {"identical": "✅ 逐字节一致", "byte-swapped": "⚠️ 差一次字节交换",
                "zero-only": "⚠️ 仅索引0不同", "diff": "❌ 不一致"}
    for r in res:
        print(f"  {r['tag']:<9} {name_map[r['verdict']]:<18} "
              f"diff={r['diff']:>6}/{r['bytes']}  调用真机路径：{r['asis']}")
    all_same = all(r["verdict"] == "identical" for r in res)
    print(f"\n  → 算法判定：{'两边解码逐字节一致' if all_same else '有差异，见上表'}")
    return 0 if all_same else 1


if __name__ == "__main__":
    sys.exit(main())

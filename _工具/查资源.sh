#!/bin/bash
# 资源一致性：图鉴静态图 / 动画帧 / 索引 / SPIFFS 镜像
set -u
cd "$(dirname "$0")/.."
python3 - <<'PY'
import glob, os, sys
SPR='_图鉴4bpp'; IMG='/tmp/assets_60.img'; PART=1048576
bad=0
st=sorted(glob.glob(os.path.join(SPR,'f*.bin')))
print("== 静态图（96×96 4bpp，应为 4,648 B）==")
print("  文件数 %d" % len(st))
wrong=[f for f in st if os.path.getsize(f)!=4648]
if wrong:
    print("  ❌ 大小不对 %d 个：%s" % (len(wrong), ", ".join(os.path.basename(x) for x in wrong[:3]))); bad=1
anim=sorted(glob.glob(os.path.join(SPR,'anim','f*.bin')))
print("== 动画（48×48 4bpp ×N 帧）==")
print("  文件数 %d" % len(anim))
for f in anim:
    d=open(f,'rb').read(9)
    if d[:2]!=b'DA': print("  ❌ %s 不是动画文件" % os.path.basename(f)); bad=1; break
    frames=d[6]; need=9+16*2+frames*(48*48//2)
    if os.path.getsize(f)!=need:
        print("  ❌ %s 大小 %d ≠ 期望 %d（%d 帧）" % (os.path.basename(f), os.path.getsize(f), need, frames)); bad=1; break
print("== 索引 ==")
idx=os.path.join(SPR,'index.txt')
if os.path.exists(idx):
    lines=len([l for l in open(idx,encoding='utf-8') if l.strip()])
    print("  index.txt %d 行（文件数 %d）" % (lines, len(st)))
    if lines!=len(st): print("  ❌ 行数与文件数不一致"); bad=1
else:
    print("  ❌ 缺 index.txt"); bad=1
print("== SPIFFS 镜像 ==")
if os.path.exists(IMG):
    sz=os.path.getsize(IMG); print("  %s %d B（分区 %d）" % (os.path.basename(IMG), sz, PART))
    if sz!=PART: print("  ❌ 镜像大小必须等于分区"); bad=1
    used=sum(os.path.getsize(f) for f in glob.glob(os.path.join('/tmp/spiffs_in','**','*'),recursive=True) if os.path.isfile(f))
    print("  内容合计 %.0f KB（SPIFFS 实际占用会比这个大 ~10-15%%）" % (used/1024))
else:
    print("  ⏭  还没生成镜像")
print()
print("✅ 资源一致性通过" if not bad else "❌ 资源检查失败")
sys.exit(1 if bad else 0)
PY

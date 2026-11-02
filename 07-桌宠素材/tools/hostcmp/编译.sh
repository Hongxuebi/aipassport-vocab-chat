#!/bin/bash
# 编译主机侧帧比对器：把固件里那份 pet_anim.c 原样编进去（不是照抄算法）
# 产物：/tmp/petdiff/dump_frame
set -e
cd "$(dirname "$0")"
FIRM=../../../_参考仓库/folotoy-ai-passport
[ -f "$FIRM/components/pet/pet_anim.c" ] || { echo "❌ 找不到固件工作副本：$FIRM"; exit 8; }
mkdir -p /tmp/petdiff
# -include stdbool.h：ESP-IDF 的编译单元里有隐式 stdbool，主机上没有
# -DPET_ANIM_C：让 dump_frame.c include 的正是固件工作副本那一份
cc -O1 -std=gnu11 -Wall -Wextra -include stdbool.h \
   -I stub -I "$FIRM/components/pet/include" \
   -DPET_ANIM_C="\"$PWD/$FIRM/components/pet/pet_anim.c\"" \
   dump_frame.c -o /tmp/petdiff/dump_frame
echo "✅ 编译通过：/tmp/petdiff/dump_frame"
echo "   源码：$FIRM/components/pet/pet_anim.c  ($(shasum -a 1 "$FIRM/components/pet/pet_anim.c" | cut -c1-12))"

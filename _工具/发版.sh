#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 发版.sh —— 一条命令把"编译 → 补丁 → 闸 → 发布包 → SHA → 提交 → 打标签"走完
#
# 为什么要它（2026-10-29 夜，一晚上发了四版）：每版我都是手敲 8~10 条命令，
# 其中**三处最容易漏**：① 忘了重生成补丁（闸第 7 项必红）② 发布包漏了 SHA256SUMS 或 faces.bin
# ③ 标签打在 amend 之前的旧 commit 上（v0.94 就差点这样）。全收进来就只有一条命令。
#
# 用法：
#   ./_工具/发版.sh v0.95-xxx "一句话说明（会用于 git 提交与标签）"
#   ./_工具/发版.sh v0.95-xxx "说明" --no-flash      # 只编译不刷（设备不在）
#   ./_工具/发版.sh v0.95-xxx "说明" --draft         # 走到"发布包"就停，不提交不打标签
#
# 前置（脚本会自己挡）：
#   · 串口空闲（没有 rawlog / 桌宠调试台 / esptool 在跑）—— 串口同一时刻只许一个进程
#   · main/app_config.h 里 APP_* 开关全 0（否则闸第 7 项必红，不如提前说）
set -u
cd "$(dirname "$0")/.."
ROOT="$PWD"
case "$AIP_FW" in /*) FW="$AIP_FW" ;; *) FW="$ROOT/$AIP_FW" ;; esac
PATCH="$ROOT/03-固件补丁/0001-wprov-扫码配网.patch"
UP="a06235e"

VER="${1:-}"
MSG="${2:-}"
[ -n "$VER" ] && [ -n "$MSG" ] || {
  echo "用法：./_工具/发版.sh <版本号> <一句话说明> [--no-flash] [--draft]"; exit 2; }
shift 2
NOFLASH=0; DRAFT=0
for a in "$@"; do
  case "$a" in
    --no-flash) NOFLASH=1 ;;
    --draft)    DRAFT=1 ;;
    *) echo "❌ 不认识的参数：$a"; exit 2 ;;
  esac
done

say() { printf "\n\033[1m== %s ==\033[0m\n" "$1"; }
die() { printf "❌ %s\n" "$1"; exit 1; }

say "0) 前置检查"
git rev-parse --git-dir >/dev/null 2>&1 || die "不是 git 仓库"
ps aux | grep -E "rawlog|桌宠调试台|esptool|idf_monitor" | grep -v grep >/dev/null \
  && die "串口有进程在占用（rawlog/调试台/esptool）—— 先关掉再发版"
grep -nE "^#define APP_[A-Z_]+\s+[1-9]" "$FW/main/app_config.h" >/dev/null \
  && die "有 APP_* 自测开关不是 0（gate 第 7 项会红）——先改回 0"
[ -d "$ROOT/_固件备份/$VER" ] && echo "  ⚠️ 发布目录已存在：_固件备份/$VER（会覆盖 bin/补丁/SHA）"

say "1) 编译（硬熔断）"
if [ "$NOFLASH" = 1 ]; then ./_工具/编译并刷.sh 1 --no-flash || die "编译失败"; else
  ./_工具/编译并刷.sh 12 || die "编译或刷机失败"; fi
SIZE=$(ls -l "$FW/build/FoloToy-AI-Passport.bin" | awk '{print $5}')
[ "$SIZE" -lt 2097152 ] || die "固件 $SIZE B 已经吃穿 app 槽 2 MB"
FREE=$((2097152 - SIZE))
echo "  固件 $SIZE B ｜ app 槽 2 MB ｜ 余 $FREE B（$((FREE/1024)) KB）"

say "2) 重生成补丁（慢一步就会让闸第 7 项红）"
( cd "$FW" && git add -A && git diff --cached "$UP" > "$PATCH" ) || die "补丁生成失败"
echo "  补丁 $(ls -l "$PATCH" | awk '{print $5}') B"
grep -qE '^diff --git a/main/secrets\.h' "$PATCH" && die "补丁里带了 secrets.h —— 停下"
echo "  ✅ 补丁里没有 secrets.h"

say "3) 八项闸（要 GATE-FULL）"
if [ "$DRAFT" = 1 ] || [ "$NOFLASH" = 1 ]; then
  ./_工具/gate.sh | tail -4 || die "GATE-LITE 失败"
  echo "  （--no-flash/--draft：只跑到 LITE，这次**不能**当"验过"）"
else
  ./_工具/gate.sh --device | tail -5 || die "GATE-FULL 失败 —— 不发版"
fi

say "4) 发布包 _固件备份/$VER/"
D="$ROOT/_固件备份/$VER"; mkdir -p "$D"
cp "$FW/build/FoloToy-AI-Passport.bin" "$D/"
cp "$PATCH" "$D/"
# faces.bin：取现成的最大那份（12 张 510 KB / 37 张 1,348 KB），没有再去找 05-表情/
FACES=$(ls -S "$ROOT"/_固件备份/*/faces.bin 2>/dev/null | head -1)
[ -n "$FACES" ] || FACES=$(ls -S "$ROOT"/05-表情/*/faces.bin 2>/dev/null | head -1)
[ -n "$FACES" ] && cp "$FACES" "$D/" && echo "  faces.bin ← ${FACES#$ROOT/}"
[ -f "$ROOT/图/气泡预览.png" ] && cp "$ROOT/图/气泡预览.png" "$D/" && echo "  气泡预览.png ← 图/"
( cd "$D" && shasum -a 256 *.bin 2>/dev/null; shasum -a 256 0001-wprov-扫码配网.patch ) > "$D/SHA256SUMS"
echo "  ---- SHA256SUMS ----"; sed 's/^/  /' "$D/SHA256SUMS"

if [ ! -f "$D/README.md" ]; then
  cat > "$D/README.md" <<EOF
# $VER

> 一句话：$MSG

## 数字

| 项 | 值 |
|---|---|
| 固件 | **$SIZE B** |
| app 槽/余量 | 2 MB（0x200000）／**$FREE B（$((FREE/1024)) KB）** |
| 补丁 | $(ls -l "$PATCH" | awk '{print $5}') B |
| faces.bin | $( [ -f "$D/faces.bin" ] && ls -l "$D/faces.bin" | awk '{print $5}' || echo 未带 ) B |
| 闸 | $([ "$DRAFT" = 1 ] || [ "$NOFLASH" = 1 ] && echo "GATE-LITE" || echo "GATE-FULL 八项全过") |

## 怎么刷 / 怎么退

\`\`\`bash
cd ~/dev/ai-passport
./_工具/编译并刷.sh 12
./_工具/刷固件.sh _固件备份/<上一版>     # 回退
\`\`\`

## 这一版改了什么（待补）

（发版脚本只搭了骨架。**真账要写三样**：改了什么 / 怎么验的（真机日志原文） / 上一条为什么不是这个原因。）
EOF
  echo "  ⚠️ 已生成 README 骨架：$D/README.md —— **发版前把「这一版改了什么」补上**"
fi

if [ "$DRAFT" = 1 ]; then
  say "5) --draft：到此为止（没有提交、没有打标签）"
  echo "  补完 README 后重跑（不带 --draft）即可收口。"; exit 0
fi

say "5) 提交 + 打标签"
git add -A
git commit -q -m "$VER：$MSG

- 固件 $SIZE B（app 槽 2 MB，余 $FREE B）
- 补丁已重生成；闸：$([ "$NOFLASH" = 1 ] && echo GATE-LITE || echo 'GATE-FULL 八项全过')
- 发布包 _固件备份/$VER/（bin + 补丁 + faces.bin + SHA256SUMS + README）" \
  || echo "  （没有新改动可提交，跳过 commit）"
( cd "$FW" && git add -A && git commit -q -m "$VER：$MSG" ) 2>/dev/null || true
git tag -d "$VER" >/dev/null 2>&1 || true          # 标签重打，别指向 amend 之前的旧 commit
git tag -a "$VER" -m "$VER：$MSG" || die "打标签失败"
echo "  ✅ 提交 $(git log --oneline -1)"
echo "  ✅ 标签 $VER → $(git rev-parse --short "$VER^{commit}")"

say "6) 进度表（生成物，别手改）"
python3 _工具/进度表.py --check || die "进度表与能力表不一致 —— 先跑 python3 _工具/进度表.py 再发版（发版不许带着不一致的进度表走）"
ROW_N=$(grep -c '^| [0-9]' 进度表.md)
echo "  进度表已刷新：${ROW_N} 条"

say "完成"
printf "  发布包  _固件备份/%s/\n  固件    %s B（余 %s B）\n  标签    %s\n" \
       "$VER" "$SIZE" "$FREE" "$VER"

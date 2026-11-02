#!/bin/bash
# 从零重建「扫码配网」固件（换电脑/换目录都能用）
# 用法：./重建固件.sh [目标目录]
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
PATCH="$HERE/0001-wprov-扫码配网.patch"
UP_COMMIT="a06235e"
REPO="https://gitee.com/folotoy/ai-passport.git"
WORK="${1:-$HERE/../_参考仓库/folotoy-ai-passport}"
IDF="${IDF_PATH:-$HOME/esp/esp-idf-5.5.3-gh}"

echo "== 1/5 取上游 =="
if [ ! -d "$WORK/.git" ]; then
  git clone "$REPO" "$WORK"
fi
cd "$WORK"
git fetch --tags --quiet || true
git checkout "$UP_COMMIT" 2>/dev/null || git checkout -B base "$UP_COMMIT"
git checkout -- . 2>/dev/null || true

echo "== 2/5 打补丁 =="
if git apply --check "$PATCH" 2>/dev/null; then
  git apply "$PATCH"
  echo "   已应用 $PATCH"
else
  echo "   ⚠️ 补丁无法干净应用（可能已打过）—— 继续，下一步会暴露问题"
fi

echo "== 3/5 IDF =="
[ -f "$IDF/export.sh" ] || { echo "❌ 没找到 IDF 5.5.3：$IDF（设 IDF_PATH 或先克隆）"; exit 9; }
# shellcheck disable=SC1090
. "$IDF/export.sh" >/dev/null 2>&1
command -v cmake >/dev/null || { echo "❌ 该 IDF 环境缺 cmake：pip install \"cmake==3.31.6\" ninja"; exit 8; }

echo "== 4/5 编译 =="
idf.py set-target esp32c3
idf.py build

echo "== 5/5 校验页面 JS（防"正在扫描"卡死）=="
"$HERE/../_工具/查页面.sh" "$WORK" || { echo "❌ 页面 JS 语法错，别刷！"; exit 7; }

echo
echo "✅ 产物: $WORK/build/FoloToy-AI-Passport.bin"
echo "   刷机: $HERE/../_工具/刷固件.sh $WORK"

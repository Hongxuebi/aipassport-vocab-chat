#!/bin/bash
# 拉取点阵中文字体（Fusion Pixel + Ark Pixel，12px 简体比例版）
# 只下 release 里的成品 ttf，不 clone 仓库（仓库只有源码，很大）
set -e
cd "$(dirname "$0")"
TAG="2026.10.25"
mkdir -p _raw ark

fetch() {
  local repo="$1" name="$2" out="$3"
  local url="https://github.com/${repo}/releases/download/${TAG}/${name}-v${TAG}.zip"
  if [ -f "$out" ]; then echo "已有 $out，跳过"; return; fi
  echo "下载 $name …"
  curl -sSL --retry 3 -m 600 -o "_raw/${name}.zip" "$url"
  echo "  $(du -h "_raw/${name}.zip" | cut -f1)"
}

fetch "TakWolf/fusion-pixel-font" "fusion-pixel-font-12px-proportional-ttf" "fusion-pixel-12px-proportional-zh_hans.ttf"
fetch "TakWolf/ark-pixel-font"    "ark-pixel-font-12px-proportional-ttf"    "ark/ark-pixel-12px-proportional-zh_hans.ttf"

echo "解压 …"
unzip -o -q "_raw/fusion-pixel-font-12px-proportional-ttf.zip" \
      "fusion-pixel-12px-proportional-zh_hans.ttf" "OFL.txt" -d .
unzip -o -q "_raw/ark-pixel-font-12px-proportional-ttf.zip" \
      "ark-pixel-12px-proportional-zh_hans.ttf" "OFL.txt" -d ark/
[ -f ark/OFL.txt ] && mv -f ark/OFL.txt OFL-ark.txt

echo "结果："
ls -lh fusion-pixel-12px-proportional-zh_hans.ttf ark/ark-pixel-12px-proportional-zh_hans.ttf 2>/dev/null
echo
echo "生成 LVGL 字库示例："
echo '  npx --yes lv_font_conv@1.5.2 --size 12 --bpp 1 --format bin \'
echo '    --font fusion-pixel-12px-proportional-zh_hans.ttf -r 0x20-0x7E \'
echo '    --symbols "背单词设置连接道馆图鉴宠物" --no-kerning -o ui12.bin'

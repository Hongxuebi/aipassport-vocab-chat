#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 分层检查：domain 不许碰 LVGL/WiFi/NVS/驱动；ui 只许 LVGL+ui_pixel；全仓扫描 abort/assert。
# 用法：./查分层.sh [固件目录]
set -e
FW="${1:-$AIP_FW}"
[ -d "$FW" ] || { echo "❌ 没有固件目录 $FW"; exit 8; }
bad=0
EXEMPT="$PWD/_工具/第三方清单.txt"

check_dir() {  # $1=目录名模式  $2=禁止的 include 正则  $3=说明
  local files=$(find "$FW" -type f \( -name '*.c' -o -name '*.h' \) -path "*/$1/*" 2>/dev/null)
  [ -z "$files" ] && { echo "  · 跳过：还没有 $1/ 目录"; return; }
  local hit=$(echo "$files" | xargs grep -lE "$2" 2>/dev/null || true)
  if [ -n "$hit" ]; then
    echo "  ❌ $3"; echo "$hit" | sed 's/^/     /'; bad=1
  else
    echo "  ✅ $3"
  fi
}

echo "== 1) domain 层不许碰硬件/框架 =="
check_dir domain '#include *[<"](lvgl|lvgl\.h|esp_wifi|nvs|driver/)' "domain/ 里没有 lvgl/wifi/nvs/driver"

echo "== 2) ui 层不许碰网络/存储 =="
check_dir ui '#include *[<"](esp_wifi|nvs_flash|nvs\.h|driver/)' "ui/ 里没有 wifi/nvs/driver"

echo "== 3) 我们自己的文件里不许 abort/assert/ESP_ERROR_CHECK（崩机=用户失联）=="
OURS=$(find "$FW/components/srs" "$FW/components/wprov" "$FW/main/app" "$FW/main/ui" \
            "$FW/main/demo_provision.c" "$FW/main/demo_zh.c" 2>/dev/null || true)
if [ -n "$OURS" ]; then
  # 排除第三方/生成代码
  if [ -f "$EXEMPT" ]; then
    OURS=$(echo "$OURS" | while read -r f; do
      grep -qxF "${f#$FW/}" "$EXEMPT" 2>/dev/null || echo "$f"; done)
  fi
  hit=$(echo "$OURS" | xargs grep -nE '\b(abort|assert)\s*\(' 2>/dev/null | grep -v 'ESP_ERROR_CHECK' || true)
  if [ -n "$hit" ]; then echo "  ❌ 发现 abort/assert："; echo "$hit" | sed 's/^/     /'; bad=1
  else echo "  ✅ 没有裸 abort/assert"; fi
  # IDF 的 ESP_ERROR_CHECK 失败即 abort，等于变相裸 abort
  hit2=$(echo "$OURS" | xargs grep -n "ESP_ERROR_CHECK" 2>/dev/null || true)
  if [ -n "$hit2" ]; then echo "  ❌ 发现 ESP_ERROR_CHECK（会 abort）："; echo "$hit2" | sed 's/^/     /'; bad=1
  else echo "  ✅ 没有 ESP_ERROR_CHECK"; fi
fi

echo
[ $bad -eq 0 ] && echo "✅ 分层检查通过" || { echo "❌ 分层检查失败"; exit 1; }

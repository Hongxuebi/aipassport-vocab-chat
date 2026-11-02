#!/bin/bash
# 墨水屏（ZECTRIX NOTE4，ESP32-S3 N16R8）接机体检：
#   ① 找端口（自动排除 AI Passport 那台 C3）  ② chip/flash 信息  ③ 分区表  ④ 可选 16MB 全片备份
# 用法：./_工具/查墨水屏.sh            # 只体检
#       ./_工具/查墨水屏.sh --backup   # 体检 + 读整片 16MB 到 _固件备份/墨水屏原厂_<时间>.bin
set -u
cd "$(dirname "$0")/.."
PY="$HOME/.espressif/python_env/idf5.5_py3.9_env/bin/python"
C3_MAC="AA:BB:CC:DD:EE:FF"      # 已知的 AI Passport
PORT=""
for p in /dev/cu.usbmodem* /dev/cu.usbserial* /dev/cu.wchusb* /dev/cu.SLAB*; do
  [ -e "$p" ] || continue
  mac=$("$PY" -m esptool --port "$p" -b 115200 --before no_reset --after no_reset chip_id 2>/dev/null | grep -oE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -1)
  if [ "$mac" = "$C3_MAC" ]; then echo "（跳过 $p：这是 AI Passport C3）"; continue; fi
  PORT="$p"; MAC="$mac"; break
done
if [ -z "$PORT" ]; then
  echo "❌ 没找到墨水屏。检查："
  echo "   1) USB-C 线是不是**数据线**（很多是充电线）"
  echo "   2) 设备开机了吗（长按电源键）"
  echo "   3) 插上后如果没出现 /dev/cu.*：按住 BOOT 再插（进下载模式）"
  echo "   当前串口：$(ls /dev/cu.* 2>/dev/null | tr '\n' ' ')"
  exit 9
fi
echo "端口 $PORT   MAC ${MAC:-?}"

echo "== 芯片 / Flash =="
"$PY" -m esptool --port "$PORT" -b 460800 --before default_reset --after no_reset chip_id  2>&1 | grep -E "Chip|MAC|Detecting" | head -4
"$PY" -m esptool --port "$PORT" -b 460800 --before no_reset --after no_reset flash_id 2>&1 | grep -E "Manufacturer|Device|Detected flash size" | head -4

echo "== 分区表（0x8000）=="
TPL=/tmp/eink_part.bin
"$PY" -m esptool --port "$PORT" -b 460800 --before no_reset --after no_reset read_flash 0x8000 0x1000 "$TPL" >/dev/null 2>&1
"$PY" ~/esp/esp-idf-5.5.3-gh/components/partition_table/gen_esp32part.py "$TPL" 2>/dev/null | head -20

if [ "${1:-}" = "--backup" ]; then
  OUT="_固件备份/墨水屏原厂_$(date +%Y%m%d-%H%M).bin"
  mkdir -p _固件备份
  echo "== 读整片 16MB → $OUT（约 5–15 分钟）=="
  "$PY" -m esptool --port "$PORT" -b 460800 --before no_reset --after no_reset read_flash 0x0 0x1000000 "$OUT" 2>&1 | tail -2
  ls -l "$OUT" | awk '{print "大小 "$5" B"}'
  shasum -a 256 "$OUT" | awk '{print "sha256 "$1}'
fi
echo "== 完 =="

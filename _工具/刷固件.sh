#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 刷任意一个 ESP-IDF 工程的 build/ 产物（绕过 idf.py flash 在本机的拼接 bug）
# 用法：./刷固件.sh <工程目录> [串口] [波特率]
#   例：./刷固件.sh 06-实验/E1_i2s_duplex
#       ./刷固件.sh $AIP_FW  /dev/cu.usbmodem<端口号>
set -e
PROJ="${1:?用法: 刷固件.sh <工程目录> [串口] [波特率]}"
PORT="${2:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
BAUD="${3:-460800}"
IDF="${IDF_PATH:-$HOME/esp/esp-idf-5.5.3-gh}"
[ -f "$IDF/export.sh" ] || IDF="$HOME/esp/esp-idf"
. "$IDF/export.sh" >/dev/null 2>&1

[ -n "$PORT" ] || { echo "❌ 没找到串口：检查数据线（要能传数据的线）/ 按一下电源键"; exit 9; }
[ -f "$PROJ/build/flash_args" ] || { echo "❌ 没有 build/flash_args：先在该工程里 idf.py build"; exit 8; }

echo "工程: $PROJ"
echo "串口: $PORT    波特率: $BAUD"
echo "---- flash_args ----"; cat "$PROJ/build/flash_args"

cd "$PROJ/build"
# --before 默认 default_reset（DTR/RTS 复位）；本机偶尔复位不进下载模式（USB-Serial/JTAG 状态怪），
# 刷不动时用环境变量切：BEFORE=no_reset ./刷固件.sh …
BEFORE="${BEFORE:-default_reset}"
esptool.py -p "$PORT" -b "$BAUD" --before "$BEFORE" --after hard_reset write_flash @flash_args

echo "✅ 烧写完成；看日志："
echo "   python3 - <<'PY'"
echo "   import serial,time; s=serial.Serial('$PORT',115200,timeout=1); t=time.time()"
echo "   while time.time()-t<8: print(s.read(4096).decode('utf8','replace'),end='')"
echo "   PY"

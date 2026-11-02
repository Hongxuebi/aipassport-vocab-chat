#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 编译并刷 + 抓启动日志（一条命令走完，防止"编失败还刷了旧包"）
#
# 为什么要它：2026-10-25 踩过 —— 编译报错，但命令链是 `idf.py build | grep error`，
# grep 找到了 error 也算成功，于是**把上一次的旧固件刷了进去**，后面所有排查都是假的。
# 这个脚本把"必须看到 Project build complete"作为硬条件。
#
# 用法：
#   ./编译并刷.sh                 # 编译 + 熔断检查 + 刷 + 抓 8 秒启动日志
#   ./编译并刷.sh 20              # 抓 20 秒日志
#   ./编译并刷.sh 8 --no-flash    # 只编译不刷
set -u
cd "$(dirname "$0")/.."
FW=$AIP_FW
SECS="${1:-8}"
[ "${2:-}" = "--no-flash" ] && NOFLASH=1 || NOFLASH=0

. "$HOME/esp/esp-idf-5.5.3-gh/export.sh" >/dev/null 2>&1

echo "== 编译 =="
if ! ( cd "$FW" && idf.py build ) > /tmp/aip_build.log 2>&1; then
  echo "❌ 编译失败（**没有**刷机）。最后 20 行："
  tail -20 /tmp/aip_build.log
  exit 1
fi
grep -q "Project build complete" /tmp/aip_build.log || {
  echo "❌ 没看到 'Project build complete'，按失败处理（不刷）"; tail -10 /tmp/aip_build.log; exit 1; }
SIZE=$(ls -l "$FW/build/FoloToy-AI-Passport.bin" | awk '{print $5}')
echo "✅ 编译通过：$SIZE 字节 ｜ 分区余量见 gate.sh"

[ "$NOFLASH" = 1 ] && { echo "（--no-flash：不刷）"; exit 0; }

echo "== 刷机 =="
./_工具/刷固件.sh "$FW" > /tmp/aip_flash.log 2>&1 || { echo "❌ 刷机失败"; tail -10 /tmp/aip_flash.log; exit 1; }
grep -c "Hash of data verified" /tmp/aip_flash.log | awk '{print "✅ 校验通过分段数:", $1}'

echo "== 抓启动日志 ${SECS}s =="
PY="$HOME/.espressif/python_env/idf5.5_py3.9_env/bin/python"
LOG=/tmp/aip_boot_$(date +%H%M%S).log
# 端口号会变（101↔1101），永远用 glob 找 —— 写死过一次，抓不到日志
PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
[ -n "$PORT" ] || { echo "❌ 没找到串口，跳过抓日志"; exit 0; }
echo "串口: $PORT"
"$PY" - "$SECS" "$PORT" > "$LOG" 2>&1 <<'PY'
import serial, time, sys
secs=float(sys.argv[1]); port=sys.argv[2]
s=serial.Serial(port,115200,timeout=0.3)
s.setDTR(False); s.setRTS(True); time.sleep(0.2); s.setRTS(False); time.sleep(0.2); s.setDTR(True)
s.reset_input_buffer(); t0=time.time(); buf=""
while time.time()-t0<secs:
    d=s.read(8192)
    if d: buf+=d.decode("utf8","replace")
s.close(); print(buf)
PY
echo "日志：$LOG"
grep -E "就绪:Display|Guru|assert failed|Task watchdog|rst:0x" "$LOG" | head -6
echo "（看到「就绪:Display=1 Button=1 Audio=1 Battery=1」且没有 Guru/assert/watchdog 才算起来）"

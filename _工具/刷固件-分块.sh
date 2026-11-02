#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 分块刷机（16KB/块，逐块校验）：按 build/flash_args 刷；额外可再刷"数据镜像"（如 assets）
# 用法：./_工具/刷固件-分块.sh                 # 只刷 flash_args
#       ./_工具/刷固件-分块.sh 0x4A0000:/tmp/assets_60.img   # 额外刷一段
set -u
PY="$HOME/.espressif/python_env/idf5.5_py3.9_env/bin/python"
P=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)
# 串口**只能一个进程**：抓日志的 python 不关，刷机全报 Resource temporarily unavailable（2026-10-26 白折腾半小时）
HOLDER=$(lsof -nP "$P" 2>/dev/null | awk 'NR==2{print $1" (pid "$2")"}')
if [ -n "$HOLDER" ]; then
  echo "❌ 串口 $P 被占用：$HOLDER"
  echo "   先关掉抓日志的进程（pkill -f keys_capture / pkill -f 'serial.Serial'）再刷"
  exit 8
fi
case "$AIP_FW" in /*) FW="$AIP_FW" ;; *) FW="$HOME/dev/ai-passport/$AIP_FW" ;; esac
B=$FW/build
CH=16384
[ -n "$P" ] || { echo "❌ 没有串口"; exit 9; }
echo "端口 $P"
ok=0; fail=0
flash_chunk() {
  for t in 1 2 3 4 5; do
    out=$("$PY" -m esptool --port "$P" -b 115200 --no-stub --before default_reset --after no_reset \
          write_flash --flash_mode dio --flash_freq 80m --flash_size 8MB "$1" "$2" 2>&1)
    echo "$out" | grep -q "Hash of data verified" && return 0
    sleep 0.4
  done
  return 1
}
flash_file() {   # $1=offset $2=file
  local off=$1; local n=0
  rm -f /tmp/fo_chunk_*
  split -b $CH "$2" /tmp/fo_chunk_ || return 1
  for f in /tmp/fo_chunk_*; do
    n=$((n+1))
    if flash_chunk $(printf "0x%x" $off) "$f"; then ok=$((ok+1)); else fail=$((fail+1)); echo "❌ $2 块#$n @$(printf 0x%x $off)"; fi
    off=$((off+CH))
  done
  rm -f /tmp/fo_chunk_*
}
echo "== 按 flash_args 刷 =="
while read -r off file; do
  case "$off" in --*|"") continue;; esac
  [ -n "$file" ] || continue
  echo "-- $off $file"; flash_file "$off" "$B/$file"
done < "$B/flash_args"
echo "== 额外数据镜像（可选）：$* =="
for spec in "$@"; do      # 形式 0x5A0000:/path/to/img.bin
  o=${spec%%:*}; f=${spec#*:}
  echo "-- $o $f"; flash_file "$o" "$f"
done
echo "== 汇总：成功块 $ok / 失败块 $fail =="
[ $fail -eq 0 ] || exit 1

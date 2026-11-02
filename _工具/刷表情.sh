#!/bin/bash
# 刷表情.sh —— 只把表情包写进 `pet` 分区（0x210000），**不动 app / 分区表 / bootloader**
#
# 用法：
#   ./刷表情.sh                                   # 用 05-表情/大肥鱼-12表情/faces.bin
#   ./刷表情.sh 路径/faces.bin [串口] [波特率]
#   ./刷表情.sh --check                           # 只校验文件，不碰串口
#
# 前提：分区表是 **M1 那版 pet=2MB（0x200000）**。老表的 pet 只有 512KB，写 37 张会越界，
#       所以换分区表时必须**跟固件一起刷**（`_工具/刷固件.sh` 会带分区表）。
set -euo pipefail
cd "$(dirname "$0")/.."

BIN="${1:-05-表情/大肥鱼-37表情/faces-37张.bin}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && { BIN="05-表情/大肥鱼-37表情/faces-37张.bin"; CHECK_ONLY=1; shift || true; }
PORT="${2:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)}"
BAUD="${3:-460800}"
PART_OFF=0x210000
PART_SIZE=2097152         # 2 MB（2026-10-29 M1：37 张 = 1,348,296 B）

[ -f "$BIN" ] || { echo "❌ 没有表情包：$BIN（先跑 _工具/装表情.py）"; exit 8; }

SZ=$(stat -f%z "$BIN")
MAGIC=$(head -c 4 "$BIN")
echo "表情包: $BIN"
echo "  大小: $SZ B（分区 $((PART_SIZE/1024)) KB）｜magic: $MAGIC｜落点: $PART_OFF"

[ "$MAGIC" = "FAC1" ] || { echo "❌ 不是 FAC1 包（magic=$MAGIC）"; exit 7; }
[ "$SZ" -le "$PART_SIZE" ] || { echo "❌ 表情包 ${SZ} B 超过分区 ${PART_SIZE} B"; exit 7; }
# 16 + 40*n + 36400*n 必须整整齐齐
python3 - "$BIN" <<'PY' || exit 7
import struct, sys
b = open(sys.argv[1], 'rb').read()
ver, n, w, h, cf, fl, ds = struct.unpack_from('<BBHHBBH', b, 4)
need = 16 + ds * n + 36400 * n
print(f'  校验: {n} 张 {w}x{h} cf=0x{cf:02x} dir={ds}  期望 {need:,} B / 实际 {len(b):,} B')
assert len(b) == need, '字节数不对'
assert (w, h, cf, ds) == (140, 130, 0x12, 40), '头字段不对'
print('  ✅ 结构校验通过')
PY

if [ "$CHECK_ONLY" = 1 ]; then
  echo "（--check：不刷）"
  exit 0
fi

[ -n "$PORT" ] || { echo "❌ 没找到串口：检查数据线 / 按一下电源键"; exit 9; }
IDF="${IDF_PATH:-$HOME/esp/esp-idf-5.5.3-gh}"
[ -f "$IDF/export.sh" ] || IDF="$HOME/esp/esp-idf"
# shellcheck disable=SC1090
. "$IDF/export.sh" >/dev/null 2>&1

echo "串口: $PORT  波特率: $BAUD"
esptool.py -p "$PORT" -b "$BAUD" --before default_reset --after hard_reset \
  write_flash "$PART_OFF" "$BIN"
echo "✅ 表情包写完（$SZ B）。看屏：▲/▼ 短按逐张切，串口会打「表情 n/12：名字」。"

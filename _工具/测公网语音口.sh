#!/bin/bash
: "${AIP_FW:=_参考仓库/folotoy-ai-passport}"   # 固件目录（默认按作者的目录布局；外人把补丁打在上游 clone 里时用 AIP_FW=/path/to/folotoy-ai-passport 覆盖）
# 测公网语音口：从**外网**验一次"设备说话 → 服务端机 → 回话"整条链（不需要设备，不需要开发机转发）
#
# 用法：
#   ./测公网语音口.sh                          # 默认 GATEWAY_HOST RELAY_VOICE_PORT，带设备令牌
#   ./测公网语音口.sh GATEWAY_HOST RELAY_VOICE_PORT
#   ./测公网语音口.sh LAN_HOST 8802 v1         # 老固件式（半关闭；只直连/ssh 能通）
#   ./测公网语音口.sh LAN_HOST 8802 noauth     # 最老固件式（不带头）
#
# 判据（三项全绿才算通）：
#   ① TCP 能连（否则：frps 没注册 / 云安全组没放行 / 端口写错）
#   ② 回包字节数 > 0（否则：服务端机没在听 / 令牌不对被拒 / 音频没发出去）
#   ③ 服务端机 aitalk.log 里出现同一条 ASR 文本（这一步要在服务端机上看，脚本会提示）
set -u
cd "$(dirname "$0")/.." || exit 1
HOST="${1:-GATEWAY_HOST}"
PORT="${2:-10443}"
MODE="${3:-v2}"
SECRETS="$AIP_FW/main/secrets.h"
TOKEN=""
if [ -f "$SECRETS" ]; then
  TOKEN=$(sed -n 's/.*AIP_DEV_TOKEN *"\(.*\)".*/\1/p' "$SECRETS" | head -1)
fi

echo "== 1) 端口可达性 $HOST:$PORT =="
if nc -z -G 3 -w 3 "$HOST" "$PORT" 2>/dev/null; then
  echo "  ✅ TCP 通"
else
  echo "  ❌ TCP 不通："
  echo "     · 服务端机侧：ssh 过去看 frpc 日志有没有 [aip-voice] start proxy success"
  echo "     · VPS 侧：ss -lntp | grep $PORT ；以及云安全组入站是否放行 $PORT"
  exit 1
fi

echo "== 2) 造一句人话（本机 say）=="
TMP=$(mktemp -d)
say -v Tingting -o "$TMP/q.aiff" "一加一等于几" 2>/dev/null || { echo "  ❌ say 不可用"; exit 1; }
afconvert -f WAVE -d LEI16@16000 -c 1 "$TMP/q.aiff" "$TMP/q.wav" || exit 1
/usr/bin/python3 - "$TMP/q.wav" "$TMP/q.pcm" <<'PY'
import struct, sys
b = open(sys.argv[1], 'rb').read(); i = 12
while i + 8 <= len(b):
    cid = b[i:i+4]; (sz,) = struct.unpack_from('<I', b, i+4)
    if cid == b'data':
        open(sys.argv[2], 'wb').write(b[i+8:i+8+sz]); print("  PCM %d B（%.2fs）" % (sz, sz/2/16000)); break
    i += 8 + sz + (sz & 1)
PY

echo "== 3) 发过去、听回话（$MODE）=="
/usr/bin/python3 - "$HOST" "$PORT" "$TMP/q.pcm" "$MODE" "$TOKEN" <<'PY'
import socket, sys, time
host, port, pcmf, mode, tok = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
pcm = open(pcmf, 'rb').read()
t0 = time.time()
s = socket.create_connection((host, port), 8)
if mode == 'v2':
    # 新固件的做法：AIPV2 头 + [2字节长度][1KB] 帧 + 0 长度帧（**不发 FIN**）
    if tok:
        s.sendall(("AIPV2 %s\n" % tok).encode())
    for i in range(0, len(pcm), 1024):
        ch = pcm[i:i+1024]
        s.sendall(bytes([len(ch) >> 8, len(ch) & 0xFF]) + ch)
    s.sendall(b'\x00\x00')
elif mode == 'v1':
    # 老固件的做法：AIPV1 头 + 裸 PCM + shutdown(SHUT_WR)（只有直连/ssh 能通，frp 会吞）
    if tok:
        s.sendall(("AIPV1 %s\n" % tok).encode())
    s.sendall(pcm); s.shutdown(socket.SHUT_WR)
else:                                   # noauth：连头都不带（最老的固件）
    s.sendall(pcm); s.shutdown(socket.SHUT_WR)
t1 = time.time(); buf = b""; first = None
while True:
    d = s.recv(65536)
    if not d: break
    if first is None: first = time.time() - t1
    buf += d
print("  首字节 %s ms ｜ 回包 %d B（%.2fs）" % (("%.0f" % (first*1000)) if first else "—", len(buf), len(buf)/2/16000))
if buf:
    open('/tmp/aip_pub_reply.pcm', 'wb').write(buf)
    print("  ✅ 有声音回来了（已存 /tmp/aip_pub_reply.pcm，可用 afplay 听）")
    print("     afplay 需要 WAV 头，最省事：afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/aip_pub_reply.pcm /tmp/aip_pub_reply.wav")
else:
    print("  ❌ 一个字节都没回：令牌不对 / 服务端机 aitalk 没在听 / 音频被丢")
    sys.exit(2)
PY
RC=$?
echo "== 4) 服务端机侧对账（这一步要 ssh 到服务端机看）=="
echo "  tail -6 ~/aipassport/aitalk.log  应看到：TCP 鉴权… → ASR … '1加1等于几？' → TTS …"
rm -rf "$TMP"
exit $RC

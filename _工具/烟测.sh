#!/bin/bash
# 真机烟测：复位设备 → 抓 15 秒串口 → 断言关键行；崩机/外设没起来都会 FAIL。
# 用法：./烟测.sh [串口]
set -e
PORT="${1:-$(ls /dev/cu.usbmodem* 2>/dev/null | head -1)}"
[ -n "$PORT" ] || { echo "❌ 没找到串口（设备插了吗？数据线能传数据吗？）"; exit 9; }
PY="$HOME/.espressif/python_env/idf5.5_py3.9_env/bin/python"
LOG="/tmp/smoke_$(date +%H%M%S).log"

echo "串口: $PORT   日志: $LOG"
"$PY" - "$PORT" > "$LOG" 2>&1 <<'PY'
import serial, time, sys
p=sys.argv[1]
s=serial.Serial(p,115200,timeout=0.3)
s.setDTR(False); s.setRTS(True); time.sleep(0.15); s.setRTS(False); time.sleep(0.15); s.setDTR(True)
s.reset_input_buffer()
t0=time.time()
while time.time()-t0 < 15:
    d=s.read(8192)
    if d: sys.stdout.write(d.decode('utf8','replace')); sys.stdout.flush()
s.setDTR(False); s.setRTS(False); time.sleep(0.05)   # 关之前放开复位线：close() 抖一下会把芯片送进下载模式
s.close()
PY

fail=0
chk(){ if grep -qE "$1" "$LOG"; then echo "  ✅ $2"; else echo "  ❌ $2"; fail=$((fail+1)); fi; }
bad(){ if grep -qE "$1" "$LOG"; then echo "  ❌ 出现异常：$2"; fail=$((fail+1)); else echo "  ✅ 无「$2」"; fi; }

echo "---- 断言 ----"
chk "就绪:Display=1 Button=1 Audio=1 Battery=1" "四项外设自检全 1"
chk "bsp_disp: 显示就绪 240x320"                "屏幕初始化"
chk "bsp_lvgl: LVGL 就绪"                       "LVGL 起来"
chk "bsp_btn: 按键就绪"                         "按键起来"
# 网络三种合法态（第三种 2026-10-25 补：保存的网络当前不在范围内时，
# 15 秒窗口内走不到"热点+扫描就绪"，原来这条断言在异地/热点关了时**永远过不了**，
# 那不是设备故障，是闸的真空档 —— 判据必须是"连接路径跑完并报了结果"，不是"WiFi 必须通"）
chk "(STA 拿到 IP|没有保存过 WiFi|没连上.*换下一个|第一遍全部失败|自动连接结果|热点\+扫描就绪)" "网络：连上 / 没配过 / 已判失败走兜底"
bad "Guru Meditation|abort\(\)|assert failed|paniced" "崩溃/断言"
bad "E \(.*\) (esp_netif|wifi):.*fail"          "网络初始化失败"

echo
if [ $fail -eq 0 ]; then echo "✅ 烟测通过（日志留档 $LOG）"; else echo "❌ 烟测失败 $fail 项（看 $LOG）"; exit 1; fi

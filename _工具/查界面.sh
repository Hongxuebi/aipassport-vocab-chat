#!/bin/bash
# 界面静态规则：正文见 查界面.py（①中文不许用 montserrat ②按键回调不许增删对象 ③中文标签给固定尺寸）
# ④ 折行规则（2026-10-29 夜加）：气泡/回显的两行折法用**离线镜像**验 —— 不刷机就能挡回退步
HERE="$(cd "$(dirname "$0")" && pwd)"
python3 "$HERE/查界面.py" || exit 1
exec python3 "$HERE/预览回显.py" --check

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_preview.py —— 把 pet_assets.bin 做成一个自包含的 HTML，模拟 AI-Passport 的屏。

为什么不是"直接放 webm"：那样看到的是 PC 版。这里刻意用 ESP32 那条管线的产物
（96×96、12fps、255 色索引、透明填底色），你看到的每一帧就是真机会刷出来的画面。

用法：
  python build_preview.py --bin <pet_assets.bin> --out <xxx.html>
"""
import argparse
import base64
import json
import sys
from pathlib import Path

HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>大肥鱼桌宠 · AI-Passport 屏幕预览 (240x320)</title>
<style>
  :root{
    --ink:#e8e8ef; --dim:#8a8a99; --body:#0b0b10;
    --shell1:#23232c; --shell2:#15151b;
    --screen-bg:#1A1A22;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0; background:
      radial-gradient(1200px 700px at 50% -10%, #1b2030 0%, #0b0b10 60%, #07070a 100%);
    color:var(--ink);
    font:14px/1.5 -apple-system,"Segoe UI",system-ui,"Microsoft YaHei",sans-serif;
    display:flex; align-items:center; justify-content:center;
    min-height:100%; padding:28px 16px;
  }
  .wrap{display:flex; gap:36px; align-items:center; flex-wrap:wrap; justify-content:center}

  /* ---------------- 设备外形 ---------------- */
  .device{
    position:relative;
    padding:22px 20px 18px;
    border-radius:44px;
    background:linear-gradient(160deg,var(--shell1),var(--shell2));
    box-shadow:
      0 30px 70px rgba(0,0,0,.65),
      inset 0 1px 0 rgba(255,255,255,.10),
      inset 0 -1px 0 rgba(0,0,0,.6);
    border:1px solid rgba(255,255,255,.06);
  }
  .device::before{
    content:""; position:absolute; inset:9px; border-radius:36px;
    border:1px solid rgba(255,255,255,.045); pointer-events:none;
  }
  .bezel{
    border-radius:30px; padding:0; overflow:hidden;
    box-shadow: inset 0 0 0 2px #000, 0 0 0 6px #101014;
    background:#000;
  }
  canvas#screen{
    display:block;
    width:480px; height:640px;      /* 240x320 的 2 倍，方便看清像素 */
    image-rendering:pixelated;
    image-rendering:crisp-edges;
    background:var(--screen-bg);
  }

  /* ---------------- 三键 ---------------- */
  .keys{display:flex; gap:10px; justify-content:center; margin-top:16px}
  .key{
    flex:1; height:52px; border-radius:14px; border:1px solid rgba(255,255,255,.10);
    background:linear-gradient(180deg,#2b2b36,#1c1c24);
    color:var(--ink); font:600 13px/1 inherit; letter-spacing:.08em;
    cursor:pointer; user-select:none; -webkit-user-select:none;
    transition:transform .06s ease, background .12s ease, box-shadow .12s ease;
    box-shadow:0 3px 0 #0a0a0d, 0 6px 14px rgba(0,0,0,.5);
  }
  .key:hover{background:linear-gradient(180deg,#33333f,#22222b)}
  .key.active{transform:translateY(2px); box-shadow:0 1px 0 #0a0a0d; background:#3a4a6a}
  .key small{display:block; font-weight:400; font-size:10px; color:var(--dim); letter-spacing:0; margin-top:3px}

  /* ---------------- 右侧说明 ---------------- */
  .side{max-width:330px}
  .side h1{font-size:17px; margin:0 0 6px; letter-spacing:.01em}
  .side .sub{color:var(--dim); font-size:12.5px; margin-bottom:18px}
  .hud{
    background:rgba(255,255,255,.035); border:1px solid rgba(255,255,255,.07);
    border-radius:12px; padding:12px 14px; margin-bottom:14px;
    font:12px/1.7 ui-monospace,"Consolas",monospace; color:#c8c8d8;
  }
  .hud b{color:#7fb0ff; font-weight:600}
  .hud .row{display:flex; justify-content:space-between; gap:12px}
  .hud .row span:first-child{color:var(--dim)}
  table{width:100%; border-collapse:collapse; font-size:12.5px}
  td{padding:5px 0; vertical-align:top}
  td:first-child{color:#9fd0ff; white-space:nowrap; padding-right:12px; font-family:ui-monospace,Consolas,monospace}
  td:last-child{color:#c2c2d0}
  select#animList{
    width:100%; margin-bottom:14px; padding:9px 11px; border-radius:10px;
    background:#1b1b23; color:#dcdee8; border:1px solid rgba(255,255,255,.10);
    font:13px/1.4 inherit; outline:none; cursor:pointer;
  }
  select#animList:focus{border-color:rgba(127,176,255,.5)}
  .note{margin-top:16px; font-size:11.5px; color:#6f6f80; line-height:1.7}
  .badge{display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px;
         background:rgba(127,176,255,.14); color:#9fd0ff; border:1px solid rgba(127,176,255,.25)}
  .loading{position:fixed; inset:0; display:flex; align-items:center; justify-content:center;
           background:#07070a; color:#9fd0ff; font-size:14px; z-index:99; transition:opacity .4s}
</style>
</head>
<body>
<div class="loading" id="loading">正在解析帧包…</div>

<div class="wrap">
  <div class="device">
    <div class="bezel"><canvas id="screen" width="240" height="320"></canvas></div>
    <div class="keys">
      <button class="key" data-key="up">UP<small>上一段</small></button>
      <button class="key" data-key="ok">OK<small>点它 / 双击换</small></button>
      <button class="key" data-key="down">DN<small>下一段</small></button>
    </div>
  </div>

  <div class="side">
    <h1>大肥鱼桌宠 · AI-Passport 屏</h1>
    <div class="sub">240×320 ST7789 · 96×96 精灵 · 12 fps · 255 色<span class="badge">真机同款管线</span></div>

    <div class="hud" id="hud">
      <div class="row"><span>动作</span><span><b id="h-anim">—</b></span></div>
      <div class="row"><span>进度</span><span id="h-frame">0/0</span></div>
      <div class="row"><span>库内</span><span id="h-count">—</span></div>
      <div class="row"><span>位置</span><span id="h-pos">—</span></div>
    </div>

    <select id="animList"><option>加载中…</option></select>

    <table>
      <tr><td>OK 单击</td><td>点它 → 播「点击回应」</td></tr>
      <tr><td>OK 双击</td><td>随机换一段动作</td></tr>
      <tr><td>UP / DN</td><td>上一段 / 下一段</td></tr>
      <tr><td>UP 长按</td><td>换位置（四角 + 居中）</td></tr>
    </table>

    <div class="note">
      这一页不是 PC 版回放：每一帧都是从 <code>pet_assets.bin</code> 里用 PackBits 解出来的，
      和 ESP32 上 <code>pet_anim_decode()</code> 走同一套逻辑。像素墙就是 240×320 本身，
      放大 2 倍只是为了看清。<br><br>
      注意看：<b>透明区域是一块填了底色的方块</b> —— LCD 没有 alpha 通道，真机上就是这样，
      不是 bug。
    </div>
  </div>
</div>

<script>
const B64 = "__B64__";

const SCREEN_W = 240, SCREEN_H = 320;
const BG = [26, 26, 34];            // PET_BG_COLOR 0x1A1A22

const canvas = document.getElementById('screen');
const ctx = canvas.getContext('2d', { alpha:false });
const off = document.createElement('canvas');
const octx = off.getContext('2d', { alpha:false });

// AI-Passport 的落点（和 demo_pet.c 的 POS[] 一字不差）
const POS = [[144,208],[16,208],[16,24],[144,24],[72,112]];

let petW=0, petH=0, pal=[], anims=[], imgData=null, offCtx=null;
let cur=0, frame=0, posIdx=0, lastTick=0, showBox=false, SC=1;

function rgb565(v){ return [((v>>11)&0x1F)*255/31|0, ((v>>5)&0x3F)*255/63|0, (v&0x1F)*255/31|0]; }

function parse(buf){
  const D = new Uint8Array(buf);
  const dv = new DataView(buf);
  const magic = String.fromCharCode(D[0],D[1],D[2],D[3]);
  if (magic !== 'PET1') throw new Error('magic 不对: '+magic);
  const ver = D[4];
  const palN = dv.getUint16(5,true);
  petW = dv.getUint16(7,true);
  petH = dv.getUint16(9,true);
  let o = 11;
  pal = [];
  for (let i=0;i<palN;i++){ pal.push(rgb565(dv.getUint16(o+i*2,true))); }
  o += palN*2;
  const n = dv.getUint16(o,true); o += 2;
  const dec = new TextDecoder('utf-8');
  anims = [];
  for (let k=0;k<n;k++){
    const nl = D[o++];
    const name = dec.decode(D.subarray(o,o+nl)); o += nl;
    const fc = dv.getUint16(o,true); o+=2;
    const fps = dv.getUint16(o,true); o+=2;
    o += 1;
    const offs = [];
    for (let i=0;i<fc;i++){ offs.push(dv.getUint32(o,true)); o+=4; }
    anims.push({name,fc,fps,offs});
  }
  window.__D = D; window.__DV = dv;
  off.width = petW; off.height = petH;
  offCtx = off.getContext('2d', { alpha:false });
  imgData = offCtx.createImageData(petW, petH);
  const d = imgData.data;
  for (let i=0;i<petW*petH;i++){ d[i*4]=BG[0]; d[i*4+1]=BG[1]; d[i*4+2]=BG[2]; d[i*4+3]=255; }
  // 小分辨率素材（开机画面）按整数倍最近邻放大到屏幕，和固件里"每个像素写 N×N"一致
  SC = Math.max(1, Math.min(Math.floor(SCREEN_W/petW), Math.floor(SCREEN_H/petH)));
  const dw = petW*SC, dh = petH*SC;
  if (dw >= SCREEN_W || dh >= SCREEN_H){
    POS.length = 0;                       // 满屏素材（开机画面）只有原点一个落点
    POS.push([Math.floor((SCREEN_W-dw)/2), Math.floor((SCREEN_H-dh)/2)]);
  }
}

// 和 pet_anim.c 的 pet_anim_decode 同构：边解边查表，透明填底色
function decodeAt(offv){
  const D = window.__D, dv = window.__DV;
  const len = dv.getUint16(offv,true);
  let p = offv+2; const end = p+len;
  const total = petW*petH;
  const d = imgData.data;
  let n = 0;
  while (n < total && p < end){
    const c = D[p++];
    if (c < 0x80){
      if (p>=end) break;
      const v = D[p++];
      const px = v ? pal[v] : BG;
      const cnt = c+1;
      for (let k=0;k<cnt && n<total;k++,n++){
        d[n*4]=px[0]; d[n*4+1]=px[1]; d[n*4+2]=px[2];
      }
    } else {
      let cnt = (c & 0x7F)+1;
      while (cnt-- && n<total && p<end){
        const v = D[p++];
        const px = v ? pal[v] : BG;
        d[n*4]=px[0]; d[n*4+1]=px[1]; d[n*4+2]=px[2]; n++;
      }
    }
  }
  while (n<total){ d[n*4]=BG[0]; d[n*4+1]=BG[1]; d[n*4+2]=BG[2]; n++; }
  offCtx.putImageData(imgData,0,0);
}

function findByName(nm){ return anims.findIndex(a=>a.name===nm); }

function pickNext(){
  if (!anims.length) return;
  if (Math.random()*100 < 20){ const i=findByName('待机呼吸休闲'); if(i>=0) return play(i); }
  play((Math.random()*anims.length)|0);
}

function play(i){
  if (i<0 || i>=anims.length) return;
  cur = i; frame = 0; lastTick = 0;
  document.getElementById('h-anim').textContent = anims[i].name;
  const sel = document.getElementById('animList');
  if (sel && sel.value !== String(i)) sel.value = String(i);
}

function clickReaction(){
  const hits = anims.map((a,i)=>({a,i})).filter(x=>x.a.name.startsWith('点击回应'));
  if (hits.length){ play(hits[(Math.random()*hits.length)|0].i); }
  else pickNext();
}

function step(dir){
  if (!anims.length) return;
  play((cur + dir + anims.length) % anims.length);
}

function moveTo(i){
  posIdx = ((i % POS.length) + POS.length) % POS.length;
  document.getElementById('h-pos').textContent = posIdx+1 + '/5';
  redraw();
}

function redraw(){
  ctx.fillStyle = 'rgb('+BG.join(',')+')';
  ctx.fillRect(0,0,SCREEN_W,SCREEN_H);
  const p = POS[posIdx] || POS[0] || [0,0];
  const [x,y] = p;
  ctx.imageSmoothingEnabled = false;      // 真机是整数倍像素复制，不是双线性
  ctx.drawImage(off, x, y, petW*SC, petH*SC);
  if (showBox){
    ctx.strokeStyle='rgba(127,176,255,.55)';
    ctx.lineWidth=1;
    ctx.strokeRect(x+.5,y+.5, petW*SC-1, petH*SC-1);
  }
}

function tick(now){
  const a = anims[cur];
  if (a){
    const gap = 1000 / (a.fps || 12);
    if (!lastTick || now - lastTick >= gap){
      lastTick = now;
      decodeAt(a.offs[a.fc ? frame % a.fc : 0]);
      redraw();
      frame++;
      document.getElementById('h-frame').textContent = ((frame-1)%a.fc + 1) + '/' + a.fc;
      if (frame >= a.fc){ pickNext(); }
    }
  }
  requestAnimationFrame(tick);
}

// ---------------- 三键（含长按 / 双击），语义对齐 demo_pet.c ----------------
const HOLD_MS = 500, DBL_MS = 260;
let holdT = 0, okPending = null, okLast = 0;

function press(key){
  if (key === 'ok'){
    const now = performance.now();
    if (now - okLast < DBL_MS){          // 双击
      clearTimeout(okPending); okPending = null; okLast = 0;
      pickNext();
    } else {
      okLast = now;
      okPending = setTimeout(()=>{ okPending=null; okLast=0; clickReaction(); }, DBL_MS);
    }
  } else if (key === 'up') step(-1);
  else if (key === 'down') step(+1);
}

function hold(key){
  if (key === 'up') moveTo(posIdx + 1);
}

document.querySelectorAll('.key').forEach(btn=>{
  const key = btn.dataset.key;
  btn.addEventListener('pointerdown', e=>{
    e.preventDefault();
    btn.classList.add('active');
    holdT = performance.now();
  });
  btn.addEventListener('pointerup', e=>{
    e.preventDefault();
    btn.classList.remove('active');
    const dt = performance.now() - holdT;
    if (dt >= HOLD_MS) hold(key); else press(key);
  });
  btn.addEventListener('pointerleave', ()=> btn.classList.remove('active'));
  btn.addEventListener('contextmenu', e=>e.preventDefault());
});

window.addEventListener('keydown', e=>{
  if (e.repeat) return;
  const k = e.key;
  if (k === 'Enter' || k === ' ') { e.preventDefault(); press('ok'); }
  else if (k === 'ArrowUp') { e.preventDefault(); press('up'); }
  else if (k === 'ArrowDown') { e.preventDefault(); press('down'); }
  else if (k === 'b' || k === 'B') { showBox = !showBox; redraw(); }
  else if (k === 'ArrowLeft') { e.preventDefault(); moveTo(posIdx-1); }
  else if (k === 'ArrowRight') { e.preventDefault(); moveTo(posIdx+1); }
});

// ---------------- 启动 ----------------
(async function boot(){
  try{
    const res = await fetch('data:application/octet-stream;base64,' + B64);
    const buf = await res.arrayBuffer();
    parse(buf);
    document.getElementById('h-count').textContent = anims.length + ' 段 / ' + petW + '×' + petH;
    document.getElementById('h-pos').textContent = '1/5';
    const sel = document.getElementById('animList');
    sel.innerHTML = '';
    anims.forEach((a,i)=>{
      const o = document.createElement('option');
      o.value = i;
      o.textContent = String(i+1).padStart(3,'0') + '  ' + a.name + '  (' + a.fc + ' 帧)';
      sel.appendChild(o);
    });
    sel.addEventListener('change', ()=> play(+sel.value));
    const idle = findByName('待机呼吸休闲');
    play(idle >= 0 ? idle : 0);
    redraw();
    document.getElementById('loading').style.opacity = 0;
    setTimeout(()=>document.getElementById('loading').remove(), 420);
    requestAnimationFrame(tick);
  }catch(err){
    document.getElementById('loading').textContent = '加载失败：' + err.message;
  }
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    p = Path(a.bin)
    raw = p.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    html = HTML.replace("__B64__", b64)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    # 顺手把报告里的动作数念出来
    rep = p.parent / "report.json"
    n = "?"
    if rep.exists():
        try:
            j = json.loads(rep.read_text(encoding="utf-8"))
            n = j.get("anim_count", j.get("frames", "?"))
        except Exception:
            pass
    print(f"bin {len(raw)/1024:.0f} KB → html {out.stat().st_size/1024/1024:.2f} MB, 动作 {n}")
    print(f"已写入：{out}")


if __name__ == "__main__":
    main()

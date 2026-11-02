# 04-字体 · 点阵中文字库

> 设备屏（240×320）默认的 Montserrat **没有中文字形**，写中文就是方块/空白。
> 这一份解决它：拉取点阵中文字体 → 按"实际用到的字"生成 LVGL 字体 → 放 spiffs 加载。

---

## 1. 选了哪两套（都是 OFL-1.1，可商用、可改、可嵌）

| 字体 | 仓库 | 特点 | 12px 预览 |
|---|---|---|---|
| **Fusion Pixel（缝合像素）** | `TakWolf/fusion-pixel-font` | 笔画更饱满，12px 下更清楚 | [预览_12px.png](预览_12px.png) 上半 |
| **Ark Pixel（方舟像素）** | `TakWolf/ark-pixel-font` | 略瘦，字面更"秀气" | 同图下半 |

两套都提供 8 / 10 / 12 / 16px，且每个尺寸有 `proportional`（比例）与 `monospaced`（等宽）。
我们取 **12px proportional zh_hans**（简体）——UI 用比例字更自然。

许可：OFL-1.1（`OFL.txt` / `OFL-ark.txt` 已在目录里，随字体一起保留）。

## 2. 怎么拉（可迁移：换电脑一条命令）

```bash
./拉取字体.sh            # 下载 release zip + 解压 + 校验 sha256
```

脚本下载的是 GitHub Release（**不要 clone 仓库**，仓库里只有源码，成品在 release）：
`https://github.com/TakWolf/fusion-pixel-font/releases/download/2026.10.25/...-12px-proportional-ttf-v2026.10.25.zip`

## 3. 怎么变成固件里的字库（实测跑通的命令）

```bash
# ① 生成 LVGL 二进制字体（放 spiffs，运行时 lv_font_load 加载）
npx --yes lv_font_conv@1.5.2 --size 12 --bpp 1 --format bin \
  --font fusion-pixel-12px-proportional-zh_hans.ttf \
  -r 0x20-0x7E --symbols "背单词设置连接道馆…" --no-kerning \
  -o ui12.bin

# ② 或者生成 C 数组（编进固件，做兜底/首启用）
npx --yes lv_font_conv@1.5.2 --size 12 --bpp 1 --format lvgl ... -o ui12.c
```

> ⚠️ 生成的 `.c` 里默认是 `#else #include "lvgl/lvgl.h"`，本仓库要改成 `#include "lvgl.h"`（managed component 的 include 路径），否则编译报 `lvgl/lvgl.h: No such file`。生成后 sed 一下即可。

**关键参数**：`--bpp 1` = 真 1 位点阵（黑白、无灰阶，最省空间）；`--no-kerning` = 不要字距调整（点阵字不需要，还省表）。
`--symbols` 只放**实际用到的字**，不放全字库。

## 4. 空间账（实测，不是估的）

| 内容 | 体积 |
|---|---|
| 138 个界面用字 + 全 ASCII，12px 1bpp | **3.6 KB**（bin）／44 KB（等价 C 源码） |
| 换算 | ≈ **26 字节/字** |
| 界面用字约 200 个 | ≈ 5–6 KB |
| 连词库用字一起（3000 字） | ≈ 80 KB |
| 22px 卡片释义（800 常用字） | ≈ 50 KB |

⇒ **完全放得下**。存放位置：`storage` 分区（spiffs），不编进 app
（原因：出厂 app 已占 2.56MB/3MB，只剩 448KB，见 `03-固件补丁/README.md`）。

## 5. 三档字号（对应 `04-AI Passport 竖屏版式` 的坐标表）

| 用途 | 尺寸 | 说明 |
|---|---|---|
| 界面/状态/列表 | **12px** | 一行能放 18 个汉字（216px 内容宽） |
| 按钮/标题 | **16px** | 13 个汉字 |
| 单词卡释义/例句 | **22px** | 9 个汉字；英文单词用 32px ASCII |

每档一个 bin 文件：`ui12.bin` / `ui16.bin` / `card22.bin`。

## 6. 加载方式（LVGL 9）

```c
// 从 spiffs 加载二进制字体
lv_font_t *ui12 = lv_font_load("S:/fonts/ui12.bin");   // 驱动注册的盘符按实际改
lv_obj_set_style_text_font(label, ui12, 0);
```

> 一个坑：`lv_font_load` 需要 `LV_FONT_FMT_TXT_LARGE`/binfont 支持打开（LVGL 9 默认带）。
> 加载失败要打日志并回落到 Montserrat，**不能白屏**。

## 7. 落地顺序（一次只改一处）

1. 定字体（Fusion 还是 Ark）→ 生成 `ui12.bin` + 加载器 + 一个中文示例页（真机看一眼）
2. 配网页 `WiFi Setup` 换中文（该页文案少，风险最低）
3. 主菜单/设置换中文
4. 单词卡换中文（22px，靠词库字表生成子集；**这时才需要全词库字表**）

## 8. 目录

```
04-字体/
├── 拉取字体.sh          # 下载 + 解压 + 校验
├── README.md            # 本文件
├── 预览_12px.png        # 两套字体的 12px 实际效果
├── OFL.txt / OFL-ark.txt
├── fusion-pixel-12px-proportional-zh_hans.ttf   （不进 git，脚本拉）
└── ark/ark-pixel-12px-proportional-zh_hans.ttf  （不进 git，脚本拉）
```

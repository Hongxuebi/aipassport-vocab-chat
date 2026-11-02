# 03-固件补丁 · 扫码配网（wprov）

> 这一份是**我们自己的固件改动**，叠在 FoloToy 官方仓库上。换电脑、换目录、重装系统，照这份就能重建出同一份固件。
> 生成时间：2026-10-25 ｜ 真机验证：✅ 网页能开、列表能出、提交能收、失败能回热点

---

## 1. 基线（缺一不可，别用别的版本拼）

| 项 | 值 |
|---|---|
| 上游仓库 | `https://gitee.com/folotoy/ai-passport.git`（GitHub 同名，MIT） |
| **上游 commit** | **`a06235e`**（2026-10-09，`main`） |
| ESP-IDF | **v5.5.3**（`dependencies.lock` 锁死；5.4 编不过 BSP 依赖） |
| 目标芯片 | ESP32-C3，8MB flash，无 PSRAM |
| 本机 IDF 路径 | `$HOME/esp/esp-idf-5.5.3-gh`（GitHub 递归 clone，子模块齐全） |
| 补丁文件 | `0001-wprov-扫码配网.patch`（10 个文件，108KB） |

> ⚠️ gitee 的**浅克隆**（`--depth 1`）会缺 `micro-ecc` 那个 commit，cmake 直接失败 —— 用 GitHub 的 `--recursive --shallow-submodules` 克隆。
> ⚠️ IDF 5.5.3 的 python 环境里**没有 cmake/ninja**，要 `pip install "cmake==3.31.6" ninja`。

## 2. 一条命令重建

```bash
./重建固件.sh                       # 默认重建到 ../_参考仓库/folotoy-ai-passport
./重建固件.sh ~/work/aipassport-fw  # 或指定任意新目录（可迁移）
```

脚本做的事：clone → 切到 `a06235e` → 打补丁 → `idf.py set-target esp32c3` → `idf.py build`。
产物：`build/FoloToy-AI-Passport.bin`，用 `../_工具/刷固件.sh <目录>` 烧。

## 3. 补丁里有什么（10 个文件）

| 文件 | 作用 |
|---|---|
| `components/wprov/include/wprov.h` | 配网对外 API（起热点/扫描/连接/存取凭据/状态） |
| `components/wprov/wprov.c` | 状态机：APSTA 起热点 → 扫描 → "先连后存" → 失败回热点 |
| `components/wprov/wprov_http.c` | 配网页（服务端发 HTML + JS 取列表）+ 通配 DNS（弹窗认证）+ 请求日志 |
| `components/wprov/src/qrcodegen.c/.h` | QR 码生成（nayuki，MIT/公域） |
| `components/wprov/CMakeLists.txt` | 组件声明（不依赖 LVGL，可被别处复用） |
| `main/demo_provision.c` | 第 8 个菜单页 `WiFi Setup`：二维码 + 热点名/口令 + 扫到的 WiFi + 状态 |
| `main/demo.h` / `main/main.c` / `main/CMakeLists.txt` | 接线：菜单注册、`app_demo_back()`、开机静默自动连 |

## 4. 关键设计（改之前先看这几条，都是踩过坑换来的）

1. **先连上、再落盘**：只有真的拿到 IP 才写 NVS；失败保留上一组可用凭据 → 不会被一次手误弄失联。
2. **起/切 WiFi 的顺序**（顺序错了会 panic，`MEPC=0x0`）：
   `esp_wifi_stop()` → 建 STA netif（必须在 start 之前）→ `set_mode(WIFI_MODE_STA)` → `set_config` → `esp_wifi_start()` → `esp_wifi_connect()`。
3. **停 httpd 要等响应发完**：连接流程里先 `vTaskDelay(400ms)` 再动网络，否则 worker 会在 httpd 回包途中拆它（use-after-free）。
4. **HTTP 路由**：必须设 `cfg.uri_match_fn = httpd_uri_match_wildcard`，然后只注册一个 `/*` 入口按路径分发。
   否则：（a）通配不生效；（b）`max_uri_handlers` 只有 8 个，注册多了直接丢；（c）404 回调靠不住。
5. **屏上文字一律 ASCII**：Montserrat 没有中文字形，写中文等于什么都不显示。
6. **AP 名固定**：换名字会让二维码里的旧名字对不上，手机扫了也加不进去。
7. **失败不要拆 DNS/HTTP**：每拆一次，最大连续空闲块从 69KB 掉到 31KB（WiFi 模式切换的碎片），再试两次热点就起不来了。

## 4.5 多网络（v0.3 起）

- NVS 里存 **3 个**网络：`s0..s2` / `p0..p2` + `n`；新的成功连接提到第 0 位、去重
- **开机依次尝试**：每个最多等 12s，连上就停（配网页里仍是 25s）
- **旧固件自动迁移**：只有 `ssid`/`pass` 两个键的老设备，开机自动搬进第 0 槽，不用重配
- 桌面用 `wprov_saved_count()` / `wprov_forget_all()`，网页侧暂未露"忘记"入口

## 4.6 开机快连（v0.4 起）

- 网络任务**提到最前面**，和屏幕初始化并行跑（不等外设就绪）
- 记住上次那个 AP 的 **BSSID + 信道**（NVS `bssid0`/`chan0`）→ 第 0 个网络**跳过扫描直连**
- 第 0 个给 **8 秒**；其余网络 12 秒
- **第二遍兜底**：快连失败（路由器换信道/换 AP 节点）→ 全部网络按普通扫描再试一遍，不会把已知能用的网跳过
- 实测（这台机器、这个环境）：

| 指标 | 数值 |
|---|---|
| 上电 → 网络模块开始连 | **0.33 s**（以前要等外设初始化完，约 0.9 s） |
| 上电 → 拿到 IP | **2.12 s** |
| 其中 connect → IP | **1.23 s**（BSSID 快连，扫描已跳过） |

> 结论：**扫描不是瓶颈**，1.2 秒主要花在 WiFi 启动 + 四次握手 + DHCP。想再快只能上静态 IP（风险高，暂不做）。

## 4.7 两条兜底（v0.5 起）

**A · 设备端选网**（网页彻底打不开时的最后一条路）
- 配网页上：`▲▼` 在扫到的前 5 个网络里移动（`>` 标记当前选中），`●` 直连
- 直连只用**已保存的密码**（`wprov_find_saved()`）；开放网络直接连；没存过密码就提示"用手机页"
- 不新增密码键盘（三键输密码不现实），所以 A 只解决"连过的网"

**B · 失败 3 次封顶**
- 连续失败达 3 次 → `s_fail_locked`，不再自动重试、`wprov_request_connect()` 直接拒绝
- 屏上显示 `STOPPED (3 fails) / press OK to retry`，按 `●` 调 `wprov_reset_fail()`
- 目的：不再反复切 WiFi 模式（每切一轮最大连续块从 69KB 掉到 31KB，切两三轮热点就起不来了）
- 成功一次即清零

## 5. 真机验证清单（这版跑过的）

| 项 | 结果 |
|---|---|
| 开机自动连已保存网络 | ✅ 上电 2.6s，`STA 拿到 IP LAN_HOST` |
| 老凭据迁移 | ✅ 升级后开机直接 `共记住 1 个网络` 并连上（没重配） |
| 进配网页出二维码 | ✅ `二维码 116x116（29 模块 × 4）` |
| 安卓探测路径 302 | ✅ `GET /generate_204_<随机> -> 302 /` |
| 页面与列表 | ✅ `GET / -> page`、`GET /api/scan -> json (16 个)` |
| 手机提交 | ✅ `POST /api/connect (36 B)` → `开始连 HOME-AP` |
| 错密码失败路径 | ✅ `reason=14` → 热点自动回来 |
| 成功路径 | ✅ 14:19 那次 `STA 拿到 IP LAN_HOST` + `凭据已保存` |

## 6. 回滚

| 想退到 | 命令 |
|---|---|
| 官方 demo | `../_工具/刷固件.sh <上游 clone 目录>` |
| **出厂固件** | `../_固件备份/恢复原厂.sh`（8MB 整片，写完自动 verify） |

## 7. 还没做的兜底（下一版）

1. **存 3 个网络**：别人家配一次、回家不用再配（按 RSSI 挑能用的）
2. **设备端 ▲▼ 选网**：网页彻底打不开时的最后一条路
3. **进配网页时"只换一次"热点名**：让手机重新探测，同时二维码跟着重建（这次撤掉是因为没重建二维码）
4. **失败后限制重试次数**：连续失败 3 次就停在提示页，不再反复切模式（避免碎片累积）

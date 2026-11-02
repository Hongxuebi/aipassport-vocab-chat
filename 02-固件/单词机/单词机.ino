/*
 * 单词机固件 v0.2
 * 目标板：ESP32-C3（Arduino IDE 选 "ESP32C3 Dev Module"）
 * 屏   ：ST7789 240×320 / ST7735 128×160（SPI）
 * 存储 ：TF 卡（存卡片图 + 待回传队列）
 * 交互 ：3 个按键（翻面 / 不认识 / 认识）
 *
 * 依赖库（库管理器里装）：
 *   Adafruit GFX Library
 *   Adafruit ST7735 and ST7789 Library
 *   ArduinoJson (v7；用 v6 的把 JsonDocument 换成 DynamicJsonDocument(8192))
 *
 * 设计原则（见 ../00-方案.md）：
 *   1) 卡片是预渲染好的图，设备不解码、不排版、不要字库
 *   2) 先显示 SD 里的缓存，再联网；断网照样能背
 *   3) 评分记在 RTC 内存里，回传成功才清；掉电前会先落盘
 *
 * ⚠️ 为什么用 light sleep 而不是 deep sleep：
 *    C3 的 deep sleep 只能被 RTC GPIO（0~5）唤醒，而本方案按键在 20/21，
 *    deep sleep 后按不动。light sleep 支持任意 GPIO 唤醒，还有 ~0.2mA，
 *    500mAh 电池能撑一两周。真要用 deep sleep，就把三个键全改到 0/3 上。
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_GFX.h>
#include <ArduinoJson.h>
#include <esp_sleep.h>
#include <driver/gpio.h>

// ==================== 用户配置 ====================
#define WIFI_SSID   "你的WiFi名"
#define WIFI_PASS   "你的WiFi密码"
#define SERVER_URL  "http://LAN_HOST:8791"   // 跑「单词机.py 服务」的那台机器

#define SCR_W 240
#define SCR_H 320
#define USE_ST7789 1        // 1=ST7789 240x320；0=ST7735 128x160（同时改上面两行）

// 引脚（C3 SuperMini）。⚠️ GPIO2/8/9 是启动脚，改前看 00-方案.md 附录 A
#define PIN_SCLK     4
#define PIN_MOSI     6
#define PIN_MISO     5
#define PIN_TFT_CS   7
#define PIN_TFT_DC   1
#define PIN_TFT_RST  2
#define PIN_BL       10
#define PIN_SD_CS    8
#define PIN_BTN_FLIP 0      // 翻面
#define PIN_BTN_NO   20     // 不认识
#define PIN_BTN_YES  21     // 认识

#define IDLE_SLEEP_MS 120000UL   // 空闲 2 分钟进 light sleep
#define STRIP_H 8                // 分块刷屏行数：240*8*2 = 3.7KB 缓冲

// ==================== 全局 ====================
#if USE_ST7789
#include <Adafruit_ST7789.h>
Adafruit_ST7789 tft(&SPI, PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST);
#else
#include <Adafruit_ST7735.h>
Adafruit_ST7735 tft(&SPI, PIN_TFT_CS, PIN_TFT_DC, PIN_TFT_RST);
#endif

#define MAX_CARDS 160
#define MAX_PEND  32

struct CardRef {
  char id[14];
  uint32_t fsz;
  uint32_t bsz;
  uint8_t type;      // 0=普通词卡 1=收工卡
};
CardRef cards[MAX_CARDS];
int cardN = 0;
char packDay[12] = "";

// 待回传（RTC 内存，light sleep 不丢；掉电丢，所以每次也往 SD 写一份）
RTC_NOINIT_ATTR char pendId[MAX_PEND][14];
RTC_NOINIT_ATTR int8_t pendGrade[MAX_PEND];
RTC_NOINIT_ATTR bool pendInit;

static uint16_t strip[SCR_W * STRIP_H];
static File rleFile;
static uint8_t rbuf[512];
static int rp = 0, rlen = 0;
static uint16_t runLeft = 0, curColor = 0;
static bool rleEnded = false;

static int cur = 0;
static bool showingBack = false;
static unsigned long lastAct = 0;

char curDay[12] = "";   // 同步后拿到的新日期

// ==================== RLE 解码 ====================
static inline uint8_t rbyte() {
  if (rp >= rlen) {
    rlen = rleFile.read(rbuf, sizeof(rbuf));
    rp = 0;
    if (rlen <= 0) { rleEnded = true; return 0; }
  }
  return rbuf[rp++];
}

static inline uint16_t nextPixel() {
  if (rleEnded) return 0xFFFF;
  if (runLeft == 0) {
    runLeft = rbyte();
    uint8_t hi = rbyte();
    uint8_t lo = rbyte();
    curColor = ((uint16_t)hi << 8) | lo;
    if (rleEnded) return 0xFFFF;
    if (runLeft == 0) runLeft = 1;
  }
  runLeft--;
  return curColor;
}

void showRLE(const char *path) {
  rleEnded = false;
  rp = rlen = 0;
  runLeft = 0;
  rleFile = SD.open(path, FILE_READ);
  if (!rleFile) {
    tft.fillScreen(ST77XX_BLACK);
    tft.setTextColor(ST77XX_RED);
    tft.setCursor(6, 6);
    tft.print("缺文件: ");
    tft.print(path);
    return;
  }
  for (int y = 0; y < SCR_H; y += STRIP_H) {
    int h = (y + STRIP_H > SCR_H) ? (SCR_H - y) : STRIP_H;
    for (int i = 0; i < SCR_W * h; i++) strip[i] = nextPixel();
    tft.drawRGBBitmap(0, y, strip, SCR_W, h);
  }
  rleFile.close();
}

void drawMsg(const char *msg, uint16_t color = ST77XX_WHITE) {
  tft.fillScreen(ST77XX_BLACK);
  tft.setTextColor(color);
  tft.setTextSize(1);
  tft.setCursor(8, SCR_H / 2 - 8);
  tft.print(msg);
}

// ==================== 按键 ====================
struct Btn { int pin; bool last; };
Btn bFlip{PIN_BTN_FLIP, false}, bNo{PIN_BTN_NO, false}, bYes{PIN_BTN_YES, false};

bool pressed(Btn &b) {
  bool v = (digitalRead(b.pin) == LOW);
  bool edge = v && !b.last;
  b.last = v;
  return edge;
}

// ==================== 索引 ====================
bool loadIndex() {
  File f = SD.open("/manifest.json", FILE_READ);
  if (!f) return false;
  JsonDocument doc;
  DeserializationError e = deserializeJson(doc, f);
  f.close();
  if (e) { Serial.printf("manifest 解析失败: %s\n", e.c_str()); return false; }

  strncpy(packDay, doc["date"] | "", sizeof(packDay) - 1);
  packDay[sizeof(packDay) - 1] = 0;

  cardN = 0;
  for (JsonObject c : doc["cards"].as<JsonArray>()) {
    if (cardN >= MAX_CARDS) break;
    const char *id = c["id"] | "";
    strncpy(cards[cardN].id, id, sizeof(cards[cardN].id) - 1);
    cards[cardN].id[sizeof(cards[cardN].id) - 1] = 0;
    cards[cardN].fsz = c["fsz"] | 0;
    cards[cardN].bsz = c["bsz"] | 0;
    cards[cardN].type = (strncmp(id, "zz_end", 6) == 0) ? 1 : 0;
    cardN++;
  }
  Serial.printf("卡片 %d 张，包日期 %s\n", cardN, packDay);
  return cardN > 0;
}

// ==================== 联网 ====================
bool wifiUp(uint32_t timeoutMs = 8000) {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) delay(80);
  bool ok = (WiFi.status() == WL_CONNECTED);
  Serial.printf("WiFi %s %s\n", ok ? "ok" : "FAIL", WiFi.localIP().toString().c_str());
  return ok;
}

bool fetchToSD(const String &url, const String &path, uint32_t expect) {
  if (expect && SD.exists(path.c_str())) {
    File ex = SD.open(path.c_str(), FILE_READ);
    uint32_t got = ex ? ex.size() : 0;
    if (ex) ex.close();
    if (got == expect) return true;
  }
  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(url)) return false;
  int code = http.GET();
  if (code != 200) { http.end(); return false; }

  File out = SD.open(path.c_str(), FILE_WRITE);
  if (!out) { http.end(); return false; }
  WiFiClient *s = http.getStreamPtr();
  uint8_t buf[512];
  int remain = http.getSize();
  unsigned long guard = millis();
  while (millis() - guard < 15000) {
    size_t n = s->readBytes(buf, sizeof(buf));
    if (n == 0) break;
    out.write(buf, n);
    if (remain > 0) { remain -= n; if (remain <= 0) break; }
  }
  out.close();
  http.end();
  return true;
}

// 返回 true = 有可用的包（含"沿用旧包"）
bool syncPack() {
  if (!wifiUp()) return cardN > 0;

  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(String(SERVER_URL) + "/manifest.json")) return cardN > 0;
  int code = http.GET();
  if (code != 200) { http.end(); return cardN > 0; }
  String body = http.getString();
  http.end();
  if (body.length() < 10) return cardN > 0;

  JsonDocument doc;
  if (deserializeJson(doc, body)) return cardN > 0;
  strncpy(curDay, doc["date"] | "", sizeof(curDay) - 1);

  bool sameDay = (packDay[0] && strcmp(packDay, curDay) == 0 && cardN > 0);

  File mf = SD.open("/manifest.json", FILE_WRITE);
  if (mf) { mf.print(body); mf.close(); }
  if (!sameDay) cur = 0;

  JsonArray arr = doc["cards"].as<JsonArray>();
  int total = arr.size(), i = 0;
  tft.fillRect(0, SCR_H - 22, SCR_W, 22, ST77XX_BLACK);
  tft.setTextColor(ST77XX_WHITE);
  tft.setTextSize(1);
  tft.setCursor(6, SCR_H - 16);

  for (JsonObject c : arr) {
    const char *id = c["id"] | "";
    char pf[40], pb[40];
    snprintf(pf, sizeof(pf), "/c/%s_f.rle", id);
    snprintf(pb, sizeof(pb), "/c/%s_b.rle", id);
    fetchToSD(String(SERVER_URL) + "/c/" + id + "_f.rle", pf, c["fsz"] | 0);
    fetchToSD(String(SERVER_URL) + "/c/" + id + "_b.rle", pb, c["bsz"] | 0);
    i++;
    tft.setCursor(6, SCR_H - 16);
    tft.printf("同步 %d/%d  ", i, total);
    if (total) tft.fillRect(0, SCR_H - 6, (SCR_W - 12) * i / total, 4, ST77XX_GREEN);
  }
  return loadIndex();
}

// ==================== 回传 ====================
void pendAdd(const char *id, int grade) {
  for (int k = 0; k < MAX_PEND; k++) {
    if (pendId[k][0] == 0) {
      strncpy(pendId[k], id, 13);
      pendId[k][13] = 0;
      pendGrade[k] = (int8_t)grade;
      return;
    }
  }
}

void pendFlush() {
  int n = 0;
  for (int k = 0; k < MAX_PEND; k++) if (pendId[k][0]) n++;
  if (!n) return;
  if (!wifiUp(6000)) return;

  String js = "[";
  for (int k = 0; k < MAX_PEND; k++) {
    if (!pendId[k][0]) continue;
    if (js.length() > 1) js += ",";
    js += "{\"id\":\"";
    js += pendId[k];
    js += "\",\"grade\":";
    js += String((int)pendGrade[k]);
    js += "}";
  }
  js += "]";

  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(String(SERVER_URL) + "/review")) return;
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(js);
  http.end();
  if (code == 200) {
    Serial.printf("回传 %d 条 OK\n", n);
    memset(pendId, 0, sizeof(pendId));
    memset(pendGrade, 0, sizeof(pendGrade));
  } else {
    Serial.printf("回传失败 code=%d（留着下次发）\n", code);
  }
}

// ==================== 显示 / 睡眠 ====================
void showCard(int i, bool back) {
  if (i < 0 || i >= cardN) return;
  char p[48];
  snprintf(p, sizeof(p), "/c/%s_%s.rle", cards[i].id, back ? "b" : "f");
  showRLE(p);
}

void sleepNow() {
  pendFlush();
  tft.fillScreen(ST77XX_BLACK);
  digitalWrite(PIN_BL, LOW);
  WiFi.mode(WIFI_OFF);

  gpio_wakeup_enable((gpio_num_t)PIN_BTN_FLIP, GPIO_INTR_LOW_LEVEL);
  gpio_wakeup_enable((gpio_num_t)PIN_BTN_NO, GPIO_INTR_LOW_LEVEL);
  gpio_wakeup_enable((gpio_num_t)PIN_BTN_YES, GPIO_INTR_LOW_LEVEL);
  esp_sleep_enable_gpio_wakeup();

  esp_light_sleep_start();          // ← 按任意键回来

  gpio_wakeup_disable((gpio_num_t)PIN_BTN_FLIP);
  gpio_wakeup_disable((gpio_num_t)PIN_BTN_NO);
  gpio_wakeup_disable((gpio_num_t)PIN_BTN_YES);
  digitalWrite(PIN_BL, HIGH);
  bFlip.last = bNo.last = bYes.last = true;   // 防止唤醒那一下当成一次按键
  showCard(cur, false);
  showingBack = false;
  lastAct = millis();
}

// ==================== 主流程 ====================
void setup() {
  Serial.begin(115200);
  delay(150);

  pinMode(PIN_BTN_FLIP, INPUT_PULLUP);
  pinMode(PIN_BTN_NO, INPUT_PULLUP);
  pinMode(PIN_BTN_YES, INPUT_PULLUP);
  pinMode(PIN_BL, OUTPUT);
  digitalWrite(PIN_BL, HIGH);

  SPI.begin(PIN_SCLK, PIN_MISO, PIN_MOSI, -1);
#if USE_ST7789
  tft.init(SCR_W, SCR_H);
#else
  tft.initR(INITR_BLACKTAB);
#endif
  tft.setRotation(0);
  tft.fillScreen(ST77XX_BLACK);

  drawMsg("mount sd ...");
  if (!SD.begin(PIN_SD_CS, SPI, 20000000)) {
    drawMsg("SD 挂不上：检查接线/卡格式", ST77XX_RED);
    delay(4000);
    return;
  }
  if (!pendInit) { memset(pendId, 0, sizeof(pendId)); pendInit = true; }

  bool haveIndex = loadIndex();

  // ① 有缓存就立刻显示（目标：按键到看见字 <1 秒）
  if (haveIndex) {
    if (cur < 0 || cur >= cardN) cur = 0;
    showCard(cur, false);
  }

  // ② 再联网同步（失败不影响背书）
  bool ok = syncPack();
  if (!ok && !haveIndex) {
    drawMsg("没包也没网，先别背了", ST77XX_RED);
    delay(4000);
    return;
  }
  if (ok) {
    if (cur >= cardN) cur = 0;
    showCard(cur, false);
  }

  showingBack = false;
  lastAct = millis();
}

void loop() {
  if (pressed(bFlip)) {
    lastAct = millis();
    showingBack = true;
    showCard(cur, true);
  }

  if (pressed(bNo)) {
    lastAct = millis();
    if (cards[cur].type == 0) pendAdd(cards[cur].id, 0);
    cur = (cur + 1) % cardN;
    showingBack = false;
    showCard(cur, false);
    if (cur == 0) pendFlush();
  }

  if (pressed(bYes)) {
    lastAct = millis();
    if (cards[cur].type == 0) pendAdd(cards[cur].id, 2);
    cur = (cur + 1) % cardN;
    showingBack = false;
    showCard(cur, false);
    if (cur == 0) pendFlush();
  }

  if (millis() - lastAct > IDLE_SLEEP_MS) sleepNow();
  delay(4);
}

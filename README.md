# 航班规划与特价雷达

个人用的航班搜索和特价提醒工具：多个数据源一起查价，按「最便宜 / 最快 / 综合最优」排序，同一航班比较航司官网和各 OTA 的价格，用日历找最便宜的日子，并定时监控你关心的航线。

没有填 API key 时程序用**演示数据**运行，可以先体验界面；填上 key 之后就是真实航班。

## 功能

| 页面 | 做什么 |
| --- | --- |
| 搜索比价 | 单程 / 往返实时搜索；最便宜、最快、综合最优三个榜单；价格-时长散点图（标出「性价比前沿」）；红眼航班、衔接过紧提示 |
| 指定航司 / 航班号 | 只看某几家航司（可输入「英航」「JU」「Air Serbia」），或查某个具体航班号；结果顶部列出这条航线上每家航司的最低价和是否有直飞 |
| 多城市：自动排顺序 | 给出要去的几个城市（最多 6 个）和每个城市停留天数，试遍所有先后顺序，找机票总价最低的走法；出发日可浮动 ±3 天 |
| 多城市：多段航线 | 顺序固定的多段行程，每段分别查单程票，列出每段候选航班和合计价格 |
| 各渠道比价 | 同一个航班在航司官网、Trip.com、Expedia 等卖家的价格、票价类型和行李说明，一键去预订；另附 Google Flights、Skyscanner、Trip.com、携程国内版、Kayak、去哪儿、飞猪的搜索链接 |
| 灵活日期 | 一段日期内每天最低价的日历热力图，列出最便宜的 5 天，点击即查实时价 |
| 任意目的地 | 从你的机场出发，某个月最便宜能去哪里 |
| 航线监控 | 定时查价并记录历史；低于目标价，或比历史中位数低 30% 以上时，通过邮件或 Telegram 提醒 |
| 特价网站 | 读取 Secret Flying 等特价网站的 RSS，和你出发地相关的排在前面，新帖会推送提醒 |

地点可以直接输入中文或英文城市名，如「伦敦」「贝尔格莱德」「Belgrade」，也可以输入机场代码。城市有多个机场时（伦敦、巴黎、上海等）会同时搜索所有机场；输入「伦敦希思罗」「上海浦东」则只查那个机场。输入时会自动提示。

**综合最优**的算法：广义成本 = 价格 + 时间价值 × 总小时 + 中转惩罚 × 中转次数 + 红眼 / 衔接过紧惩罚。时间价值可以在页面上用滑块调，设为 0 就等于「只看价格」。

## 安装和启动

需要 Python 3.11 或更高版本。

**Windows**：双击 `start.bat`。第一次会自动创建环境、安装依赖并生成 `.env`，然后打开浏览器。

**macOS / Linux**：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env      # 按下面说明填写
flightplanner serve       # 打开 http://127.0.0.1:8010（被占用时自动换端口，以窗口里显示的地址为准）
```

### 在平板 / 手机上用

电脑开着程序时，同一个 Wi-Fi 下的 iPad、安卓平板或手机都能打开：

1. 双击 `start-pad.bat`（代替 `start.bat`）。
2. 第一次 Windows 会弹防火墙提示，勾选「专用网络」并允许。
3. 黑窗口里会显示一个地址，形如 `http://192.168.x.x:8010/?key=…`，在平板浏览器里打开它。
4. 想像 App 一样用：iPad 在 Safari 点「分享 → 添加到主屏幕」；安卓在 Chrome 菜单里点「添加到主屏幕」。

访问码 `key` 防止同一网络里的其他人用你的 API 额度，不要发给别人；删除 `data/lan_key.txt` 再重启就会换一个。学校宿舍等公共 Wi-Fi 常常禁止设备互相访问，这种情况下平板连不上，可以用电脑开热点让平板连。电脑关机或关掉黑窗口后平板就打不开了。

命令行也能查：

```bash
flightplanner search MAN PVG 2026-12-01 --return 2026-12-15 --mode cheapest
flightplanner check       # 立即检查所有航线监控
```

## 填写 API key（.env）

三个数据源都可以免费注册，填哪个用哪个：

| 变量 | 数据源 | 用途 | 费用 |
| --- | --- | --- | --- |
| `SERPAPI_KEY` | [SerpApi](https://serpapi.com) 的 Google Flights 接口 | 主数据源；各卖家分别报价 | 有小额免费额度；Developer 套餐 $75/月 5,000 次 |
| `DUFFEL_TOKEN` | [Duffel](https://app.duffel.com) | 航司直连报价，第二数据源 | 搜索基本免费 |
| `TRAVELPAYOUTS_TOKEN` | [Travelpayouts](https://www.travelpayouts.com) Data API | 灵活日期日历、任意目的地（缓存价） | 免费 |

- `SERPAPI_MONTHLY_LIMIT` 是每月调用上限，到了就停，防止超预算。每次搜索用 1 次；查看一个航班的各渠道价格再用 1 到 2 次。
- 查询结果缓存 `CACHE_MINUTES` 分钟，同样的条件不重复扣费。
- 没有 Travelpayouts 时，灵活日期会改为逐天实时查询（最多 7 天），任意目的地不可用。
- 「自动排顺序」有 Travelpayouts 时用免费缓存价估算，不消耗 SerpApi；没有时改用实时查询，单次最多 20 次搜索（3 个城市、出发日不浮动并回到出发地约需 18 次），所以建议填上免费的 Travelpayouts。
- 不同数据源货币不同时，用欧洲央行汇率（frankfurter.app，免费）换算成 `CURRENCY` 再比较。

### 提醒

- 邮件：填 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`ALERT_EMAIL_TO`。Gmail 需要用「应用专用密码」，主机 `smtp.gmail.com`，端口 587。
- Telegram：用 @BotFather 创建机器人得到 `TELEGRAM_BOT_TOKEN`，给机器人发一条消息后访问 `https://api.telegram.org/bot<token>/getUpdates` 找到 `chat.id` 填入 `TELEGRAM_CHAT_ID`。
- 页面「航线监控」里有「发送测试提醒」按钮。
- 程序开着时每 `WATCH_INTERVAL_HOURS` 小时自动检查一次；想在关机时也检查，可以用 Windows 任务计划程序定时运行 `flightplanner check`。

## 注意

- 显示的价格来自查询时刻，最终以卖家页面为准。各渠道的行李政策不同，比价时看清行李说明（程序会列出，但不会自动把行李费加进价格）。
- Travelpayouts 的价格是缓存价，只用来挑日期，下单前点「查实时价」。
- 程序不抓取航司或 OTA 网站，也不登录任何社交账号，只使用正式 API 和公开 RSS。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

代码结构：

```
src/flightplanner/
  providers/     各数据源适配（serpapi / duffel / travelpayouts / demo），统一转成 models.py 的格式
  scoring.py     广义成本、排序、帕累托前沿、跨数据源去重
  places.py      城市 / 机场 / 航司名称匹配（中英文）
  trips.py       多城市：多段航线、自动排顺序
  service.py     组合数据源：搜索、各渠道比价、灵活日期、任意目的地
  deals/         特价雷达：watcher（航线监控）、feeds（RSS）、notify（邮件 / Telegram）
  links.py       各比价网站的搜索链接
  api.py         网页后端（FastAPI）
  web/           前端（原生 HTML / JS，无需构建）
```

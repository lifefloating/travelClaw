# Tripadvisor Geo Crawler Plan

Date: 2026-05-28

## 目标

做一个 Python + Scrapling 技术栈的 Tripadvisor geo 爬虫，主抓英文站 `https://www.tripadvisor.com/` 的地理实体数据，输出符合 `vendor-data-delivery.html` 的 `geo/` 数据包，并在 R2 配置完整时上传到:

```text
qiqi/geo/tripadvisor/<YYYY-MM-DDTHHMMSSZ>/
```

R2 配置缺失时只生成本地包，不上传。

## 已确认材料

- 当前仓库基本为空，需要从项目骨架开始建。
- 交付规范要求:
  - `manifest.json` 必填。
  - 主数据: `geo.ndjson.gz`。
  - 媒体: `media.ndjson.gz` + `media/` 二进制文件。
  - `_READY` 必须最后上传。
  - 包路径固定在 `qiqi/<focus>/<source>/<timestamp>/`。
  - 本次 focus 是 `geo`，source 是 `tripadvisor`。
- 历史 Google POI 样例可复用思想:
  - `local_id` / `record_id` 稳定编号。
  - 图片条目记录 `source_url`、`local_path`、`file_name`、`download_status`、`content_type`。
  - `scrape_meta.primary_destination`、`source_destinations`、`source_input_ids` 这类追踪字段。
- `raw.json` 是 NDJSON 流式文件，不是 JSON 数组；新爬虫也要按流式追加写，避免内存堆积。
- 参考项目 `TripAdvisor-Review-Scraper` 可借鉴:
  - 必须使用 `www.tripadvisor.com`，不要用地区域名。
  - `Accept-Language: en-US,en;q=0.9`。
  - Tripadvisor GraphQL endpoint: `https://www.tripadvisor.com/data/graphql/ids`。
  - URL 中 `g<geo_id>` / `d<detail_id>` 的解析方式。

## 页面观察

用 jshook 交互检查了英文站 Kyoto 流程:

1. 首页搜索框 action 是 `/Search`，参数包含 `q`、`searchNearby=false`、`searchSessionId`。
2. 搜索 `Kyoto` 后跳到:

```text
https://www.tripadvisor.com/Search?q=Kyoto&geo=1&ssrc=a&searchNearby=false&searchSessionId=...&offset=0
```

3. 搜索结果里 top destination 是:

```text
https://www.tripadvisor.com/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html
```

4. 目的地详情页标题:

```text
Kyoto, Japan: All You Must Know Before You Go (2026) - Tripadvisor
```

5. 详情页主要模块:
  - 首屏: 地点名、breadcrumb、review/contribution count、简介、hero gallery。
  - `Essential Kyoto`: Things to do / Places to stay / Food & drink。
  - `Travel Advice`: 月份气温、降雨、忙闲程度。
  - AI itineraries。
  - Editorial / guide modules。
  - Sponsored blocks。
  - FAQ。

6. 页面 JSON-LD 包含 breadcrumb、推荐 POI/酒店/餐厅/FAQ，但这些是详情页下方推荐内容，不等于 geo 自身数据。
7. 详情页网络请求里确认到 geo gallery GraphQL 请求形态:

```json
{
  "variables": {
    "locationId": 298564,
    "albumId": 101,
    "client": "t",
    "dataStrategy": "geo",
    "filter": {
      "mediaGroup": "ALL_INCLUDING_RESTRICTED",
      "mediaTypes": ["PHOTO", "PHOTO_360", "VIDEO"]
    },
    "subAlbumId": 101,
    "offset": 0,
    "limit": 20
  },
  "extensions": {
    "preRegisteredQueryId": "e451fc43b6a61cab"
  }
}
```

## 图片策略

图片只抓 geo 自身图片，不从详情页下方推荐、广告、攻略帖中取图。

允许来源:

- 详情页首屏 hero gallery。
- GraphQL 中 `dataStrategy="geo"` 的 gallery/media 请求。
- `og:image` 仅作为兜底 thumbnail，不作为批量图片来源。

明确排除:

- `Essential Kyoto` 里的 POI / 酒店 / 餐厅卡片图片。
- `Travel Advice` 以下的 AI itinerary、editorial、guide、FAQ、sponsored/ad blocks 图片。
- JSON-LD 中推荐 POI/酒店/餐厅对象的 `image`。
- 页面滚动到底部后懒加载出来的 feed 图片。

限制:

- 单个城市/目的地最多下载 10,000 张图片。
- POC 默认更小，例如每个 geo 50-200 张，便于验证结构。
- 只接受 `image/jpeg`、`image/png`、`image/webp`。
- 单文件最大 25 MB，最大边长 8000 px，按交付规范校验。
- 去重键优先用 Tripadvisor media id，其次 canonical image URL。

图片量预估:

- Tripadvisor geo gallery 的 `totalMediaCount` 对热门城市非常大，不能按全量下载设计。
- 2026-05-28 用 gallery metadata 小样本看到:
  - Kyoto: 244,547
  - Paris: 2,151,397
  - New York City: 1,665,610
  - San Francisco: 473,746
  - Istanbul: 944,600
  - Prague: 989,628
  - Lisbon: 917,794
  - New Orleans: 328,744
  - Yosemite National Park: 32,712
- 上面是 Tripadvisor album 返回的媒体总数口径，实际下载时仍只保留图片类型，并受 `--max-images-per-geo` 硬上限约束。
- 正式默认按每个 geo 最多 10,000 张下载；20 个目的地理论上最多 200,000 张。

授权:

- manifest 和媒体行统一使用 `license="proprietary"`。
- attribution 统一使用 `© 2026 frank`。
- contact 使用 `frank <imshuazi@126.com>`。

## 数据模型

### places.ndjson.gz 是否输出

按交付文件，`geo/` 包主数据是 `geo.ndjson.gz`；`places.ndjson.gz` 是允许附带的 POI 文件，不是必填文件。规范原文含义是:

- `geo/` 包: 城市 / 地区类爬，地理实体优先接收。
- `geo/` 包里顺带抓到的 POI 行可以宽松接受，不强制落在 20 目的地内。
- 目录示例里 `places.ndjson.gz` 标注为“附属: 帖子里提到的 POI”，不是主文件。
- 校验规则也只是说明 `geo/` 包内 POI 行走宽松通道，没有要求必须存在。

本项目主数据仍是 `geo.ndjson.gz`。如果详情页已经稳定暴露相关 POI / 酒店 / 餐厅，并且能解析到满足 `places.ndjson.gz` 基础字段的记录，就附带输出 `places.ndjson.gz`；如果当前页面/接口抓不到或字段不足，则不生成该文件。附带 POI 图片不默认下载，避免把推荐模块图片和 geo 自身图片混在一起。

### geo.ndjson.gz

每个 destination / geo entity 一行。字段草案:

```json
{
  "record_id": "qiqi:tripadvisor:geo:298564",
  "source": "tripadvisor",
  "source_id": "g298564",
  "source_url": "https://www.tripadvisor.com/Tourism-g298564-Kyoto_Kyoto_Prefecture_Kinki-Vacations.html",
  "captured_at": "2026-05-28T09:50:00Z",
  "name": "Kyoto",
  "name_i18n": { "en": "Kyoto" },
  "kind_hint": "tripadvisor:city",
  "country_code": "JP",
  "center": { "lat": 35.0116, "lng": 135.7583 },
  "raw": {
    "title": "...",
    "meta": {},
    "breadcrumbs": [],
    "description": "...",
    "review_count_text": "244,547",
    "sections_seen": [],
    "tripadvisor_ids": { "geo_id": 298564 }
  }
}
```

坐标策略:

- 优先使用 seed 文件给的 WGS-84 `lat/lng`。
- 如果详情页或接口提供中心点，则记录在 `raw` 并可覆盖 seed。
- 若没有 `center`、`bbox`、`boundary` 三者之一，该行不写入正式包，放入 error report。
- Tripadvisor 页面通常不给 boundary；本期先以 `center` 为主，`bbox/boundary` 有源数据再填，不造数据。

### media.ndjson.gz

每张下载成功的 geo 图片一行:

```json
{
  "record_id": "qiqi:tripadvisor:media:298564:482391113",
  "source": "tripadvisor",
  "source_id": "482391113",
  "source_url": "https://dynamic-media-cdn.tripadvisor.com/...",
  "captured_at": "2026-05-28T09:50:00Z",
  "path": "media/298564/000001.jpg",
  "mime_type": "image/jpeg",
  "width": 1280,
  "height": 853,
  "caption": "source caption if present",
  "license": "proprietary",
  "attribution": "© 2026 frank",
  "depicts": {
    "record_id": "qiqi:tripadvisor:geo:298564"
  }
}
```

媒体行默认继承同一组授权和署名；实现时仍显式写入行内字段，方便独立校验。

### manifest.json

manifest 默认字段:

```json
{
  "vendor_id": "qiqi",
  "focus": "geo",
  "source": "tripadvisor",
  "package_version": 1,
  "license": "proprietary",
  "attribution": "© 2026 frank",
  "contact": {
    "name": "frank",
    "email": "imshuazi@126.com"
  }
}
```

### annotations.ndjson.gz

默认不生成。若后续需要保存简介、travel advice、FAQ，可以作为可选文件生成，`about.record_id` 指向 geo 行。

## 输入种子

支持 CSV / JSONL 两种输入，字段:

```text
name_cn,name_en,latitude,longitude,kind,country_code,tripadvisor_url(optional),tripadvisor_geo_id(optional)
```

默认内置用户给的 20 个目的地，外部文件可覆盖。已有 URL 或 `geo_id` 时跳过搜索发现，直接进详情页。

## 爬取流程

1. 读取 `.env` 和 seed 文件。
2. 对每个 seed:
  - 如果已有 `tripadvisor_url`，直接详情页。
  - 否则访问英文搜索页 `/Search?q=<name_en>&searchNearby=false`。
  - 选 top `Tourism-g...-Vacations.html` destination 结果。
  - 用 seed 坐标和结果名称做轻量校验，避免错选。
3. 进入详情页:
  - 提取 `geo_id`、canonical URL、title、meta、breadcrumb、description、review count、详情页模块摘要。
  - 解析 JSON-LD breadcrumb 和 FAQ，但不把推荐 POI 图片当 geo 图片。
4. 调用或复用页面触发的 GraphQL geo media 请求:
  - `dataStrategy=geo`。
  - 分页 `offset/limit`。
  - 到达 `--max-images-per-geo` 或无更多结果停止。
5. 下载白名单图片:
  - HEAD/GET 校验 MIME、大小。
  - 写入 `media/`。
  - 写 `media.ndjson` 行。
6. 实时追加写:
  - `geo.ndjson`、`media.ndjson`、`errors.ndjson` 都逐行 flush。
  - 断点状态写 `state.sqlite` 或 `state.jsonl`。
7. 结束后 gzip NDJSON，计算 sha256、size、row_count，生成 `manifest.json`。
8. R2 配置完整且启用上传时:
  - 上传所有文件。
  - 校验远端大小/etag 或至少本地 sha。
  - 最后上传 `_READY`。

## 并发与限速

面向 8c / 16G VPS 的默认建议:

- Search/detail HTML 并发: 4-8。
- GraphQL media 分页并发: 每 geo 1-2，全局 4-8。
- 图片下载并发: 16-32。
- R2 上传并发: 16-32，使用 boto3 `TransferConfig(max_concurrency=...)`。
- 每个代理独立 semaphore。
- 全局 domain token bucket + jitter，默认 0.5-2 req/s/proxy，遇到 429/403 指数退避。
- 自动重试: 网络错误、5xx、429，默认 3 次。
- 长跑支持 Ctrl+C 后 resume。

代理:

- `.env` 中用 `TA_PROXIES=http://user:pass@host:port,http://...`。
- 不把代理写入代码或日志。
- 日志只输出代理 index，不输出完整 URL。

## R2 配置

只从 `.env` 读取，不提交真实值。

建议变量名:

```text
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_ENDPOINT_URL=
R2_BUCKET=r2-qiqi
R2_PREFIX=qiqi
R2_REGION=auto
R2_UPLOAD_ENABLED=false
```

行为:

- `R2_UPLOAD_ENABLED=true` 且四个核心字段完整时上传。
- 任一核心字段缺失时跳过上传，只打印本地 package path。
- `_READY` 必须最后上传。

## 项目结构

```text
.
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
├── seeds/
│   └── destinations.sample.csv
├── src/
│   └── travelclaw_ta_geo/
│       ├── cli.py
│       ├── settings.py
│       ├── seeds.py
│       ├── tripadvisor/
│       │   ├── discovery.py
│       │   ├── detail.py
│       │   ├── graphql.py
│       │   ├── media.py
│       │   └── parsing.py
│       ├── output/
│       │   ├── writers.py
│       │   ├── manifest.py
│       │   └── package.py
│       ├── storage/
│       │   └── r2.py
│       └── state.py
└── tests/
```

核心依赖:

- `scrapling[all]>=0.4.3`
- `typer`
- `pydantic-settings`
- `boto3`
- `orjson`
- `rich`
- `pillow` 或等价图片元数据读取库

## .gitignore 必须覆盖

```text
.env
.env.*
!.env.example
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.scrapling/
.crawl/
data/
output/
packages/
downloads/
media/
*.ndjson
*.ndjson.gz
*.sqlite
*.log
.DS_Store
```

## CLI 设计

POC 命令，只抓几百条/少量图片:

```bash
uv run travelclaw-ta-geo poc \
  --seed seeds/destinations.sample.csv \
  --limit-geos 3 \
  --max-images-per-geo 100 \
  --output-dir data/poc \
  --no-upload
```

正式单批:

```bash
uv run travelclaw-ta-geo crawl \
  --seed seeds/destinations.csv \
  --output-dir data/runs \
  --max-images-per-geo 10000 \
  --upload
```

只打包不爬:

```bash
uv run travelclaw-ta-geo package --run-dir data/runs/<run_id>
```

只上传已打包目录:

```bash
uv run travelclaw-ta-geo upload --package-dir data/packages/<timestamp>
```

## POC 验收标准

- 能从英文站搜索并进入详情页，不再使用 `.co` 西语站。
- 至少 1-3 个 seed 成功产出 `geo.ndjson.gz`。
- 每个 geo 行有:
  - `record_id`
  - `source=tripadvisor`
  - `source_id=g...`
  - `source_url`
  - `captured_at`
  - `name`
  - `kind_hint`
  - `center`
  - `raw`
- 图片只来自 geo gallery / hero，不包含下方推荐、广告、攻略卡片图片。
- 本地 `manifest.json` 的 sha256、size、row_count 正确。
- R2 配置为空时不上传、不报错。
- R2 配置完整且 `--upload` 时上传到 `qiqi/geo/tripadvisor/<timestamp>/`，并最后上传 `_READY`。

## 测试计划

- URL 解析:
  - `Tourism-g298564-...` -> `geo_id=298564`
  - `Attraction_Review-g...-d...` 不被当作 geo 主记录。
- 搜索结果选择:
  - top destination 优先。
  - 非 destination 结果跳过。
- 图片白名单:
  - `dataStrategy=geo` 接受。
  - JSON-LD POI/Hotel/Restaurant image 拒绝。
  - sponsored/editorial/feed image 拒绝。
- schema:
  - geo 缺 `center/bbox/boundary` 时进入 error report。
- media 缺 license/attribution 时正式模式拒绝；默认写入 `proprietary` / `© 2026 frank`。
- manifest:
  - row_count、sha256、size_bytes 准确。
- R2:
  - 缺配置跳过。
  - `_READY` 最后写。

## 风险与处理

- Tripadvisor 页面和 GraphQL query id 可能变化:
  - 先用详情页实际触发请求发现媒体 query。
  - query id 抽成配置和探测逻辑，不硬编码成唯一入口。
- 反爬 / 限流:
  - 低并发起步，代理轮换，指数退避。
  - POC 不全量跑。
- 授权:
  - manifest 和媒体行统一写入 `license="proprietary"`、`attribution="© 2026 frank"`。
  - contact 统一写入 `frank <imshuazi@126.com>`。
- 位置精度:
  - Tripadvisor geo 页通常不提供 boundary。
  - 本期依赖 seed center；没有坐标的 seed 不写正式 geo 行。
- 图片混入广告:
  - 不通过页面底部滚动采图。
  - 只用 hero / geo gallery API。
  - 保留模块来源字段，便于审计。

## 待确认问题

1. 20 个 seed 是否需要补 `country_code`？如果你不提供，我可以用 seed 名称做一个小映射表，但这属于我们内部辅助字段，不会冒充源数据。
2. POC 图片数建议每个 geo 100 张；如果你只想看结构，可以降到 20 张。

## 实施顺序

1. 建项目骨架、`.gitignore`、`.env.example`、`pyproject.toml`、seed 样例。
2. 实现 settings、seed loader、URL/id parser。
3. 实现 Scrapling 搜索发现和详情页解析。
4. 实现 geo gallery GraphQL 探测/请求和图片白名单。
5. 实现流式 NDJSON writer、图片下载、manifest/package。
6. 实现 R2 上传，确保 `_READY` 最后上传。
7. 加单测和 POC 命令。
8. 跑 POC，交付本地样例包路径和数据结构预览。

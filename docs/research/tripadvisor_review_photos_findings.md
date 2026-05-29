# TripAdvisor 评论附图（review photos）补充抓取 —— 调研结论

> 调研日期：2026-05-29 · 目标：在官方图库 `mediaAlbumPage`（每 geo 约 2550 张封顶）之外，用**用户评论里上传的照片**把大城市补到接近目标张数（如 10000）。
> 参考项目：[`TripAdvisor-Review-Scraper`](https://github.com/algo7/TripAdvisor-Review-Scraper)（Go，已验证能拿评论数据）。
> **本文档只做调研与设计，代码尚未实现**（`graphql.py` 里 `_review_photos_media` 是占位骨架，默认关闭）。实现前必须先完成 §4 的「实抓一次响应」。

---

## TL;DR（三句话）

1. 评论列表的 query id 是 **`ef1a9f94012220d3`**（hotel / attraction 共用的 `reviewListPage`，参考项目里写死为常量，长期可用）。
2. **但参考项目是 POI 级（`d`-id）的，不是 geo 级（`g`-id）的**——它的 URL 正则强制要求 `-d\d+-Reviews-`，根本不接受 `Tourism-g…` 这种 geo 落地页。我们 travelClaw 是 geo 级（只有 `g189952` 这种 id，没有 `d` id），**不能原样照搬**。
3. **参考项目根本没抓「评论内容照片」**——它的 `Review` 结构里只有 `photoIds []int`（裸 ID，没有 URL），唯一规范化的是用户**头像** `avatar.photoSizeDynamic`。所以「评论附图的真实 URL 长什么样、藏在响应哪一层」**参考项目给不出答案，必须自己实抓一次响应**才能定。

---

## 1. 已经从参考项目确认的事实（可直接复用）

### 1.1 端点与 query id

- 端点同图库：`POST https://www.tripadvisor.com/data/graphql/ids`，body 是数组（批量）。
- query id 常量（`scraper/pkg/tripadvisor/models.go`）：

  | 类型 | 常量名 | query id |
  |---|---|---|
  | Hotel | `HotelQueryID` | `ef1a9f94012220d3` |
  | **Attraction** | `AttractionQueryID` | **`ef1a9f94012220d3`**（与 hotel 相同） |
  | Airline | `AirlineQueryID` | `e1ca245af416c316` |
  | Michelin（餐厅星级） | `MichelinQueryID` | `496720f897546a4e` |

  → 景点/酒店评论都走 **`ef1a9f94012220d3`**，这是我们最该试的那个。

### 1.2 请求变量形态（`scraper/pkg/tripadvisor/tripadvisor.go:54-64` + `models.go` Variables struct）

非 airline 类型的 `reviewListPage` variables（JSON 键名以 struct tag 为准）：

```json
{
  "locationId": <ID>,
  "offset": 0,
  "filters": [{ "axis": "LANGUAGE", "selections": ["en"] }],
  "limit": 20,
  "sortType": null,
  "sortBy": "SERVER_DETERMINED",
  "language": "en",
  "doMachineTranslation": true,
  "photosPerReviewLimit": 7
}
```

- `limit` 最大 **20**（参考项目 `ReviewLimit = 20`，硬上限）。
- `photosPerReviewLimit: 7` —— 每条评论最多返回 7 张附图。这正是我们要的字段。
- 参考项目实际发的是 **2 元素批量**：第 1 个是上面的 `reviewListPage`，第 2 个是一个 `routes` 预取请求（`Page: "Attraction_Review"` / `"Hotel_Review"` + `RouteParams{geoId, detailId, offset}`，offset 形如 `0,"r20","r40"…`）。**核心评论数据来自第 1 个元素**；routes 那个是 SSR 路由预取，抓图时大概率可以省略，但实抓时要确认去掉后第 1 个元素是否仍返回完整 review+photo。

### 1.3 响应顶层结构（`models.go` Response struct）

两种可能的容器（参考项目都处理了）：

```
data.ReviewsProxy_getReviewListPageForLocation[0].reviews[]
data.locations[0].reviewListPage.reviews[]      ← reviewListPage 在这一层
```

每个 `review` 节点里**确定有的字段**：`id, rating, title, text, createdDate, publishedDate, locationId, helpfulVotes, labels, photoIds[], tripInfo, userProfile`。

### 1.4 图片 URL 模板的规范化（已有现成工具）

参考项目用 `photoSizeDynamic.urlTemplate` + `maxWidth/maxHeight`，把 `{width}`/`{height}` 占位符替换成具体尺寸（`NormalizeImageURLTemplate`）。

> ✅ 我们 travelClaw 已经有等价实现：`src/travelclaw_ta_geo/tripadvisor/parsing.py:146 normalize_image_url()` 已经会替换 `{width}`/`{height}`。所以**如果评论附图也是 `urlTemplate` 形态，下载链路无需新增工具**。

---

## 2. 关键差异 / 风险（为什么不能直接抄）

### 2.1 geo 级 vs POI 级（最大的未知数）

参考项目的 `locationId` 是 **POI 的 `d`-id**（如 `d195616` 勃朗峰）。URL 正则：

```
Attraction_Review-g\d{6,10}-d\d{1,10}-Reviews-...   ← 必须有 d-id
```

而我们只有 **geo 的 `g`-id**（`g189952` 冰岛是一个国家/地区，不是一个 POI）。两个待验证问题：

- **Q1：`reviewListPage(locationId = g-id)` 能不能返回 geo 级的聚合评论？**
  - 我们的图库 `mediaAlbumPage` 用 `locationId = geo_id` + `dataStrategy:"geo"` 是能聚合整个 geo 的。评论是否也支持 `dataStrategy:"geo"` 需要实抓确认。
  - `mediaAlbumPage` 的每个 photo 节点里**带 `review` 字段**（见图库调研 §8），说明评论与照片在 geo 层是关联的——这是 geo 级评论照片**可能存在**的间接证据。
- **Q2：如果 geo 级不行**，退路是「geo → 旗下 POI 列表 → 每个 POI 走 POI 级 `reviewListPage`」，工程量大很多（要先列出 geo 下的景点 d-id），**性价比低，原则上不做**（图库调研 §8 的交付原则：补不满就按实际数量交付）。

### 2.2 评论照片的 URL 到底在哪——参考项目没给

- `Review.photoIds` 只是 `[]int`（照片 ID 列表），**没有 URL**。
- 参考项目只把 `userProfile.avatar.photoSizeDynamic` 规范化了（头像，不是评论配图）。
- 真实响应里，评论配图**很可能**在 review 节点的某个 `photos[]` / `Media_*` 子对象里（可能是 `sizes[]` 形态，像图库；也可能是 `photoSizeDynamic.urlTemplate` 形态，像头像）。**两种形态对应两套解析代码，必须实抓才能确定走哪套。**

### 2.3 当前占位代码的形态假设需要复核

`graphql.py` 现在的 `_review_photos_media` / `_extract_review_photos`（**默认关闭，未验证**）做了如下假设，实抓后大概率要改：

- 请求变量用了 `prefs/initialPrefs/needKeywords/keywordVariant` 等字段 —— 应改为对齐参考项目的 `sortBy/language/doMachineTranslation/filters[{axis,selections}]`。
- 解析只认 `sizes[]` —— 若评论附图是 `urlTemplate` 形态则抓不到，需要兼容。
- query id 来自 `ta_review_photos_query_id`（默认空）—— 实抓验证后，可把 `ef1a9f94012220d3` 作为默认值或在 `.env` 配置。

---

## 3. 接入点（实现时改这里，代码坐标）

| 位置 | 现状 | 实现时要做 |
|---|---|---|
| `settings.py:40 ta_review_photos_query_id` | 默认 `""`（关闭开关） | 实抓验证后填 `ef1a9f94012220d3`（或 `.env` 配） |
| `settings.py:41 ta_review_page_limit` | 默认 20 | 保持 ≤20 |
| `graphql.py _review_photos_media()` | 占位骨架，请求变量形态需对齐 §1.2 | 改成 §1.2 的 variables；确认是否要带 routes 批量 |
| `graphql.py _extract_review_photos()` | 只认 `sizes[]` | 按 §4 实抓结果，兼容 `sizes[]` 或 `urlTemplate` |
| `graphql.py gallery_media()` | 已串好：album 不足 → 调 review 补图 → 合并去重 | 无需改，逻辑已就绪 |
| `parsing.py:146 normalize_image_url()` | 已支持 `{width}/{height}` | 若是 urlTemplate 形态直接复用 |

合并去重、补到目标即止、部分失败保留——这些**编排逻辑已经在 `gallery_media` 里写好并测过**，只差「评论附图能被正确解析出来」这一环。

---

## 4. 实现前必做：实抓一次响应（决定性步骤）

在本地（有代理 + 已绕过 Cloudflare 的环境）发一次真实请求，把响应 dump 出来，回答 §2.1 的 Q1 和 §2.2 的「照片 URL 形态」：

1. 用 `TripadvisorHttpClient.post_graphql()` 发 `ef1a9f94012220d3`，variables 用 §1.2 形态，先试 **`locationId = geo_id`**（如冰岛 189952）。
2. 看返回：
   - `data.locations[0].reviewListPage.reviews[]` 或 `data.ReviewsProxy_getReviewListPageForLocation[0].reviews[]` 有没有数据？
   - review 节点里有没有**评论配图对象**（不是 avatar）？它是 `sizes[]` 还是 `photoSizeDynamic.urlTemplate`？字段路径是什么？
3. 若 geo 级返空 → 试加 `dataStrategy:"geo"`；仍不行 → 记录结论「geo 级评论不可达」，A3 退化为「只交付官方图库 ~2550」。
4. 把响应样本存到 `docs/research/review_probe_<geo>.json` 留档（与图库调研的 JSON 证据风格一致）。

> 也可以更省事：直接在浏览器打开某 geo 的 **`Tourism-g<id>-…-Vacations.html`** 页面里那个「Reviews」区块（或对应的 `g<id>-Reviews-…` 页），F12 → Network → 过滤 `graphql/ids` → 找 `reviewListPage` 那条 → 复制 `preRegisteredQueryId` 和完整 variables/response。这是确认 geo 级真实形态最快的办法。

---

## 5. 交付原则（沿用图库调研 §8）

- 官方图库 `mediaAlbumPage`（~2550 张精选）打底。
- 不足部分用评论附图补；**合并去重**（按 media id / photo 路径）。
- **补到目标（如 10000）即止；评论附图也补不满就按实际数量交付，不再拉其它来源。**
- 评论附图是用户上传，质量/构图不如官方精选，按业务取舍。

---

## 附：参考项目关键坐标（`TripAdvisor-Review-Scraper`）

- `scraper/pkg/tripadvisor/models.go:10-20` —— query id 常量
- `scraper/pkg/tripadvisor/models.go:67-77` —— `Variables` struct（请求变量 JSON 键名）
- `scraper/pkg/tripadvisor/models.go:150-172` —— `Review` struct（只有 `photoIds[]`，无配图 URL）
- `scraper/pkg/tripadvisor/tripadvisor.go:20-126` —— `MakeRequest`（请求批量构造）
- `scraper/pkg/tripadvisor/tripadvisor.go:228-247` —— `NormalizeImageURLTemplate`（只用于 avatar）
- `scraper/pkg/tripadvisor/tripadvisor.go:368-396` —— URL 正则（强制 `d`-id，不支持 geo）

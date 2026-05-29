# TripAdvisor 图库全量逆向 —— 调研结论

> 调研日期：2026-05-29 · 工具：Camoufox + curl_cffi（真实会话重放）· 测试 geo：Iceland (g189952，页面标称 2448 张)
> 结论路线：**A2 成功**（拿到官方图库全量），无需回退 A3（review 附图）。

## TL;DR（两句话）

1. 之前的"100 张上限"是个**误诊**：真相是 **`limit ≥ 100` 触发 TripAdvisor 返空的边界 bug**。把 `limit` 降到 `≤ 99`（建议 50），同一个 query（`e451fc43b6a61cab` / `mediaAlbumPage`）就能往后翻 —— Iceland 实测拿满 **2449/2448 = 100%**。
2. 但官方图库**有一个真实的 offset 天花板，约 2500–2550 张/geo**（与页面标称的"2,151,397 of …"无关，那是 UI 虚高聚合数，不可达）。**所以 8k+ 图的城市，官方图库只能给到约 2500 张，拿不全**；要凑到更多需走 **A3：review 附图补充**（见 §8）。

---

## 1. 旧诊断错在哪

之前的诊断说"GraphQL 每城市最多 100 张，offset=100 返空 → 真实天花板就是 100"。
**这个结论是被 `limit=100` 这个参数值误导的。** 真相是：

- `offset=100, limit=100` → 返回 **0**（看起来像"到顶了"）
- `offset=100, limit=99`  → 返回 **99**（其实后面还有一大堆）

代码 `graphql.py:63` 写的是 `limit = min(100, max(1, max_images))`，`max_images=10000` 时 limit 恒为 **100** —— 正好踩中雷区，于是每个城市都在 offset=100 处"假到顶"，只拿到 100 张。

## 2. limit 敏感性实测（offset 固定 100）

| limit | 返回条数 |
|------:|---------:|
| 10 | 10 |
| 20 | 20 |
| 49 | 49 |
| 50 | 50 |
| 51 | 51 |
| 75 | 75 |
| 99 | **99** |
| **100** | **0** ❌ |
| 101 | 0 ❌ |
| 150 | 0 ❌ |
| 200 | 0 ❌ |

**分界线干净利落：`limit ≤ 99` 正常，`limit ≥ 100` 返空。** 与 offset 无关。

## 3. 全量翻页验证（limit=50）

```
off=0    → cum 50
off=450  → cum 500
off=950  → cum 1000
off=1450 → cum 1500
off=1950 → cum 2000
off=2400 → cum 2449 (returned=49)
off=2500 → empty (stop)
=> 2449 unique / 2448 expected = 100.0%，共 51 页
```

每页 ID 全不重复，无需去重补救即覆盖全量。

## 3b. 真实 offset 天花板 ≈ 2500–2550 张/geo（重要！关系到 8k 图）

Iceland 总数恰好 2448，所以"翻到 2449 即全量"看起来很完美。但用一个**图远多于 2500** 的 geo 压测，会暴露出官方图库的真实硬上限：

**Paris (g187147) 实测（limit=50）：**

| offset | 返回条数 |
|------:|---------:|
| 0 | 50 |
| 1000 | 50 |
| 2000 | 50 |
| **2500** | **52**（最后一页有效数据） |
| 2550 | **0** ❌ |
| 2600 / 2750 / 3000 | 0 ❌ |
| 4000 / 6000 / 8000 | 0 ❌ |

> 用户手动在 viewer 里翻到 **offset ≈ 125,360–126,120**（十几万），`limit=20`，响应同样全是 `mediaList: []` —— 进一步证明真实图早已翻完，后面全是空。

**结论：`mediaAlbumPage` 每个 geo album 实际最多翻到 offset ≈ 2500–2550，即约 2550 张官方图。** 超过就恒返 `status:200 / mediaList:[]`。

### 关于页面上那个巨大的"of 2,151,397"

- viewer 顶部的 `8 of 2151397` 和 `mediaAlbum` 返回的 `totalMediaCount: 2151397` 是**整个 geo 树（含所有子地点、所有来源）的聚合计数**，是 UI 显示用的虚高数字。
- 它**不等于** album 可翻页的真实条数。`mediaAlbumPage` 只暴露约前 2550 张（按 popularity 排序的精选/热门图）。
- 因此 **`totalMediaCount` 只能用来判断"是否还有更多"，不能当作翻页终点**。真正的终点是"连续空页"。

## 3c. 8k 图场景的答复

> 问：如果某城市真有 8000 张图，这套机制能抓全吗？

**不能。** 官方图库 `mediaAlbumPage` 每 geo 最多约 **2550 张**就到顶（无论实际有多少）。8k／1 万张的城市，靠官方图库**只能拿到前 ~2550 张**。

要凑到更多（目标 10000），按既定方案走 **A3 补充**：
- 官方图库（`mediaAlbumPage`，~2550 张精选图）打底；
- 不足部分用 **review 附图**（评论里用户上传的照片）补足；
- 合并去重（按 media id / photo-o 路径）；
- **补到 10000 即止；若 review 附图也凑不满 10000，就以实际数量交付，不再额外拉取其它来源。**


## 4. 两个 query 的真实分工

| query id | 名称 | 作用 | 分页 |
|---|---|---|---|
| `1e729d38f07a88bf` | `mediaAlbum` | album 概览：返回 `totalMediaCount`（真实总数，如 2448）+ 前 ~10–17 张预览 | ❌ 不分页（offset 被忽略，恒定首屏） |
| `e451fc43b6a61cab` | `mediaAlbumPage` | 图库翻页：按 `offset`/`limit` 返回 `Media_PhotoResult` 列表 | ✅ `limit ≤ 99` 可一路翻到全量 |

> 现有代码已在用 `e451` 翻页，缺的只是：① 把 limit 降到 ≤99；② 从 `mediaAlbum` 读 `totalMediaCount` 作为翻页终止条件。

## 5. 接口与请求形态

- 端点：`POST https://www.tripadvisor.com/data/graphql/ids`，body 为数组，每项 `{variables, extensions:{preRegisteredQueryId}}`。
- `mediaAlbumPage` variables（实测可翻全量的形态）：
  ```json
  {
    "locationId": 189952, "albumId": 101, "subAlbumId": 101,
    "client": "t", "dataStrategy": "geo",
    "filter": {"mediaGroup": "ALL_INCLUDING_RESTRICTED", "mediaTypes": ["PHOTO","PHOTO_360","VIDEO"]},
    "offset": 0, "limit": 50
  }
  ```
  > ⚠️ 注意是 `mediaGroup`（单数字符串）。改成 `mediaGroups`（复数数组）+ `sortType` 等"新 schema"会直接报错——这个 preRegisteredQueryId 绑死了变量结构。
- `albumId` / `subAlbumId` 实测**被忽略**（101/102/103/1/100 返回完全相同），固定 101 即可。
- 响应结构：`[0].data.mediaAlbumPage[0].mediaList[].data`（`__typename: Media_PhotoResult`）。
- 每个 photo 节点带 `sizes[]`（多分辨率同图，含 `url`/`width`/`height`），**不是** `photoSizeDynamic`/`urlTemplate`。直接取 `sizes[]` 里最大的即可。

## 6. 用户要求：只要图片，不要视频

`mediaTypes` 去掉 `VIDEO`，或在解析时按 `node.mediaType == "PHOTO"`（含 `PHOTO_360`）过滤掉 `VIDEO`。

## 7. 修复方案（最小改动）

`src/travelclaw_ta_geo/tripadvisor/graphql.py`：

1. **核心**：`limit = min(100, max(1, max_images))` → `limit = min(50, max(1, max_images))`（任何 ≤99 的值都行，50 最稳且每页负载适中）。
2. **去掉 LocationPhotos HTML 兜底**：`gallery_media()` 里 `if len(candidates) < max_images:` 的整段 `_location_photos_media` 兜底删除——它误触发（`max_images=10000` 恒大于实际）、且极慢（每页 ~6 图、1600 页/城市）。limit 修好后官方图库本就够到它的天花板。
3. **改终止条件**：不要靠 `totalMediaCount` 当终点（那是 215 万级的虚高聚合数，永远到不了）。改用 **连续空页 / `mediaList:[]`** 作为结束信号（建议连续 1–2 个空页即停），并设一个安全 offset 上限（比如 3000，反正 ~2550 就到顶了）防止无谓空翻。
4. **过滤视频**：`mediaTypes` 去掉 `VIDEO`（用户要求只要图片；保留 `PHOTO` / `PHOTO_360`）。
5. **city_runner.py:114-119**：GraphQL 异常时整城丢图的问题另行处理（重试/部分保留），与本次 100 上限无关，但建议一并修。

预期效果：每城从"100 张"提升到该 geo 的官方图库全量（最多约 2550 张），且不再有 LocationPhotos 慢爬。

## 8. A3：review 附图补充（凑到 10000 的方案，按需实现）

官方图库到 ~2550 即顶。若业务要求每城接近 10000 张，用 review 附图补足：

- **来源**：geo 的 Reviews 页 / 评论列表 GraphQL（`mediaAlbumPage` 响应里每条 photo 已带 `review` 字段，说明评论与照片是关联的；评论列表里也内嵌用户上传照片）。
- **query id 未在本次会话定到**（我试的两个候选 id 报错）。**实现时需从真实 Reviews 页 F12 抓** `reviewListPage` 那条 GraphQL，拿到它的 `preRegisteredQueryId` + 变量形态（典型为 `locationId / offset / limit / filters`）。参考项目（TripAdvisor-Review-Scraper 的 reviewListPage 分页）已验证此路可拿上千张评论附图。
- **合并去重**：官方图库 + review 附图按 media id（或 `photo-o/<path>`）去重。
- **停止条件**：合并去重后达到 10000 即止；review 也补不满 10000 就按实际数量交付，不再拉其它来源。
- **注意**：review 附图是**用户评论照片**，质量/构图不如官方精选图，按业务取舍。


## 附：原始数据（docs/research/ 下）

- `album_probe_iceland.json` —— 初次 limit=100 探测（每 offset 的 photo 节点数，全 0 即误诊起点）
- `album_probe_v2.json` —— 证明 e451 绑死变量 schema（改成 mediaGroups[]/sortType 即报错）
- `node_structure.json` / `locphotos_html_mine.json` —— 节点结构 & LocationPhotos SSR 仅 8 图、文案总数
- `album_overview_probe.json` / `album_overview_raw.json` —— `mediaAlbum`(1e729…) 返回 `totalMediaCount`
- `pagination_truth.json` —— **决定性证据**：limit=20/50 可翻 offset>100、累计 180/300 张无重复
- `full_harvest_iceland.json` —— limit 敏感性表（≥100 返空）+ 全量 51 页 2449/2448 验证
- `scale_stress_paris.json` —— Paris 深 offset 压测（2000 有、4000+ 空；`totalMediaCount`=215 万）
- `offset_ceiling.json` —— 二分定位 Paris 天花板（off=2500 有 52、2550+ 全空 → 约 2550 张/geo）

> 这些 JSON 是逆向证据留档；对应的一次性探测脚本（`scripts/probe_*.py`、`dump_*.py`、`mine_*.py`、`debug_*.py`）调研后已清理。实现时只需参照本文档 §5–§8。


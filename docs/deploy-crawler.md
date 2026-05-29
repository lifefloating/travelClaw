# Tripadvisor Geo Crawler 部署运行手册

本文档描述真实 VPS/Docker 部署时怎么构建镜像、预热浏览器 profile、试跑、正式爬取、监控进度和恢复任务。

## 推荐方式

生产部署推荐使用 `docker compose run --rm` 运行一次性任务：

```bash
docker compose run --rm preheat
docker compose run --rm crawler run --all --parallel 4 --upload
docker compose run --rm monitor
```

`docker-compose.yml` 已经把宿主机 `/data/city_geo` 挂载到容器内同一路径。浏览器 profile、状态库、进度、工作文件和交付包都会保留在这个目录里，容器删除后数据不会丢。

## 机器准备

建议机器规格：

- CPU: 8 cores 左右
- 内存: 16GB 左右
- 磁盘: 至少 80GB 可用空间，数据目录单独放在 `/data/city_geo`
- 网络: 美国区 VPS 更适合 Tripadvisor

准备目录：

```bash
cd /path/to/travelClaw
mkdir -p /data/city_geo
```

如果宿主机不是 root 用户运行 Docker，并且容器写入 `/data/city_geo` 报权限问题，再调整该目录权限。

## 配置 `.env`

首次部署：

```bash
cp .env.example .env
```

必须检查这些配置：

```env
DATA_ROOT=/data/city_geo
TA_PROXIES=http://user:password@host1:port,http://user:password@host2:port
TA_WORKER_COUNT=4
TA_IMAGE_CONCURRENCY=32
TA_IMAGE_REQUESTS_PER_SECOND=32
TA_IMAGE_USE_PROXY=false
TA_HEADLESS=true
TA_REAL_CHROME=false
```

如果要上传 R2，必须同时设置：

```env
R2_UPLOAD_ENABLED=true
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_ENDPOINT_URL=...
R2_BUCKET=r2-qiqi
R2_PREFIX=qiqi
R2_REGION=auto
```

上传只有在命令传了 `--upload` 且 `R2_UPLOAD_ENABLED=true` 且 R2 字段完整时才会执行。

## 构建镜像

```bash
docker compose build
```

`docker-compose.yml` 只让 `crawler` 服务负责构建并标记 `travelclaw-ta-geo:latest`；`preheat` 和 `monitor` 复用这个镜像，且不会从 registry 拉取同名镜像。这样重复执行 `docker compose build` 时不会让多个服务并发导出同一个 image tag。

镜像包含 Python 依赖和 Chromium。图片去重使用轻量的 `ImageHash` PHash 实现，避免拉入 `torch` / NVIDIA / CUDA 依赖。Dockerfile 已清理 `uv` 安装缓存，避免额外打进去一份依赖缓存。

如果执行 `docker compose run --rm preheat` 报：

```text
xvfb-run: error: xauth command not found
```

说明当前 VPS 上的镜像是旧版本，里面缺少 `xvfb-run` 运行所需的 `xauth` 系统包。拉到包含修复的代码后重新构建：

```bash
docker compose build --no-cache crawler
docker compose run --rm preheat
```

## 预热浏览器 profile

每台新 VPS 第一次正式爬取前先预热：

```bash
docker compose run --rm preheat
```

这会运行：

```bash
travelclaw-ta-geo preheat --settle-seconds 12
```

预热会访问 Tripadvisor，并把 Cloudflare 相关状态写到：

```text
/data/city_geo/browser/base/
```

后续并发 worker 会从 `browser/base` 复制出自己的 profile，避免多个进程同时锁同一个浏览器目录。

如果 profile 明显失效，可以删除浏览器 profile 后重新预热：

```bash
rm -rf /data/city_geo/browser
docker compose run --rm preheat
```

## 小批量试跑

正式全量前先跑 1 到 3 个城市，不上传，不清理图片：

```bash
docker compose run --rm crawler run \
  --seed seeds/destinations.sample.csv \
  --limit-geos 3 \
  --max-images-per-geo 200 \
  --parallel 1 \
  --dry-run
```

`--dry-run` 的含义：

- 不上传 R2
- 不做正常清理
- 仍会写入 `/data/city_geo/raw/`、`/data/city_geo/data/`、`/data/city_geo/status/`
- 适合确认代理、Cloudflare、GraphQL gallery、图片下载和包结构

试跑后查看：

```bash
ls -lah /data/city_geo/status
find /data/city_geo/data -maxdepth 2 -type f | head
```

## 正式全量爬取

前台运行：

```bash
docker compose run --rm crawler run --all --parallel 4 --upload
```

建议生产环境放在 `tmux` 里跑：

```bash
tmux new -s ta-crawl
docker compose run --rm crawler run --all --parallel 4 --upload
```

detach: `Ctrl-b d`

恢复会话：

```bash
tmux attach -t ta-crawl
```

如果不用 `tmux`，也可以写日志后台跑：

```bash
mkdir -p /data/city_geo/logs
nohup docker compose run --rm crawler run --all --parallel 4 --upload \
  > /data/city_geo/logs/crawler.log 2>&1 &
```

## 查看全部日志

仓库里提供了一个宿主机侧脚本，用来一次性汇总常用诊断信息：

```bash
chmod +x scripts/view-crawler-logs.sh
scripts/view-crawler-logs.sh
```

脚本默认按顺序输出：

- `docker compose ps -a`
- 当前 compose 项目下每个容器的 `docker logs --tail 200`
- `/data/city_geo/logs/*.log`
- `/data/city_geo/status/*.json`
- `/data/city_geo/raw/*/errors.ndjson`

注意：`docker compose run --rm` 结束后容器会被删除，Docker 不会继续保留这次 stdout。正式长跑建议用上面的 `tmux` 方式保持会话，或者用 `nohup ... > /data/city_geo/logs/crawler.log 2>&1 &` 写入宿主机日志文件。

如果 `.env` 里的 `DATA_ROOT` 不是 `/data/city_geo`，脚本会自动读取 `.env`。也可以手动指定：

```bash
scripts/view-crawler-logs.sh --data-root /data/city_geo --tail 500
```

## 只跑指定城市

`--cities` 使用 seed 文件里的 Tripadvisor geo id。命令里可以写 `g` 前缀，也可以不写。

当前 `seeds/destinations.sample.csv` 的完整对应关系：

| geo id | name_en | name_cn | kind | country |
| --- | --- | --- | --- | --- |
| `g298564` | Kyoto | 京都 | city | JP |
| `g293974` | Istanbul | 伊斯坦布尔 | city | TR |
| `g187147` | Paris | 巴黎 | city | FR |
| `g274707` | Prague | 布拉格 | city | CZ |
| `g293734` | Marrakesh | 马拉喀什 | city | MA |
| `g312741` | Buenos Aires | 布宜诺斯艾利斯 | city | AR |
| `g189158` | Lisbon | 里斯本 | city | PT |
| `g60763` | New York | 纽约 | city | US |
| `g60713` | San Francisco | 旧金山 | city | US |
| `g60864` | New Orleans | 新奥尔良 | city | US |
| `g294276` | Patagonia | 巴塔哥尼亚 | natural_region | AR |
| `g255116` | South Island | 新西兰南岛 | island | NZ |
| `g188077` | Swiss Alps | 瑞士阿尔卑斯 | mountain_region | CH |
| `g3211144` | Yakushima | 日本屋久岛 | island | JP |
| `g143028` | Grand Canyon | 大峡谷 | national_park | US |
| `g61000` | Yosemite | 优胜美地 | national_park | US |
| `g29217` | Hawaii | 夏威夷 | island_region | US |
| `g189952` | Iceland | 冰岛 | country | IS |
| `g190507` | Sognefjord | 松恩峡湾 | fjord | NO |
| `g642196` | Geirangerfjord | 盖朗厄尔峡湾 | fjord | NO |
| `g304017` | Merzouga | 梅尔祖卡 | desert | MA |
| `g477972` | Douz | 杜兹 | desert | TN |

按 Tripadvisor geo id：

```bash
docker compose run --rm crawler run \
  --cities g293974,g298564 \
  --parallel 2 \
  --upload
```

也可以传不带 `g` 的 id，或 seed 里的英文名/中文名：

```bash
docker compose run --rm crawler run --cities 293974 --parallel 1 --upload
```

## 重新跑已经完成的城市

默认情况下，已完成城市会记录在：

```text
/data/city_geo/state/state.sqlite
```

同样命令重跑会自动跳过已完成城市。如果要强制重爬：

```bash
docker compose run --rm crawler run \
  --cities g298564 \
  --parallel 1 \
  --upload \
  --force
```

## 监控进度

另开一个终端：

```bash
docker compose run --rm monitor
```

只输出一次快照：

```bash
docker compose run --rm monitor monitor --once
```

如果只想在宿主机上快速看状态，不想再启动容器，可以直接读状态文件：

```bash
chmod +x scripts/view-crawler-status.sh
scripts/view-crawler-status.sh
```

持续刷新：

```bash
scripts/view-crawler-status.sh --watch 5
```

脚本默认读取 `.env` 里的 `DATA_ROOT`，没有配置时使用 `/data/city_geo`。也可以手动指定：

```bash
scripts/view-crawler-status.sh --data-root /data/city_geo
```

监控读取：

```text
/data/city_geo/status/<city>.json
```

常见 stage：

```text
queued
discovering
fetching_detail
gallery
downloading
packaging
uploading
cleanup
done
skipped
failed
```

## 数据目录

所有运行数据都在 `DATA_ROOT`，默认 `/data/city_geo`：

```text
/data/city_geo/
  raw/<city>/          当前城市工作文件，包含 geo.ndjson、media.ndjson、errors.ndjson、media/
  data/<city>/         交付包目录，上传前包含 media/
  status/<city>.json   城市进度，monitor 读取这里
  state/state.sqlite   跨运行持久状态，记录已完成城市和媒体索引
  browser/base/        预热后的基础浏览器 profile
  browser/worker_N/    每个 worker 独立 profile
  logs/                手动保存的运行日志
```

正常正式运行时：

- 每个城市流程是 crawl -> package -> upload -> cleanup
- raw media 在非 dry-run 下会清理
- package media 在上传成功后会清理
- metadata、manifest、status 和 state 会保留

## R2 交付路径

每个城市上传为一个独立 delivery：

```text
qiqi/geo/tripadvisor/<YYYY-MM-DDTHHMMSSZ>/
```

`_READY` 会最后上传。下游应该以 `_READY` 作为该 delivery 完整可读的信号。

## 并发建议

默认：

```bash
--parallel 4
```

注意：

- `--parallel` 会被 `.env` 里的 `TA_WORKER_COUNT` 限制
- 每个 worker 是独立进程，并有自己的浏览器 profile
- 图片下载用独立限速: `TA_IMAGE_CONCURRENCY` 和 `TA_IMAGE_REQUESTS_PER_SECOND`
- 默认 `TA_IMAGE_USE_PROXY=false`，HTML/GraphQL 仍走 `TA_PROXIES`，图片 CDN 直连以吃满 VPS 出口
- 如果 403/429 增多，先降到 `--parallel 1` 或 `--parallel 2`
- 如果图片下载 403/429 增多，先把 `TA_IMAGE_REQUESTS_PER_SECOND` 降到 16；仍不稳再把 `TA_IMAGE_USE_PROXY=true`
- 如果内存压力明显，也先降低 `--parallel`

## 常用命令汇总

```bash
# 构建
docker compose build

# 新 VPS 第一次预热
docker compose run --rm preheat

# 小批量试跑
docker compose run --rm crawler run --limit-geos 3 --max-images-per-geo 200 --parallel 1 --dry-run

# 全量正式跑
docker compose run --rm crawler run --all --parallel 4 --upload

# 只跑指定城市
docker compose run --rm crawler run --cities g293974,g298564 --parallel 2 --upload  # Istanbul + Kyoto

# 强制重跑指定城市
docker compose run --rm crawler run --cities g293974 --parallel 1 --upload --force  # Istanbul

# 监控
docker compose run --rm monitor

# 单次进度快照
docker compose run --rm monitor monitor --once

# 宿主机直接查看状态文件
scripts/view-crawler-status.sh

# 宿主机持续刷新状态
scripts/view-crawler-status.sh --watch 5
```

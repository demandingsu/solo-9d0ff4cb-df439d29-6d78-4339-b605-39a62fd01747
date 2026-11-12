# 公交临时停运公告采集 API

调度员出车前，需要从运营方**分页发布**的线路公告中，查出“查询时刻仍有效”的临时停运；
线路资料员还要把停运公告附带的**站点清单（CSV 附件）**入库，供调度室查到受影响的
具体站点。本服务用 FastAPI + BeautifulSoup 抓取同站分页、详情页与附件，按公告编号引用
关系与发布时间还原**更正/撤销公告链**，返回有效停运区间、受影响站点、来源链接及
无法匹配的引用。

## 接口

| 接口 | 用途 |
| --- | --- |
| `POST /query` | 公告链还原与停运有效性判定（区间、来源、悬空引用） |
| `POST /station-details` | 在链判定之上解析 CSV 站点清单，按每条有效公告链输出受影响站点与待核实链 |
| `GET /health` | 健康检查 |

两个接口入参相同：`index_url`（公告目录首页）与带时区的 `query_time`（ISO 8601）。

## 业务规则

### 公告链与有效性（两个接口共用）

| 规则 | 实现 |
| --- | --- |
| 公告按编号唯一标识 | `Announcement.notice_id` |
| 更正可引用另一条更正 | 引用图上从根（无有效出边引用的公告）沿后继遍历，支持任意深度 |
| 链顺序 | 引用关系定链，同一节点多个后继按发布时间排序 |
| 更正替换原停运信息 | 按发布时间依次应用，链上最后一条可见公告的起止/线路为当前生效信息 |
| 撤销使整条链失效 | 链上出现查询时刻已发布的撤销公告 → `inactive_chains[].reason=cancelled` |
| 未来公告不可见 | 发布时间晚于查询时刻的公告不参与链还原与判定 |
| 同线路独立公告分别判断 | 每条根公告独立成链；示例中 12 路有两条同时有效的独立停运 |
| 无法匹配的引用 | 被引用编号在整站不存在时收集到 `unmatched_references`，不影响其余判定 |
| 同站限制 | 只沿协议+主机+端口一致的链接爬行（含 CSV 附件），跨站链接跳过并在 `skipped_offsite_links` 返回 |

停运区间按半开 `[suspend_start, suspend_end)` 判定：查询时刻等于结束时刻即视为已失效。
所有时间均要求带时区（ISO 8601 偏移），比较基于同一时间线瞬间。

### 站点明细与附件（`/station-details`）

详情页可携带一个指向本站的 CSV 附件（`<a class="attachment" href="xxx.csv">`），
CSV 必含 `线路`、`站点编号` 列（`站点名称` 可选）。仅对查询时刻**有效**的公告链
解析附件，规则如下：

| 场景 | 处理 |
| --- | --- |
| 更正带新附件 | 新表替换旧表；被替换的旧表仍保留在 `chain_attachments` 中可追溯 |
| 更正未带附件且线路不变 | 沿用链上最近一份附件表，区间取更正后的新区间 |
| 更正未带附件但线路变更 | 该链标为待核实（`route_changed_without_attachment`），不用旧表补齐 |
| 整条链无附件 | 待核实（`attachment_missing`） |
| 附件抓取失败/跨站/404 | 仅相关链待核实（`attachment_fetch_failed`），不回退旧表 |
| CSV 无法解析（缺列、空表、编码错误） | 仅相关链待核实（`attachment_invalid`） |
| 表内线路与公告当前线路不符 | 仅相关链待核实（`attachment_invalid`） |
| 目录或详情采集不全（抓取/解析失败、触发限量截断） | 整站 `fully_verified=false`（未核全）；附件问题不影响该标志 |

待核实链在 `pending_chains` 中返回（含原因 `detail`、链编号、来源与区间），
已核实链在 `station_suspensions` 中逐站点输出，并同时给出公告来源
（`notice_sources`）与附件来源（`attachment_source` / `chain_attachments`）。

## 目录结构

```
app/                 FastAPI 采集服务
  app/models.py      Pydantic 模型（入参/出参/公告实体/站点明细）
  app/parser.py      BeautifulSoup 解析：分页 rel=next、详情链接、详情字段、CSV 附件链接
  app/csv_attachment.py  CSV 站点清单解析（标准库 csv，无额外依赖）
  app/crawler.py     同站分页 + 详情 + 附件抓取（httpx 异步，限量/去重/错误收集/截断标志）
  app/engine.py      公告链还原与有效性判定（纯函数，无 IO）
  app/station_service.py 站点明细还原：附件替换/沿用/待核实规则
  app/main.py        POST /query、POST /station-details、GET /health
  Dockerfile
site/                本地示例公告站（数据驱动生成纯静态 HTML + CSV）
  build_site.py      生成 html/（5 个列表页 + 24 条详情 + 13 个 CSV，覆盖全部场景）
  Dockerfile         python -m http.server 托管静态站
tests/               47 个单元/集成测试（pytest）
demo/                本地与 Compose 演示脚本（依次调用两个接口）
docker-compose.yml   api(8000) + notice-site(8080)
```

## 运行方式

### 方式一：Docker Compose（推荐，完整离线演示）

```bash
python3 site/build_site.py          # 生成示例站静态文件
docker compose up -d --build

curl -s -X POST http://127.0.0.1:8000/station-details \
  -H 'Content-Type: application/json' \
  -d '{"index_url":"http://notice-site:8080/index.html",
       "query_time":"2026-11-11T10:00:00+08:00"}'
```

容器内 API 通过 Compose 网络访问 `notice-site`；从宿主机调用时 `index_url`
也可换成 `http://127.0.0.1:8080/index.html`（两个地址同构，同站判定均成立）。
交互式文档：http://127.0.0.1:8000/docs

### 方式二：本地 venv（无 Docker 环境）

```bash
python3 -m venv --without-pip .venv
.venv/bin/python get-pip.py        # 若 ensurepip 不可用，先 bootstrap pip
.venv/bin/pip install -r app/requirements.txt
./demo/run_local_demo.sh           # 自动建站、起两个服务并发起查询
```

### 测试

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q      # 需先在 127.0.0.1:8080 启动示例站（demo 脚本会自动启动）
```

## 示例站点场景（查询时刻 2026-11-11 10:00 +08:00）

公告链判定：

| 公告 | 场景 | 判定结果 |
| --- | --- | --- |
| N-2026-001 | 12 路原始停运（带 CSV） | 有效，站点入库 |
| N-2026-002 | 18 路停运已结束 | `expired` |
| 003 → 006 → 010 | 25 路停运被两次更正，三次各带一份 CSV | 有效，站点取 010 新表，旧表保留在 `chain_attachments` |
| N-2026-004 → 007 | 33 路停运被撤销 | `cancelled`，整条链失效，不抓附件 |
| N-2026-005 | 12 路另一条独立停运（带独立 CSV）；012 更正次日才发布 | 有效，链上只有 005 |
| N-2026-008 | 5 路停运尚未开始 | `not_yet_effective` |
| 009 → 011 | 40 路更正无附件且线路不变，011 误引用 N-2026-999 | 有效，沿用 009 的站点表，同时报告悬空引用 |
| N-2026-013 | 50 路整条公告查询时刻之后才发布 | `not_yet_effective` |
| N-EXT-001 | 列表中的跨站链接 | 跳过，出现在 `skipped_offsite_links` |

站点附件场景（`/station-details`）：

| 公告链 | 场景 | 结果 |
| --- | --- | --- |
| N-2026-014 | 60 路单公告带附件 | 核实通过 |
| 015 → 016 | 61 路更正带新附件 | 新表替换旧表，核实通过 |
| 017 → 018 | 62 路更正无附件、线路不变 | 沿用 017 的表，核实通过 |
| N-2026-019 | 63 路整条链无附件 | 待核实 `attachment_missing` |
| N-2026-020 | 64 路附件文件 404（站点中故意不生成该 CSV） | 待核实 `attachment_fetch_failed` |
| N-2026-021 | 65 路附件表内线路写成 66 路 | 待核实 `attachment_invalid` |
| N-2026-022 | 66 路附件缺少“站点编号”列 | 待核实 `attachment_invalid` |
| 023 → 024 | 67 路更正无附件且线路变更为 68 路 | 待核实 `route_changed_without_attachment` |

正常采集下 `fully_verified=true`；可自行删除一个详情页或附件验证
`fully_verified=false`（目录/详情不全）与附件级待核实（不影响整站标志）的区别。

## 接入真实站点

真实页面结构不同时，只需替换 `app/parser.py` 中的 CSS 选择器与字段键映射
（`FIELD_KEYS` / `KIND_MAP`），引擎、爬虫与 API 契约无需改动。解析器约定详情页
使用 `<time datetime>` 携带带时区的 ISO 8601 时间、引用使用
`<a class="reference" data-ref-id="编号">`、CSV 附件使用
`<a class="attachment" href="xxx.csv">`，避免依赖文案措辞。CSV 列名约定见
`app/csv_attachment.py`（`线路` / `站点编号` / `站点名称`）。

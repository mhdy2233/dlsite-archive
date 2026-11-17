# DLsite Archive

把 DLsite（maniax / 同人ゲーム）的作品信息与官方图片归档成一份可搜索的静态站点，
由 GitHub Actions 每 4 小时自动增量更新，通过 GitHub Pages 展示，无需服务器。

- 前端：https://mhdy2233.github.io/dlsite-archive/
- 数据：`data/works/*.json`（每部作品一个文件）
- 图片：`docs/images/<RJ号>/`（DLsite 官方原图：主图 + 样例图 + 官方缩略图）

## 抓什么

单部作品会归档 **100+ 个字段**，全部来自详情页内嵌的 `__NUXT_DATA__`（比解析 DOM 可靠得多）：

| 分类 | 字段 |
|---|---|
| 基本 | 标题、罗马字标题、作品 ID、作品分类、年龄分级、性别分类、类型（RPG/SLN/ADV…） |
| 社团 | 社团 ID、名称、主页 URL |
| 价格 | 日元售价、原价、**8 种货币换算价**、返点 |
| 时间 | 发售时间戳、更新日期 |
| 文件 | 文件类型（EXE/PDF…）、文件体积 |
| 标签 | 全部 genre（含 genre_key，可跳回 DLsite 检索） |
| 语言 | `work_languages`、`translation_info`（是否翻译作品、父/子作品号）、`language_editions` |
| 口碑 | 评分、评价数、销量、收藏数、榜单排名（日/周/月） |
| 图片 | 官方主图、官方 240×240 缩略图、全部样例图（原尺寸）、作品介绍内嵌图 |
| 介绍 | 分部介绍的标题 + 正文 HTML + 配图 |

不抓评论正文（只有评论数量）。

## 站点功能

- 卡片网格（封面、折扣、类型、R18 标记、评分、销量）
- 搜索：标题 / 社团 / 标签（`/` 聚焦搜索框）
- 筛选：类型、标签（多选）、年龄分级
- 排序：最新发售 / 销量 / 评分 / 价格升降
- 详情弹窗：大图、样例图切换、完整信息表、标签点击反查、分部介绍
- 18+ 年龄确认（localStorage 记住）

## 部署

1. 推到仓库（默认分支 `main`）。
2. **Settings → Pages → Source** 选 `Deploy from a branch`，分支 `main`，目录 **`/docs`**。
   （GitHub 的分支部署只支持根目录或 `/docs`，所以站点放在 `docs/`。）
3. **Settings → Actions → General → Workflow permissions** 选 `Read and write permissions`（否则无法回写数据）。
4. Actions 页手动跑一次 `archive`（可填 `limit`），之后每 4 小时自动跑。

首次全量抓取请用 `workflow_dispatch` 手动把 `limit` 调大（如 300），分多轮补齐 —— 单轮
workflow 上限 6 小时，按 ~30 秒/部估算，一轮安全上限约 500 部。

## 出口

抓取必须在日本 IP 下进行（DLsite 对非日本 IP 直接 302 到 google，或对 R18 作品返回
「お住いの国・地域からは本作品は購入できません」）。

`scripts/vpngate.py` 的做法：

1. 拉 VPN Gate 公开节点列表（`/api/iphone/`），筛出 JP；
2. 建一个独立 **network namespace**（`dlsitevpn` + veth + NAT），
   **完全不动主机默认路由** —— 这是关键，否则全流量走 VPN 会让 Actions runner 与
   GitHub 失联；
3. 在 namespace 内起 OpenVPN，逐个尝试节点直到 `ipinfo.io` 报告 `country=JP`；
4. 抓取的所有 `curl` 都在这个 namespace 内执行（`ip netns exec dlsitevpn curl …`）；
5. 结束后拆除 namespace、veth 和 iptables 规则。

VPN 节点是公共共享的，只用于读取公开页面，不要在上面登录或付款。

## 本地运行

```bash
# 已有日本 SOCKS/HTTP 代理时（最快）
python3 scripts/run.py --limit 10 --no-vpn --proxy socks5h://127.0.0.1:1081

# 或让脚本自己搭 vpngate 隧道（需要 root 与 /dev/net/tun）
sudo python3 scripts/run.py --limit 10

# 生成站点数据
python3 scripts/build_site.py

# 本地预览
cd site && python3 -m http.server 8899
```

`scripts/run.py` 的参数：

| 参数 | 说明 |
|---|---|
| `--limit N` | 本次归档多少部新作品 |
| `--proxy URL` | 走代理（`socks5h://` / `http://`） |
| `--no-vpn` | 不建隧道（配合 `--proxy`） |
| `--workers N` | 并发数，默认 3，别设太高以免被限流 |
| `--max-pages N` | 列表页最多翻多少页 |

## 增量逻辑

`data/state.json` 记录已归档的 ID。每次运行按 **发售日降序** 翻列表页，跳过已存在的 ID，
连续一整页都是旧作品就停止 —— 所以日常只抓真正的新作，开销很小。

## 目录

```
.github/workflows/archive.yml   # 定时抓取 + 构建 + 回写
scripts/devalue.py              # __NUXT_DATA__ (devalue) 解析器
scripts/vpngate.py              # VPN Gate + netns 出口
scripts/scraper.py              # 列表页 / 详情页 / 图片
scripts/build_site.py           # 生成 api/index.json 等
scripts/run.py                  # 主流程
docs/                           # Pages 根目录（前端 + api + images）
data/works/                     # 每部作品的完整 JSON
data/state.json                 # 归档进度
```

## 注意

- **仓库体积**：图片按官方原尺寸保存，平均 2–3 MB/部。归档量到几千部后仓库会很大，
  届时建议改为只存缩略图，或把图片迁到对象存储（R2/S3）。
- **版权**：作品信息与图片版权归 DLsite 及各社团所有。本项目仅作归档与研究，请支持正版。
- **成人内容**：默认包含 R18 作品，站点带年龄确认。
- **礼貌抓取**：默认 1.5–2 秒/请求、3 并发。请勿调高并发，避免给对方站点造成压力。

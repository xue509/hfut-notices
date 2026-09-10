# 合工大通知公告监控 & 微信推送系统

> 🏫 自动监控合工大 4 个通知公告来源  
> 🏆 竞赛、📅 节假日 推送到微信  |  📚 全部通知归档到手机 App

## 📡 监控来源

| 来源 | 地址 |
|------|------|
| 新闻网 | https://news.hfut.edu.cn/tzgg2.htm |
| 总务部 | https://zwb.hfut.edu.cn/index/tzgg.htm |
| 学工部 | https://xgb.hfut.edu.cn/tzgg.htm |
| 学工部-学生管理 | https://xgb.hfut.edu.cn/tzgg/xsgl.htm |

来源定义在 `scraper.py` 顶部的 `SOURCES` 列表里，加一行就是加一个来源。

---

## ✨ 功能

- **多源抓取**: 4 个来源定时抓取，支持翻页，跨来源按文章去重
- **八类归档**: 全部通知按类型分档，手机 App 可分类浏览
- **正文简介**: 自动抓正文并剥掉公文腔（抬头/落款/附件区），每条通知一句简介
- **微信推送**: 只推「竞赛通知」和「节假日」两类，支持 PushPlus 和微信测试号
- **去重机制**: SQLite 存储已推送通知，同一条不重复发送
- **简单易用**: 一行命令运行，Windows 任务计划程序即可定时执行

### 分类一览

| 类别 | 图标 | 推微信 | 说明 |
|------|------|:------:|------|
| 竞赛通知 | 🏆 | ✅ | 报名、选拔、大赛、讲座报告 |
| 节假日 | 📅 | ✅ | 放假、调休、校历、开学返校 |
| 科研申报 | 🔬 | — | 基金委、重点研发计划、项目指南 |
| 教务教学 | 🎓 | — | 选课、四六级、学籍、推免 |
| 学工通知 | 🎯 | — | 奖助学金、宿舍、评优、就业 |
| 后勤服务 | 🏠 | — | 停水停电、食堂、采购、维修 |
| 公示公告 | 📢 | — | 评审结果、名单公示、活动通知 |
| 其他 | 📌 | — | 兜底，不硬塞 |

> 推送范围由 `classifier.py` 里各类别的 `push` 字段决定，**只推竞赛+节假日**，
> 其余类别只入库供 App 浏览，不打扰微信。

分类的核心原则是**宁可进「其他」，也不错分**。所以关键词用精确锚点
（「停电」而不是「检修」），并且有一整套排除规则挡住事后公示和文体活动。
当前 98 条真实数据的分布：学工 35 / 公示 21 / 后勤 20 / 科研 14 / 教务 3 / 竞赛 2 / 其他 3。

---

## 🚀 快速开始

### 1. 安装依赖

```bash
# 确保已安装 Python 3.8+
python --version

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置推送

#### 方式 A: PushPlus（推荐 ⭐）

最简单的推送方式，无需注册公众号：

1. 微信扫码关注 **PushPlus** 公众号  
   ![PushPlus二维码](https://www.pushplus.plus/img/qrcode_for_wechat.6de5a7c7.jpg)

2. 在公众号菜单点击「个人中心」→ 复制你的 **token**

3. 编辑 `config.yaml`:
   ```yaml
   pusher:
     mode: "pushplus"
     pushplus:
       token: "你的token粘贴在这里"
   ```

#### 方式 B: 微信测试号

体验更接近真实公众号（限 100 人关注）：

1. 打开 [微信公众平台测试号](https://mp.weixin.qq.com/debug/cgi-bin/sandboxinfo?action=showinfo)
2. 微信扫码登录，获得 **appID** 和 **appSecret**
3. 在页面下方「模板消息接口」添加模板:
   - 模板标题: `通知提醒`
   - 模板内容:
     ```
     {{first.DATA}}
     通知类型: {{keyword1.DATA}}
     发布时间: {{keyword2.DATA}}
     {{remark.DATA}}
     ```
4. 关注页面上的测试号二维码
5. 在「测试号二维码」旁边获取你的微信号 (openid)

6. 编辑 `config.yaml`:
   ```yaml
   pusher:
     mode: "wechat_test"
     wechat_test:
       app_id: "你的appID"
       app_secret: "你的appSecret"
       template_id: "模板ID"
       openid: "你的openid"
   ```

### 3. 运行测试

```bash
# 先试运行（不推送，只查看结果）
python main.py --dry-run

# 确认无误后正式运行
python main.py
```

### 4. 设置定时任务

#### Windows 任务计划程序

1. 打开「任务计划程序」（搜索 Task Scheduler）
2. 点击「创建基本任务」
3. 名称填写: `合工大通知监控`
4. 触发器: 每天，每隔 2 小时
5. 操作: 启动程序
   - 程序: `python`
   - 参数: `main.py`
   - 起始于: `C:\cc\hfut-notice-monitor`
6. 完成

> 💡 建议每 2 小时检查一次，避免遗漏重要通知

---

## 📖 命令说明

| 命令 | 说明 |
|------|------|
| `python main.py` | 运行一次完整检查并推送 |
| `python main.py --dry-run` | 仅抓取分类，不推送（测试用） |
| `python main.py --stats` | 显示数据库统计信息 |
| `python main.py --recent 7` | 显示最近7天的通知记录 |
| `python main.py --config my.yaml` | 使用自定义配置文件 |

---

## 🔧 自定义分类规则

分类规则写在 `classifier.py` 里（`config.yaml` 的 `classifier` 段落是死配置，不生效）：

| 常量 | 作用 |
|------|------|
| `CATEGORIES` | 八个类别的定义：标签、图标、配色、是否推送 |
| `*_RULES` | 各类别的关键词 → 子标签，按优先级从上往下匹配 |
| `HOLIDAY_EXCLUDE` | 含这些词的标题一律不算节假日（食堂、浴室、施工等）|
| `COMMON_EXCLUDE` | 事后公示、文体活动，不占任何行动类目录 |
| `ACADEMIC_EXCLUDE` | 「培养方案修订/调研」这类行政文，不算教务 |
| `LECTURE_EXCLUDE` | 排除「述职报告」这类假讲座 |

改完直接跑回归测试，内置 54 条用例：

```bash
python classifier.py     # 输出 54/54 passed 说明没改坏
```

匹配顺序是 `竞赛 → 节假日 → 科研 → 教务 → 学工 → 后勤 → 公示公告 → 其他`，
命中即停。几条容易踩的设计：

> **`_is_result_publicity()`** —— 区分对偶用词：「获奖名单的公示」排除，
> 「评选工作的通知」保留（事前还能报名）。避免「…大赛…获奖作品的公示」被当竞赛推。
>
> **`_is_official_selection()`** —— 政务流程的「大赛」不算竞赛，但必须**同时**
> 含比赛词 + 以遴选/表彰/推荐收尾，否则会误伤「优秀辅导员拟表彰名单的公示」。
>
> **`_is_holiday_false_positive()`** —— 正则二次确认，挡住「暑期浴室开放时间」
> 「暑期实验室安全」这类蹭「暑期」二字的通知。

### 配色校验

类别色会用在 App 的标签文字上，所以每个色值必须在自己的底色上达到
WCAG AA（≥ 4.5:1）。改了 `CATEGORIES` 里的颜色后跑一下：

```bash
py -3.9 color_check.py    # 全部 ✅ 再提交
```

浅色模式在 `#FFFFFF` 上验，深色模式在 `#1B1E24` 上验，两套色值各有一组
（`color` / `color_dk`），因为浅色值放到深底上对比度往往不够。

---

## 📁 项目结构

```
hfut-notice-monitor/
├── main.py              # 主程序入口（本地跑）
├── run_cloud.py         # 云端入口（GitHub Actions，配置全走环境变量）
├── scraper.py           # 多来源抓取模块（SOURCES 在这里）
├── classifier.py        # 通知分类器（含 54 条回归测试）
├── color_check.py       # 类别配色 WCAG AA 对比度校验
├── storage.py           # SQLite 存储模块
├── wechat_pusher.py     # 微信推送模块
├── app.py               # 本地 GUI（tkinter）
├── config.yaml          # 配置（仅本地用）
├── requirements.txt     # Python 依赖
├── docs/                # GitHub Pages：手机端 PWA
│   ├── index.html
│   ├── data.json        # 由脚本导出
│   └── seen.json        # 已推送记录（云端去重）
├── notices.db           # 数据库（自动生成）
├── monitor.log          # 运行日志（自动生成）
└── README.md            # 本文件
```

### 加一个新来源

在 `scraper.py` 的 `SOURCES` 里追加一项即可，抓取逻辑不用动：

```python
{
    "name": "教务处",
    "url": "https://jwc.hfut.edu.cn/tzgg.htm",
    "list": "div.list-right div.list-con ul",   # 列表容器选择器
    "date": "span.date",                        # 条目内日期选择器
    "pagination": "div.pb_sys_common",          # 分页区选择器
    "page_base": "https://jwc.hfut.edu.cn/tzgg.htm",  # 分页相对链接基准
},
```

> ⚠️ `page_base` 必须是**页面自身 URL**（分页链接如 `tzgg/67.htm` 是相对当前目录的）。
> 而文章链接的基准是**站根**——两者不同，代码里分别处理，别合并。

验证单个来源：

```bash
python scraper.py 教务处    # 只抓这个来源并打印结果
```

---

## ❓ 常见问题

**Q: 为什么不直接做一个真正的微信公众号？**  
A: 注册微信服务号/订阅号需要营业执照或身份证、300元认证费、服务器备案等，门槛较高。PushPlus 和测试号免费且立即可用，适合个人使用。如果后续需要升级，模块已预留微信 API 接口。

**Q: 可以部署到服务器上吗？**  
A: 可以。项目所有路径都是相对路径，将整个目录复制到服务器，用 `crontab` 定时执行即可:
```bash
# Linux crontab 示例: 每2小时执行一次
0 */2 * * * cd /path/to/hfut-notice-monitor && python main.py
```

**Q: 推送会不会有重复？**  
A: 不会。系统通过 URL hash 去重，同一条通知只会被推送一次。

**Q: 分类不准确怎么办？**  
A: 编辑 `classifier.py` 里的 `*_RULES` 关键词表（注意 `config.yaml` 的 `classifier` 段落不生效）。
改完跑 `python classifier.py` 确认 54 条用例没改坏，再用 `--dry-run` 预览真实数据的分档结果。
如果某条标题进了「其他」，多半是缺一个精确锚点，而不是规则太严——加锚点比放宽规则安全。

---

## ⚠️ 定时任务会被 GitHub 静默停掉

`scrape.yml` 每 2 小时跑一次，但 GitHub 对**定时工作流**有硬规则：

> 仓库连续 **60 天没有任何活动**，schedule 自动禁用，**且不会通知你**。

而这个 workflow 只在 `docs/data.json` 有变化时才 commit —— 放假期间通知少，
连续几天没新内容，60 天计时就一直在走，很容易被停掉。

**排查**: 去 https://github.com/xue509/hfut-notices/actions 看有没有
`This workflow was disabled` 的红字。

**解决**: 找个能连上 GitHub 的时间手动触发一次 `workflow_dispatch`
（Actions 页面 → Notice Monitor → Run workflow），即可重置计时器。

---

## 📝 更新日志

- **v3.0** (2026-09-10): 分类、简介、工大红
  - 分类从 2 类扩到 8 类，关键词改用精确锚点 + 多组排除规则，
    「其他」从 89 条降到 3 条；只推竞赛+节假日，其余仅入库
  - 每条通知自动抓正文简介，剥掉公文抬头/落款/附件区
  - 前端配色改为工大红 `#af2227`（取自官网 CSS），类别色随 `data.json` 下发
  - 新增 `color_check.py`，两套配色过 WCAG AA
  - 修复 `content.jsp` 形式的文章链接 404（学工部有 6 条）
  - 修复 `storage.get_recent()` 的 `category` 参数被忽略
- **v2.0** (2026-09-10): 多来源
  - 新增 总务部 / 学工部 / 学工部-学生管理 3 个来源，抓取逻辑数据驱动
  - `SOURCES` 配置化，加来源只需加一项
  - 文章链接用站根、分页链接用页面目录，两套基准分别处理
  - 分类器新增结果公示/文体活动排除，回归测试 31 条
- **v1.0** (2026-07-22): 初始版本
  - 新闻网通知公告抓取
  - 竞赛/节假日智能分类
  - PushPlus + 微信测试号双通道推送
  - SQLite 去重存储

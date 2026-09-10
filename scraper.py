"""
合工大通知公告 - 多来源网页抓取模块 (v2)

数据驱动: 每个来源在 SOURCES 里描述自己的列表容器和分页规律,
抓取逻辑只写一份。

已接入:
  - 新闻网      news.hfut.edu.cn/tzgg2.htm
  - 总务部      zwb.hfut.edu.cn/index/tzgg.htm
  - 学工部      xgb.hfut.edu.cn/tzgg.htm
  - 学工部-学生管理  xgb.hfut.edu.cn/tzgg/xsgl.htm
"""

import re
import logging
from typing import Optional
from urllib.parse import urljoin, urlsplit, parse_qs

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# ============================================================
# 来源配置
#
#   name        来源名称（会写进 source_tab 字段，前端直接显示）
#   url         列表首页
#   list        列表容器 CSS 选择器
#   date        单个条目内日期的 CSS 选择器；留空则用正则兜底
#   pagination  分页区 CSS 选择器（用于翻页）
#   page_base   分页相对链接的拼接基准（页面自身 URL，因为 tzgg/67.htm 是相对当前目录）
#
# 注意: 文章链接的基准是站根（../info/1029/6465.htm → /info/1029/6465.htm），
#       分页链接的基准是页面目录，两者不一样，别合并。
# ============================================================
SOURCES = [
    {
        "name": "新闻网",
        "url": "https://news.hfut.edu.cn/tzgg2.htm",
        "list": "div.list#tzz",
        "date": "i",
        "pagination": "div.pb_sys_common",
        "page_base": "https://news.hfut.edu.cn/tzgg2.htm",
    },
    {
        "name": "总务部",
        "url": "https://zwb.hfut.edu.cn/index/tzgg.htm",
        "list": "ul.list-text",
        "date": "div.date",
        "pagination": "div.pb_sys_common",
        "page_base": "https://zwb.hfut.edu.cn/index/tzgg.htm",
    },
    {
        "name": "学工部",
        "url": "https://xgb.hfut.edu.cn/tzgg.htm",
        "list": "div.list-right div.list-con ul",
        "date": "span.date",
        "pagination": "div.pb_sys_common",
        "page_base": "https://xgb.hfut.edu.cn/tzgg.htm",
    },
    {
        "name": "学工部-学生管理",
        "url": "https://xgb.hfut.edu.cn/tzgg/xsgl.htm",
        "list": "div.list-right div.list-con ul",
        "date": "span.date",
        "pagination": "div.pb_sys_common",
        "page_base": "https://xgb.hfut.edu.cn/tzgg/xsgl.htm",
    },
]

# 学工部部分条目用 content.jsp?wbtreeid=..&wbnewsid=.. 跳转，不是明文 URL。
# 这类链接换个会话直接访问会 404，所以只做规范化用于去重，不改写成 info/ 路径。
_CONTENT_JSP_RE = re.compile(r"wbtreeid=(\d+)[^#]*?wbnewsid=(\d+)")


class NoticeScraper:
    """多来源通知公告抓取器"""

    BASE_URL = "https://news.hfut.edu.cn/tzgg2.htm"
    BASE_HOST = "https://news.hfut.edu.cn"

    def __init__(self, timeout: int = 15, user_agent: Optional[str] = None):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })

    # ---------------- HTTP ----------------

    def fetch_page(self, url: str) -> str:
        """获取页面 HTML 内容（自动判断编码）"""
        logger.info(f"正在抓取: {url}")
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = self._detect_encoding(resp)
            return resp.text
        except requests.RequestException as e:
            logger.error(f"抓取失败 {url}: {e}")
            return ""

    def _detect_encoding(self, resp) -> str:
        """
        判断页面编码。各子站编码不统一（新闻网 utf-8 / 部分老站 gbk），
        照抄 response.encoding 会乱码，所以自己判一遍。
        """
        # 1. HTTP 头里声明了 utf-8 就用它
        ctype = resp.headers.get("Content-Type", "")
        if "charset=" in ctype.lower():
            declared = ctype.lower().split("charset=")[-1].split(";")[0].strip()
            if declared.startswith("utf"):
                return "utf-8"

        # 2. 从 HTML meta 里找
        head = resp.content[:3000].lower()
        m = re.search(rb'charset=["\']?([\w-]+)', head)
        if m:
            enc = m.group(1).decode("ascii", "ignore")
            # GB2312/GBK 统一按 gb18030 解，兼容字符更多
            if enc.lower() in ("gb2312", "gbk"):
                return "gb18030"
            return enc

        # 3. 猜：能按 utf-8 解就用 utf-8
        try:
            resp.content.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            return "gb18030"

    # ---------------- 解析 ----------------

    def parse_notice_list(self, html: str, source_name: str = "",
                          list_sel: str = "div.list#tzz",
                          date_sel: str = "i") -> list[dict]:
        """
        从列表页 HTML 中解析通知。

        返回: [{"title", "date", "link", "source_tab", "raw_date"}, ...]
        """
        soup = BeautifulSoup(html, "lxml")
        notices = []

        list_area = soup.select_one(list_sel)
        if not list_area:
            logger.warning(f"[{source_name}] 未找到列表区域 ({list_sel})")
            return notices

        for li in list_area.find_all("li"):
            a_tag = li.find("a")
            if not a_tag:
                continue

            link = a_tag.get("href", "")
            if link:
                link = self._normalize_link(link, source_name)

            # 标题优先取 title 属性，其次取 a 内第一个非日期子节点
            title = (a_tag.get("title") or "").strip()
            if not title:
                title = self._extract_title_text(a_tag, date_sel)

            raw_date = self._extract_date_text(li, date_sel)
            date_str = self._parse_date(raw_date)

            if title and date_str:
                notices.append({
                    "title": title,
                    "date": date_str,
                    "link": link,
                    "source_tab": source_name,
                    "raw_date": raw_date,
                })

        return notices

    def _extract_title_text(self, a_tag, date_sel: str) -> str:
        """没有 title 属性时，从 a 的文本里剥掉日期/分类标签，剩下的就是标题"""
        clone = BeautifulSoup(str(a_tag), "lxml")
        for extra in clone.select(f"{date_sel or 'i'}, span.date, div.date, span.aejlm"):
            extra.decompose()
        return clone.get_text(separator=" ", strip=True).strip()

    def _extract_date_text(self, li, date_sel: str) -> str:
        """取条目里的日期文本，选择器失配时正则兜底"""
        if date_sel:
            node = li.select_one(date_sel)
            if node:
                return node.get_text(strip=True)

        # 兜底: 在 li 文本里找 2026-09-09 或 2026年09月09日
        text = li.get_text(" ", strip=True)
        m = re.search(r"\d{4}[-年]\d{1,2}[-月]\d{1,2}", text)
        return m.group(0) if m else ""

    def _parse_date(self, raw_date: str) -> str:
        """中文/横线日期统一转 ISO: "2026年07月21日" / "2026-07-21" -> "2026-07-21" """
        if not raw_date:
            return ""

        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', raw_date)
        if not match:
            match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', raw_date)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        return raw_date.strip()

    def _normalize_link(self, href: str, source_name: str) -> str:
        """
        把相对链接拼成可访问的绝对 URL。

        ⚠️ content.jsp 是特例: 它挂在站根，不在页面目录下。
           /tzgg.htm 里的 content.jsp?wbtreeid=.. 应拼成
             https://xgb.hfut.edu.cn/content.jsp?...      ✓
           而不是
             https://xgb.hfut.edu.cn/tzgg/content.jsp?... ✗ (404)

        不这么处理的话，学工部里点开这类通知全是死链，摘要也抓不到。
        """
        base = self._root_base(source_name)
        if "content.jsp" in href and not href.startswith(("http://", "https://")):
            # 去掉可能的前导 ../ 或目录前缀，锚到站根
            return urljoin(base, "content.jsp" + href.split("content.jsp", 1)[1])
        return urljoin(base, href)

    def _root_base(self, source_name: str) -> str:
        """
        文章链接的拼接基准 = 站根。

        各站文章相对路径是按站点根算的，不是页面目录:
          总务部 /index/tzgg.htm 里 ../info/1029/6465.htm
            → https://zwb.hfut.edu.cn/info/1029/6465.htm   ✓
          学工部 /tzgg.htm 里 info/1057/20001.htm
            → https://xgb.hfut.edu.cn/info/1057/20001.htm  ✓
            （若按页面目录会拼成 /tzgg/info/... → 404）
        """
        for src in SOURCES:
            if src["name"] == source_name:
                parts = urlsplit(src["url"])
                return f"{parts.scheme}://{parts.netloc}/"
        return self.BASE_HOST

    # ---------------- 抓取 ----------------

    def fetch_all(self, pages: int = 2, sources: Optional[list] = None) -> list[dict]:
        """
        抓取所有来源的通知，跨来源去重后按日期降序返回。

        page_urls 默认每个来源取最新的 pages 页。
        """
        all_notices = []
        seen_keys = set()

        for src in (sources or SOURCES):
            notices = self.fetch_source(src, pages=pages)
            for n in notices:
                # 跨来源去重：同一个文件可能同时挂在新闻网和学工部
                key = self._dedup_key(n["link"])
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_notices.append(n)

        all_notices.sort(key=lambda x: x["date"], reverse=True)
        logger.info(f"共获取 {len(all_notices)} 条通知 (跨 {len(sources or SOURCES)} 个来源去重后)")
        return all_notices

    def fetch_source(self, source: dict, pages: int = 2) -> list[dict]:
        """抓取单个来源的首页 + 后续分页"""
        name = source["name"]
        notices = []
        seen_links = set()

        html = self.fetch_page(source["url"])
        if not html:
            logger.warning(f"[{name}] 首页抓取失败，跳过该来源")
            return notices

        _append_unique(notices, seen_links,
                       self.parse_notice_list(html, name, source["list"], source["date"]))

        # 翻页
        for page_url in self._get_page_urls(html, source, pages):
            page_html = self.fetch_page(page_url)
            if not page_html:
                continue
            _append_unique(notices, seen_links,
                           self.parse_notice_list(page_html, name, source["list"], source["date"]))

        logger.info(f"[{name}] 获取 {len(notices)} 条")
        return notices

    def _get_page_urls(self, html: str, source: dict, max_pages: int) -> list[str]:
        """
        从首页分页区提取后续页面 URL。

        分页规律: 末页 = /1.htm，页码越大越新（第2页是 67.htm / 108.htm 这种），
        所以取数字最大的 max_pages-1 个。
        """
        if max_pages <= 1:
            return []

        soup = BeautifulSoup(html, "lxml")
        paging = soup.select_one(source["pagination"])
        if not paging:
            return []

        base = source.get("page_base") or source["url"]
        page_links = []
        for a in paging.find_all("a"):
            href = a.get("href", "")
            m = re.search(r'/(\d+)\.htm$', href)
            if m and href != "javascript:;":
                page_links.append((int(m.group(1)), urljoin(base, href)))

        page_links.sort(key=lambda x: x[0], reverse=True)
        return [url for _, url in page_links[:max_pages - 1]]

    # ---------------- 去重 & 摘要 ----------------

    def _dedup_key(self, link: str) -> str:
        """
        去重键。

        学工部同一篇文章会同时给出两种链接形式:
          content.jsp?wbtreeid=1011&wbnewsid=19978
          info/1011/19978.htm
        同站内归一成 tree/news 组合，避免重复推送。
        """
        m = _CONTENT_JSP_RE.search(link)
        if m:
            return f"news:{m.group(1)}/{m.group(2)}"
        return link

    def fetch_article_summary(self, url: str, max_chars: int = 200) -> str:
        """
        抓取通知详情页，提取正文前 N 字摘要。

        适配合工大各子站不同 HTML 结构:
        - info/xxx/xxx.htm 类型
        - /2026/xxxx/cxxxaxxxx/page.htm 类型
        """
        try:
            html = self.fetch_page(url)
            if not html:
                return ""

            soup = BeautifulSoup(html, "lxml")

            # 从上到下试。基金委(kyy)/科研院那套模板用的是 wp_articlecontent，
            # 排在前面免得落到 body 兜底、把导航栏当正文。
            selectors = [
                "div.v_news_content",   # 最常见的正文区
                "div.news_content",
                "div.article-content",
                "div.article_content",
                "div.wp_articlecontent",   # 基金委 / 科研院
                "div#vsb_content",
                "div.vsb_content",
                "div#wp_news_w6",
                "div.content",
                "article",
                "div.entry-content",
            ]

            text = ""
            for sel in selectors:
                container = soup.select_one(sel)
                if container:
                    text = container.get_text(separator=" ", strip=True)
                    # 选择器可能只圈住了导航壳，正文没进来 —— 换下一个试
                    if len(self._clean_summary(text)) >= 40:
                        break
                    text = ""

            if not text:
                body = soup.find("body")
                if body:
                    text = body.get_text(separator=" ", strip=True)

            text = re.sub(r'\s+', ' ', text).strip()
            text = self._clean_summary(text)

            if len(text) > max_chars:
                text = text[:max_chars].rstrip("，。、；： ") + "…"

            return text

        except Exception as e:
            logger.warning(f"抓取摘要失败 {url}: {e}")
            return ""

    # 公文抬头 / 落款，摘要里是噪音
    _SALUTATION_RE = re.compile(
        r'^(?:各位|尊敬的)?[^：:]{0,12}?'
        r'(?:单位|师生|同学|用户|老师|同志们|部门|学院|学生|教职工|全体|党员)'
        r'[^：:]{0,8}[：:]\s*'
    )
    _SIGNATURE_RE = re.compile(
        r'(?:特此通知|特此公告|特此函告|请遵照执行|望周知|请相互转告)'
        r'[。.]?\s*$'
    )

    # 正文提取失配时会把导航壳吃进来，特征是「您的位置」/「发布时间」/栏目名重复。
    # 找到最后一个「发布时间：」并保证后面有足够正文，就从那里截断。
    _CHROME_CUT_RE = re.compile(r'发布时间\s*[：:]\s*[\d\-年月日\s:]{6,20}')

    @classmethod
    def _clean_summary(cls, text: str) -> str:
        """
        去掉公文腔和网站导航壳，让简介「简洁明了」。

        - 导航壳: 「您的位置：首页 通知公告 … 发布时间：2026-09-10」→ 截掉
        - 抬头:   「校内各单位：」「亲爱的同学们：」→ 删
        - 落款:   「特此通知。」→ 删
        - 附件:   「附件1：xxx」之后的内容 → 截掉
        """
        text = re.sub(r'\s+', ' ', text).strip()

        # 1. 网站导航壳（只在它后面确实还有正文时才截，否则整条就废了）
        for m in reversed(list(cls._CHROME_CUT_RE.finditer(text))):
            if len(text) - m.end() >= 20:
                text = text[m.end():].strip()
                break

        # 2. 抬头可能连着出现两次（不同层级）
        for _ in range(2):
            new = cls._SALUTATION_RE.sub('', text, count=1)
            if new == text:
                break
            text = new.strip()

        # 3. 附件区没有阅读价值
        text = re.split(r'\s*附件\s*[0-9一二三四五六七八九]*\s*[：:]', text)[0]

        text = cls._SIGNATURE_RE.sub('', text).strip()
        return text

    # ---- 短简介（卡片上那 30 字） ----

    SHORT_MAX = 30
    SHORT_MIN = 10

    # 切句见 _split_clauses()：正则做不干净括号配平，改成手动扫。
    # 句首残留的序号、以及被切在括号里的残渣
    _ORDINAL_RE = re.compile(r'^[一二三四五六七八九十\d]{1,3}[、.．)）]\s*')
    _DANGLING_RE = re.compile(r'^[》）)】」』，。、；：]+')
    # 前导引用块: 「根据《…》（皖招考函〔2026〕122号）精神，」→ 整段删掉
    _LEAD_CITE_RE = re.compile(
        r'^(?:根据|按照|依据|为落实|为贯彻)[^，。]{0,80}?'
        r'(?:精神|要求|规定|办法|方案|通知|意见|条例|法)[，,]?\s*'
    )

    # 动作词: 这条通知「要你干什么」。带这些词的句子信息量最大。
    _ACTION_WORDS = (
        "报名", "申报", "截止", "时间", "地点", "举行", "举办",
        "开展", "评选", "认定", "发放", "停水", "停电", "开放", "调整",
        "放假", "考试", "选课", "比赛", "竞赛", "培训", "招聘", "提交",
        "报送", "受理", "开始", "结束", "安排", "举行", "召开",
        "举办", "征集", "检修", "施工", "搬迁", "领取", "发放", "公示", "立项",
    )

    # 虚词收尾 = 话没说完（「…的通知」单挂一句是残的，「…的」更是）。
    # 注意别把「…评审工作」这类名词短语算进来，它们在中文里能独立成句。
    _DANGLING_TAIL = "的和与及等或并而为于在对把将向从以如第至到"

    # 从属连词开头 + 没有动作词 = 半截话（「接包河供电公司通知」「经学院推荐」）
    _SUBORDINATE_RE = re.compile(
        r'^(?:接|据|经|根据|按照|依据|由于|鉴于|随着|通过|为|为了|兹|现将|现就)')

    # 「现将…通知如下」这类公文体壳子，剥掉才看得见正题
    _SCAFFOLD_HEAD_RE = re.compile(r'^(?:现将|现就|兹将|特将)\s*')
    _SCAFFOLD_TAIL_RE = re.compile(
        r'\s*(?:有关事项|相关事项|有关事宜|相关事宜)?'
        r'(?:通知|说明|安排|公布|公告|通告)?如下[：:]?$')
    _SCAFFOLD_TAIL2_RE = re.compile(r'\s*(?:有关事项|相关事项|有关事宜|相关事宜)$')

    # 表格被抽成正文：一堆栏头词摞在一起，没有一句人话
    _TABLE_JUNK_RE = re.compile(r'工作模块|完成时限|面向对象|项目负责人|主办单位|承办单位|备注')

    # 节标题常常和正文黏成一句（「活动时间主题活动集中在2026年9月实施」
    # 「学校申报截止日期和材料报送 项目申请人按照…」），读起来像结巴。
    # 只收几个不会误伤的，剥完还得剩得下东西。
    _SECTION_HEAD_RE = re.compile(
        r'^(?:[^\d，。、；：]{0,4})?'
        r'(?:活动时间|活动主题|活动对象|活动安排|征集截止时间|征集时间|申报截止日期|'
        r'报送方式|报送时间|报送要求|报名时间|考试时间|比赛时间|参赛对象|'
        r'组织机构|承办单位|联系方式|联系电话|工作要求|工作内容|项目内容|'
        r'申报条件|评审程序|资助方向|时间安排|总体要求|评选对象|评选范围)')

    # 开场套话/引用: 「为深入学习贯彻…」「根据《…》（x号）精神，」
    # 这类开头没有信息量，偏偏公文里最靠前、最容易被选中，必须显式压分。
    _BOILERPLATE_RE = re.compile(
        r'^(?:为|根据|按照|依据|为了|为深入|为贯彻|为落实|为认真|为扎实|为切实|为做好|为进一步)'
        r'[^，。]{0,60}?(?:精神|思想|要求|通知|意见|办法|法规|规定|文件|战略|部署|指示|方案|条例)'
    )
    # 纯主题陈述（"为…，现将…"里的前半截），整句只有目的没有动作
    _PURPOSE_RE = re.compile(r'^(?:为|为了)[^，。]{4,60}$')
    # 时政套话。「全面落实习近平总书记关于新域新质的重要论述」—— 每篇公文
    # 都能套上，放哪条通知上都成立，等于没说
    _XI_RE = re.compile(
        r'习近平总书记|习近平新时代|党的二十大精神|二十届[一二三四五六七八九十]*中全会|'
        r'重要论述|重要思想|重要讲话精神')
    # 对着收件人喊话：「请有关单位仔细研读通知」。说的是要读者怎么样，
    # 不是一个字的内容
    _PLEASE_RE = re.compile(r'^请(?:各|有关|相关|全校|广大|于|勿|注意)')
    # 通用办事流程话术：「材料报送 项目申请人按照指南要求填报申请书及附件
    # 材料并提交至所在学院」。基金委那批通知正文只剩这一句，五条通知一字不差
    # —— 它说的是怎么交材料，不是这条通知讲什么，还不如标题剥壳
    _GENERIC_BODY_RE = re.compile(r'材料报送|填报申请书|提交至所在|按照指南要求|报送方式')
    # 流程名词收尾 = 事还没说完（「经个人申报、单位推荐、专家评审」后面
    # 本该跟「共评出…」）。带数字的除外，那说明结果已经出来了
    _PROCESS_TAIL_RE = re.compile(
        r'(?:环节|阶段|程序|流程|评审|推荐|申报|审核|审查|考察|遴选|初评|复评|'
        r'研究|批准|同意|决定)$')
    # 以虚词收尾 = 话没说完。「…的精神」「…的水平」「…的使命」
    _VAGUE_TAIL_RE = re.compile(r'(?:的)?(?:精神|思想|水平|使命|成果|要求|规定|指示|部署)$')
    # 光秃秃的文号（「（皖教工委函〔2026〕216号）」「（皖教工委函〔2026〕216号）要求」）
    # 注意文号里常套一层〔〕，内层不能排除
    _DOCNUM_ONLY_RE = re.compile(r'^[（(【〔][^）)]{2,24}[）)][^，。]{0,8}$')
    # 网页页脚/工具栏被当成正文抓进来
    _PAGE_CHROME_RE = re.compile(r'浏览次数|联系我们|版权所有|地址[:：]中国|技术支持')
    # 结尾客套话。「因停水施工给您带来的不便，敬请谅解」—— 放哪儿都对，
    # 唯独不说明这条通知要干什么
    _APOLOGY_RE = re.compile(
        r'给您带来|给您造成|敬请谅解|敬请理解|感谢您的|感谢您对|由此带来|'
        r'请予以理解|望谅解|特此通知|特此公告|特此说明')
    # 括注的简称：「（以下简称CET）」「（CET－SET）」。公文里满篇都是，
    # 30 字的额度经不起这么花 —— 剥掉不影响阅读。但「（第一批）」
    # 「（合肥校区）」是实义，不能碰，所以只收简称和纯字母的
    _ALIAS_PAREN_RE = re.compile(
        r'[（(](?:以下简称|简称|下称)[^）)]{0,12}[）)]'
        r'|[（(][A-Za-z][A-Za-z0-9\-－]{1,10}[）)]')
    # 「四、六级」「一、二级」—— 这里的顿号是编号的一部分，不是并列断点
    _ENUM_DUN_RE = re.compile(r'[一二三四五六七八九十\d]、[一二三四五六七八九十\d]')
    # 正文里只有一句「去哪儿看」的指路语，没有实质内容。
    # 基金委那批通知正文就这么一句，拿去当简介等于什么都没说
    _NAV_JUNK_RE = re.compile(
        r'项目管理-项目指南|见通告原文|详见附件|详见原文|点击查看|见附件|'
        r'菜单栏中查看|菜单栏查看')

    @classmethod
    def _short_summary(cls, title: str, summary: str,
                       limit: int = SHORT_MAX) -> str:
        """
        从正文摘要里摘出 30 字以内、能独立看懂的一句。

        不能直接切前 30 字 —— 会卡在句子中间变成
        「因翡翠湖校区供水管网损坏，需对部分区域进」这种半截话。

        正文里挑不出人话时（整篇是标题的复述、是张表格、是网页页脚），
        退回去用标题本身剥壳当简介 —— 卡片上必须有一句，哪怕它
        只是标题更短的说法。
        """
        text = cls._normalize_space(summary or "")
        digest = cls._title_digest(title, limit)

        # 正文压根没法用：空的、网页页脚、表格栏头摞一起
        if not text or cls._is_unusable(text):
            return digest

        if len(text) <= limit and not cls._looks_incomplete(text):
            return text.rstrip("，。、；： ")

        parts = []
        for p in cls._split_clauses(text):
            p = cls._clean_fragment(p)
            if len(p) < 5:
                continue
            # 大半句都是标题的原话 —— 基金委那批通告通篇在重复标题。
            # 读者刚看完标题，这种句子再占一行也是白占（真需要它的内容时，
            # 下面还有标题剥壳兜着，信息一点不少）。宁可让位给正文里
            # 那些「笔试 12 月 12 日举行」的干货。
            if cls._title_overlap(title, p) >= 0.72:
                continue
            parts.append(p)
        if not parts:
            return digest

        # 先把每条候选「最终会显示成什么样」定下来：该截的截，截完读不通的
        # 直接淘汰。只在原文上打分是不够的 ——
        # 「学校申报截止日期和材料报送项目申请人按照指南要求填报申请书及」
        # 原文是个完整句，截完就成了残句。
        cands = []
        for idx, p in enumerate(parts):
            if len(p) <= limit:
                src = shown = p
            else:
                src = cls._strip_redundant_head(p, title)
                shown = cls._clip(src, limit)
            shown = shown.strip("，。、；： ")
            if len(shown) < 5 or cls._looks_incomplete(shown):
                continue
            cands.append((idx, p, shown, src))
        if not cands:
            return digest

        # 全篇都没提动作词时只能矮子里拔将军；只要有一句提了，就优先那句 ——
        # 「安徽省市场监督管理局、安徽省精神文明建设办公室」这种名单，
        # 读完也不知道要干什么
        has_action = any(any(w in p for w in cls._ACTION_WORDS)
                         for _, p, _, _ in cands)

        def score(cand):
            idx, part, _, _ = cand
            has = any(w in part for w in cls._ACTION_WORDS)
            overlap = cls._title_overlap(title, part)
            s = 1.2 / (idx + 1)                        # 越靠前越可能是主旨
            s += sum(0.6 for w in cls._ACTION_WORDS if w in part)
            s += overlap * 1.4
            if len(part) <= limit:                     # 不用截断的整句加分
                s += 0.4
            # 「积极组织征集」这种六字短语，动词齐全但没有宾语，读完等于没读。
            # 能当简介的短句至少也得有十来个字
            if len(part) < 10:
                s -= 1.5
            if cls._BOILERPLATE_RE.match(part):
                s -= 2.5                               # 开场套话，压到底
            if cls._PURPOSE_RE.match(part):
                s -= 1.5                               # 只讲目的不讲事
            if cls._XI_RE.search(part):
                s -= 2.0                               # 时政套话，哪条通知都能套
            # 「请有关单位仔细研读通知」—— 对着收件人喊话，不是内容
            if cls._PLEASE_RE.match(part):
                s -= 1.5
            # 开头就是时间的句子（"X月X日，…"），信息量通常不如点明事件的那句，
            # 而卡片上方已经显示了日期
            if re.match(r'^[\d一二三四五六七八九十]{1,4}[年月日]', part) and \
                    len(part) > limit:
                s -= 0.8
            # 以虚词收尾 = 话没说完（「…的重要使命」）
            if cls._VAGUE_TAIL_RE.search(part):
                s -= 1.0
            # 半截话（「接包河供电公司通知」「经学院推荐」）。这句往往是全篇第一句，
            # 靠位置分压过后面那些真正说事的句子，必须扣回来。
            if cls._looks_incomplete(part):
                s -= 1.8
            # 只是部分复述标题：扣分但不判死 —— 复述里带的新信息（几点停水、
            # 哪个校区）有时候正是最该看的那句
            if overlap >= 0.6:
                s -= 0.8
            # 开头就是日期（「特定于2026年10月16日」），日期卡片上已经有了
            if re.match(r'^(?:特定于|定于|兹定于)', part):
                s -= 1.2
            if has_action and not has:
                s -= 1.5
            # 几乎全是数字的日子（「2026年9月-12月」）—— 日期卡片上已经有一份了
            if part and sum(c.isdigit() for c in part) / len(part) >= 0.5:
                s -= 1.5
            return s

        ranked = max(cands, key=score)
        # 挑中的是「前言」里那种讲意义的漂亮话：没有动作词、没有具体数字、
        # 也跟标题对不上号（「切实保障国家各项资助政策和措施真正落实到家庭
        # 经济困难学生身上」「推动学生宪法宣传教育常态化、长效化」）。
        # 读完不知道这条通知要他干什么，不如回去用标题剥壳
        # 挑中的是「前言」里那种讲意义的漂亮话：没有动作词、没有具体数字、
        # 也跟标题对不上号（「切实保障国家各项资助政策和措施真正落实到家庭
        # 经济困难学生身上」「推动学生宪法宣传教育常态化、长效化」）。
        # 读完不知道这条通知要他干什么，不如回去用标题剥壳
        if not any(w in ranked[1] for w in cls._ACTION_WORDS) \
                and not any(c.isdigit() for c in ranked[1]) \
                and cls._title_overlap(title, ranked[1]) < 0.5:
            return digest or ranked[2]
        # 挑中的是通用办事流程话术，又不带任何日期/数字 —— 十几条通知共用
        # 同一句，标题剥壳反而能说清这是哪条
        if digest and cls._GENERIC_BODY_RE.search(ranked[1]) \
                and not any(c.isdigit() for c in ranked[1]):
            return digest
        # 一条能看的都没有 —— 回去用标题剥壳，别硬凑
        if score(ranked) < 0.5:
            return digest
        # 选中的得靠截断才塞得下，而它本来就是标题的换皮说法 ——
        # 那就直接用剥了壳的标题，至少断在干净的地方
        if len(ranked[1]) > limit and \
                cls._title_overlap(title, ranked[1]) >= 0.6:
            return digest
        # 截断截在了词中间。正文里挑不出别的，就用标题剥壳顶上 ——
        # 「…并提交至所」这种半截话比复述标题难看得多
        if digest and not cls._cut_clean(ranked[2], ranked[3]):
            return digest
        return ranked[2]

    @classmethod
    def _is_unusable(cls, text: str) -> bool:
        """正文压根不是人话：网页页脚、表格栏头"""
        if cls._PAGE_CHROME_RE.search(text) and len(text) < 200:
            return True
        # 表格抽出来的文字：栏头词摞一起，几乎不断句
        if cls._TABLE_JUNK_RE.search(text) and text.count("。") <= 1:
            return True
        return False

    @classmethod
    def _looks_incomplete(cls, part: str) -> bool:
        """这句能不能独立站住 —— 虚词收尾、或从属连词开头又不带动作"""
        if not part:
            return True
        if part[-1] in cls._DANGLING_TAIL:
            return True
        # 「…在合肥工业大学举办第三届」—— 届次后面本该跟活动名，断了就是个悬念
        if re.search(r'第[一二三四五六七八九十百\d]{1,4}[届次期批讲]$', part):
            return True
        # 从属句读起头就是半句，除非它自己带动作词
        # （「经研究，决定于…举办…」是完整的，「经个人申请、学院推荐等环节」不是）
        if cls._SUBORDINATE_RE.match(part) and \
                not any(w in part for w in cls._ACTION_WORDS):
            return True
        # 从属句读起头 + 流程名词收尾 + 通篇没数字 = 结果还没说出来
        if cls._SUBORDINATE_RE.match(part) and not any(c.isdigit() for c in part) \
                and cls._PROCESS_TAIL_RE.search(part):
            return True
        return False

    @classmethod
    def _title_digest(cls, title: str, limit: int = SHORT_MAX) -> str:
        """
        正文指不上时，把标题剥掉公文壳当简介。

        「关于做好2026-2027学年研究生学业奖学金评审工作的通知」
        → 「做好2026-2027学年研究生学业奖学金评审工作」

        比重复标题短一截，扫一眼就知道这条讲什么。剥完太短说明
        标题本身就是光杆（「公示」），那就没有简介可言。
        """
        t = cls._normalize_space(title or "")
        if not t:
            return ""
        t = re.sub(r'^关于', '', t)
        t = re.sub(r'[（(][^）)]{0,24}[）)]$', '', t)          # 尾部补充说明
        # 「…的通知」剥掉是净赚，但「…申报指南征求意见」里的「意见」是动词
        # 的宾语，剥了就成了「…申报指南征求」—— 半截话
        t = re.sub(
            r'(?<!征求)(?<!反馈)(?<!提出)(?<!报送)(?<!征集)'
            r'(?:的)?(?:通知|通告|公示|公告|决定|意见|函|批复)$', '', t)
        t = t.strip("，。、；： ")
        if len(t) < 6:
            return ""
        if len(t) > limit:
            # 公文标题是「限定语 + 正题」，正题永远在后面。超长时把前面那截
            # 限定语丢掉，从正题起算 ——「基金委发布2026年度交叉科学部关于
            # 征集重大非共识项目立项建议」→ 去掉「基金委发布」正好卡进 30 字，
            # 比在「立项建」处硬切好看得多。挑最长的那个能装下的后缀
            for m in re.finditer(r'[：:——]|”|(?:发布|印发|转发|关于)', t):
                tail = t[m.end():].lstrip("—－：: ")
                if 8 <= len(tail) <= limit and _balanced(tail):
                    return tail
        return cls._clip(t, limit)

    @staticmethod
    def _normalize_space(text: str) -> str:
        """
        归一化 HTML 提取留下的空格。

        抽取正文时用 separator=" " 拼接，会把「2026年度」拆成「2026 年度」、
        「9月30日」拆成「9 月 30 日」，白白吃掉字符额度。中文里的空格基本
        都是噪音，只保留中文和英文/数字之间的那一个。
        """
        text = re.sub(r'\s+', ' ', text).strip()
        # 「17:00 二、报送方式」——节标题前的空格是原文里唯一的断句线索，
        # 后面的规则会把它抹掉，先换成硬换行存着
        text = re.sub(r'(?<=[\d。；：！？]) (?=[一二三四五六七八九十]{1,3}[、.．])',
                      '\n', text)
        # 数字/英文 与 中文 之间本来就不该有空格（公文排版不会这么写）
        text = re.sub(r'(?<=[一-鿿]) (?=[\dA-Za-z])', '', text)
        text = re.sub(r'(?<=[\dA-Za-z]) (?=[一-鿿])', '', text)
        # 中文之间、数字内部的零散空格
        text = re.sub(r'(?<=[一-鿿]) (?=[一-鿿])', '', text)
        text = re.sub(r'(?<=\d) (?=\d)', '', text)
        # 「2026 -2027」「10月16日 — 18日」：破折号两边的空格
        text = re.sub(r'(?<=[\d一-鿿]) (?=[-—－~～至])', '', text)
        text = re.sub(r'(?<=[-—－~～]) (?=[\d一-鿿])', '', text)
        text = re.sub(r'\s*([，。、；：！？（）《》“”])\s*', r'\1', text)
        return text.strip()

    @staticmethod
    def _split_clauses(text: str) -> list:
        """
        括号感知的切句。

        正则做不干净：`根据《…考试报名》（皖招考函〔2026〕122号）精神，现…`
        里的逗号在书名号/括号内，用 lookahead 判断配平很容易误伤。
        直接扫一遍，只在括号外切。

        切点: 。！？； 换行 | 括号外的逗号 | 「一、」「1.」这类小标题序号
        """
        OPEN, CLOSE = "《（(【〔", "》）》】〕"
        out, buf, depth = [], [], 0
        i, n = 0, len(text)

        while i < n:
            ch = text[i]

            if ch in OPEN:
                depth += 1
            elif ch in CLOSE:
                depth = max(0, depth - 1)

            # 小标题序号前断开（「一、」「3.」），但别把「2026.09」切开，
            # 也别把「大学英语四、六级」里的「四、」当序号 —— 序号只会出现在
            # 句末标点/换行/数字之后，或者整段的开头
            if depth == 0 and (ch in "一二三四五六七八九十" or ch.isdigit()):
                prev = text[i - 1] if i > 0 else ""
                if not prev or prev in "。；：！？\n" or prev.isdigit() or \
                        not "".join(buf).strip():
                    m = re.match(
                        r'[一二三四五六七八九十]{1,3}[、.．]|\d{1,2}[、.．](?!\d)',
                        text[i:])
                    if m and len("".join(buf).strip()) >= 5:
                        out.append("".join(buf))
                        buf = []
                        i += m.end()
                        continue

            if depth == 0 and ch in "。！？；!?;\n":
                buf.append(ch)
                out.append("".join(buf))
                buf = []
                i += 1
                continue

            if depth == 0 and ch == "，":
                cur = "".join(buf)
                # 逗号后内容太短就先不断，免得切出一地碎片
                if len(text[i + 1:].split("，")[0]) >= 4 and len(cur.strip()) >= 5:
                    out.append(cur)
                    buf = []
                    i += 1
                    continue

            buf.append(ch)
            i += 1

        if "".join(buf).strip():
            out.append("".join(buf))
        return out

    @classmethod
    def _clean_fragment(cls, part: str) -> str:
        """
        收拾切分残留。

        - 「（一）征集时间征集截止时间2026年9月30日17:00」→ 去掉序号标记
        - 「开展…检查工作（一）」→ 序号悬挂在末尾，说明正文被切掉了，丢弃
        - 「时间安排2026年9月-12月三…」→ 取后半段
        - 「根据《…》（皖招考函〔2026〕122号）精神，」→ 纯引用，正文不在这
        - 「现将有关事项通知如下」→ 公文体壳子，剥掉

        注意别把「（一）征集时间」这种正常节标题当成硬切残渣 ——
        序号后面有内容是正常的，只有悬挂在末尾才是残缺。
        """
        part = part.strip().strip("，。、；： ")
        part = cls._ORDINAL_RE.sub('', part)

        # 以序号标记收尾 = 正文被切掉了，不可读
        if re.search(r'[（(][一二三四五六七八九十\d]{1,3}[)）]\s*$', part):
            return ""

        # 以序号标记开头 = 节标题，去掉标记取内容
        part = re.sub(r'^[（(][一二三四五六七八九十\d]{1,3}[)）]\s*', '', part)

        # 括注简称先剥掉，别占字数
        part = cls._ALIAS_PAREN_RE.sub('', part)

        # 节标题和正文黏在一起：「一、重大非共识项目资助定位重大非共识项目
        # 资助科研人员从事…」—— 正文开头和标题重了那么几个字，重复点之前
        # 一律是标题。_SECTION_HEAD_RE 只认得几个常见栏头，这个是兜底
        for n in range(4, 13):
            if len(part) < 2 * n:
                break
            head = part[:n]
            again = part.find(head, n)
            if 0 < again <= n + 6:
                part = part[again:]
                break

        # 前导引用块、被切在括号里的残渣
        part = cls._LEAD_CITE_RE.sub('', part)
        part = cls._DANGLING_RE.sub('', part)

        # 公文体壳子
        part = cls._SCAFFOLD_HEAD_RE.sub('', part)
        part = cls._SCAFFOLD_TAIL_RE.sub('', part)
        part = cls._SCAFFOLD_TAIL2_RE.sub('', part)
        part = part.strip("，。、；： ")

        # 黏在正文前面的节标题（「活动时间主题活动集中在…」→「主题活动集中在…」）
        m = cls._SECTION_HEAD_RE.match(part)
        if m and len(part) - m.end() >= 8:
            head = part[:m.end()]
            rest = part[m.end():].lstrip("和及与等的地")
            # 剥完只剩个日期（「征集截止时间2026年9月30日17:00」），说明标题
            # 本身就是全部信息 —— 剥了等于把「什么时候截止」扔了
            if not (_is_bare_date(rest) and
                    any(w in head for w in cls._ACTION_WORDS)):
                # 「评选对象1. 纳入…」剥完还挂着个序号，顺手带走
                part = cls._ORDINAL_RE.sub('', rest)
        if len(part) < 5:
            return ""

        # 前面挂着一个词（「时间安排2026年…」）就把它去掉。但要是剥完只剩个
        # 光日期（「征集截止时间2026年9月30日17:00」），说明前面那截才是信息
        m = re.match(r'^[^\d一二三四五六七八九十]{2,10}?(\d{4}年.*)$', part)
        if m and len(part) <= cls.SHORT_MAX and not _is_bare_date(m.group(1)):
            part = m.group(1)

        if part.endswith(("…", "...", "等", "及", "和", "与", "、")):
            return ""
        # 数字收尾 + 提到年份 = 多半切在「2026年10」这种地方。但 17:00 不算
        if re.search(r'(?<![:：\d])[\d一二三四五六七八九十]{1,2}$', part) and "年" in part:
            return ""

        # 光秃秃的文号、网页页脚、结尾客套话、指路语
        if cls._DOCNUM_ONLY_RE.match(part) or cls._PAGE_CHROME_RE.search(part) \
                or cls._APOLOGY_RE.search(part) or cls._NAV_JUNK_RE.search(part):
            return ""
        # 被切在引号/书名号里的残渣（「讲宪法”活动的相关通知要求」）—— 引号
        # 落单就说明起点是从中间抠出来的
        if not _balanced(part):
            return ""
        return part.strip("，。、；： ")

    @classmethod
    def _strip_redundant_head(cls, text: str, title: str) -> str:
        """
        开头的学年/学期限定语如果标题里已经写了，先摘掉。

        「2026年下半年全国大学英语四、六级笔试和口试将分别于12月12日…」
        —— 标题里就有「2026年下半年」，卡片上方正显示着，重复它等于白花
        6 个字。摘掉之后省下的额度刚好够把考试日期装进来。
        """
        m = re.match(r'(?:\d{4}\s*年\s*(?:上|下)半年|\d{4}\s*[-—－]\s*\d{4}\s*学年'
                     r'|\d{4}\s*学年|\d{4}\s*年)', text)
        if m and m.group(0).replace(" ", "") in (title or "").replace(" ", ""):
            rest = text[m.end():].lstrip("，。、；： ")
            if len(rest) >= 10:
                return rest
        return text

    @staticmethod
    def _title_overlap(title: str, part: str) -> float:
        """句子里有多少 2 字词出现在标题中（0~1 归一）"""
        title = title or ""
        grams = {part[i:i + 2] for i in range(len(part) - 1)}
        if not grams:
            return 0.0
        hit = sum(1 for g in grams if g in title)
        return hit / len(grams)

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        """
        整句太长时的退路。

        先保证不切在书名号/括号里面 —— 「为贯彻落实《中华人民共和国科学技术普及法》
        《全民科学素质行动」这种断法比切短更难看。再在 limit 内找最后一个
        并列符号断开：「、」「暨」连接的是并列成分，从那里断比从逗号断
        更不容易缺胳膊少腿。实在没有合适断点就硬切。
        """
        head = _bracket_safe(text, limit)
        # 「—」「－」不当断点：CET－SET 这类缩写会被拦腰截断。
        # 「和」「及」排最后：并列成分从这里断开风险略大，所以和「、」分开处理，
        # 只在断点靠后（≥60%）时才认
        for sep in ("、", "；", "：", "暨"):
            cut = _last_sep(head, sep)
            if cut < int(limit * 0.5):
                continue
            out = head[:cut].rstrip("，。、；：—－ ")
            if _balanced(out):
                return out
        for sep in ("，", "和", "及"):
            cut = head.rfind(sep)
            if cut < int(limit * 0.6):
                continue
            out = head[:cut].rstrip("，。、；： ")
            if len(out) >= 8 and _balanced(out):
                return out
        return head.rstrip("，。、；： ")

    @staticmethod
    def _cut_clean(shown: str, source: str) -> bool:
        """
        _clip 是不是断在了干净的地方。

        断点落在并列/句读符号上（也就是下一句正是从这个符号起头的），或者
        退到了书名号之前，都算干净；硬切在第 30 个字上（「…并提交至所」
        「…检查和2026年」）就是断在词中间，读着像乱码。

        要拿真正被截的那个串来比 —— 候选可能先被摘掉了重复的学期限定语。
        """
        if len(shown) >= len(source):
            return True
        nxt = source[len(shown):len(shown) + 1]
        return not nxt or nxt in "，。、；：！？暨和及《（(【〔“"

    def fetch_recent_notices(self, days: int = 7, pages: int = 3) -> list[dict]:
        """获取最近 N 天内的通知"""
        from datetime import datetime, timedelta

        all_notices = self.fetch_all(pages=pages)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        recent = [n for n in all_notices if n["date"] >= cutoff]
        logger.info(f"最近 {days} 天内共 {len(recent)} 条通知")
        return recent


_BRACKET_OPEN = "《（(【〔“"


def _last_sep(head: str, sep: str) -> int:
    """
    找 head 里最后一个能当断点的 sep。

    「、」要排掉「四、六级」这种编号 —— 在那里断开就成了
    「2026年下半年全国大学英语四」。找不到返回 -1。
    """
    pos = head.rfind(sep)
    while pos >= 0:
        if not (sep == "、" and pos > 0
                and NoticeScraper._ENUM_DUN_RE.match(head, pos - 1)):
            return pos
        pos = head.rfind(sep, 0, pos)
    return -1


_BARE_DATE_RE = re.compile(r'[\d年月日\-—－~～:：.至到\s]+')


def _is_bare_date(text: str) -> bool:
    """整段就是个日期/时间段，没别的信息（「2026年9月30日17:00」「9月-12月」）"""
    return bool(text) and bool(_BARE_DATE_RE.fullmatch(text))


def _balanced(text: str) -> bool:
    """括号/引号是否配对"""
    if sum(text.count(c) for c in _BRACKET_OPEN) != \
            sum(text.count(c) for c in "》）》】〕”"):
        return False
    return True


def _bracket_safe(text: str, limit: int) -> str:
    """
    截到 limit 个字符，但不能把书名号/括号切一半。

    「为贯彻落实《中华人民共和国科学技术普及法》《全民科学素质行动」——
    最后一截断在《》里，读起来像乱码。宁可退回到那个括号之前。
    """
    head = text[:limit]
    if _balanced(head):
        return head
    for i in range(len(head) - 1, -1, -1):
        if text[i] in _BRACKET_OPEN:
            return text[:i]
    return head


def _append_unique(notices: list, seen_links: set, new_notices: list):
    """按链接去重后追加"""
    for n in new_notices:
        if n["link"] not in seen_links:
            seen_links.add(n["link"])
            notices.append(n)


# 命令行测试
if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    scraper = NoticeScraper(timeout=20)

    only = sys.argv[1] if len(sys.argv) > 1 else None
    srcs = [s for s in SOURCES if not only or only in s["name"]]
    notices = scraper.fetch_all(pages=2, sources=srcs)

    print(f"\n共 {len(notices)} 条\n" + "=" * 70)
    for n in notices[:25]:
        print(f"[{n['date']}] [{n['source_tab']}] {n['title'][:40]}")
        print(f"   → {n['link']}")

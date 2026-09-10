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

    def fetch_recent_notices(self, days: int = 7, pages: int = 3) -> list[dict]:
        """获取最近 N 天内的通知"""
        from datetime import datetime, timedelta

        all_notices = self.fetch_all(pages=pages)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        recent = [n for n in all_notices if n["date"] >= cutoff]
        logger.info(f"最近 {days} 天内共 {len(recent)} 条通知")
        return recent


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

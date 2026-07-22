"""
合工大新闻网通知公告 - 网页抓取模块

抓取 https://news.hfut.edu.cn/tzgg2.htm 的通知列表，
解析标题、日期、链接等信息。
"""

import re
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class NoticeScraper:
    """通知公告页面抓取器"""

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

    def fetch_page(self, url: str) -> str:
        """获取页面 HTML 内容"""
        logger.info(f"正在抓取: {url}")
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.encoding = "utf-8"
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.error(f"抓取失败 {url}: {e}")
            return ""

    def parse_notice_list(self, html: str, source_tab: str = "") -> list[dict]:
        """
        从 HTML 中解析通知列表。

        返回: [{"title": "标题", "date": "2026-07-21", "link": "完整URL",
                "source_tab": "教学科研", "raw_date": "2026年07月21日"}, ...]
        """
        soup = BeautifulSoup(html, "lxml")
        notices = []

        # 主列表区: div.list.wrap#tzz 下的所有 li
        list_area = soup.find("div", class_="list", id="tzz")
        if not list_area:
            # 回退：尝试找任何 div.list-wrap 下的 ul>li
            list_area = soup.find("div", class_="list")

        if not list_area:
            logger.warning("未找到通知列表区域")
            return notices

        for li in list_area.find_all("li"):
            a_tag = li.find("a")
            if not a_tag:
                continue

            # 提取链接
            link = a_tag.get("href", "")
            if link:
                link = urljoin(self.BASE_HOST, link)

            # 提取标题 (优先用 title 属性，其次用 p 标签文本)
            title = a_tag.get("title", "")
            if not title:
                p_tag = a_tag.find("p")
                title = p_tag.get_text(strip=True) if p_tag else ""

            # 提取日期
            i_tag = a_tag.find("i")
            raw_date = i_tag.get_text(strip=True) if i_tag else ""
            date_str = self._parse_date(raw_date)

            if title and date_str:
                notices.append({
                    "title": title.strip(),
                    "date": date_str,
                    "link": link,
                    "source_tab": source_tab,
                    "raw_date": raw_date.strip(),
                })

        return notices

    def parse_tab_notices(self, html: str, tab_id: str, tab_name: str) -> list[dict]:
        """
        解析指定 tab 下的通知列表。

        tab_id: 'c01' (教学科研) 或 'c02' (其他通知)
        """
        soup = BeautifulSoup(html, "lxml")
        notices = []

        tab_div = soup.find("div", id=tab_id)
        if not tab_div:
            logger.warning(f"未找到 tab: {tab_id}")
            return notices

        for li in tab_div.find_all("li"):
            a_tag = li.find("a")
            if not a_tag:
                continue

            link = a_tag.get("href", "")
            if link:
                link = urljoin(self.BASE_HOST, link)

            title = a_tag.get("title", "")
            if not title:
                p_tag = a_tag.find("p")
                title = p_tag.get_text(strip=True) if p_tag else ""

            i_tag = a_tag.find("i")
            raw_date = i_tag.get_text(strip=True) if i_tag else ""
            date_str = self._parse_date(raw_date)

            if title and date_str:
                notices.append({
                    "title": title.strip(),
                    "date": date_str,
                    "link": link,
                    "source_tab": tab_name,
                    "raw_date": raw_date.strip(),
                })

        return notices

    def fetch_all(self, pages: int = 2) -> list[dict]:
        """
        抓取多页通知。

        首页 tzgg2.htm 包含 20 条混合通知。
        分页 URL 格式: tzgg2/1256.htm (数字越大越新)
        同时抓取首页两个 tab 的独立内容作为补充。
        """
        all_notices = []
        seen_links = set()

        # 1. 抓取首页（包含混合列表 + 两个 tab 的独立列表）
        html = self.fetch_page(self.BASE_URL)
        if html:
            # 主混合列表
            for notice in self.parse_notice_list(html):
                if notice["link"] not in seen_links:
                    seen_links.add(notice["link"])
                    all_notices.append(notice)

            # 教学科研 tab 独立列表
            for notice in self.parse_tab_notices(html, "c01", "教学科研"):
                if notice["link"] not in seen_links:
                    seen_links.add(notice["link"])
                    all_notices.append(notice)

            # 其他通知 tab 独立列表
            for notice in self.parse_tab_notices(html, "c02", "其他通知"):
                if notice["link"] not in seen_links:
                    seen_links.add(notice["link"])
                    all_notices.append(notice)

        # 2. 抓取分页（第2页起）
        if pages > 1:
            # 分页从 1256 开始递减（当前最新第2页）
            # 先从首页解析出分页链接
            page_urls = self._get_page_urls(html, pages) if html else []

            for page_url in page_urls:
                page_html = self.fetch_page(page_url)
                if page_html:
                    for notice in self.parse_notice_list(page_html):
                        if notice["link"] not in seen_links:
                            seen_links.add(notice["link"])
                            all_notices.append(notice)

        # 去重后按日期降序排序
        all_notices.sort(key=lambda x: x["date"], reverse=True)
        logger.info(f"共获取 {len(all_notices)} 条通知 (去重后)")
        return all_notices

    def _get_page_urls(self, html: str, max_pages: int) -> list[str]:
        """从首页分页区域提取后续页面URL"""
        soup = BeautifulSoup(html, "lxml")
        urls = []

        # 找到分页区域中的链接
        paging_div = soup.find("div", class_="pb_sys_common")
        if not paging_div:
            return urls

        # 收集页码链接，数字越大越新
        page_links = []
        for a in paging_div.find_all("a"):
            href = a.get("href", "")
            if "tzgg2/" in href:
                # 提取页码数字
                match = re.search(r'tzgg2/(\d+)\.htm', href)
                if match:
                    page_num = int(match.group(1))
                    full_url = urljoin(self.BASE_HOST, href)
                    page_links.append((page_num, full_url))

        # 按页码降序（越大越新），取前 max_pages-1 个
        page_links.sort(key=lambda x: x[0], reverse=True)
        for _, url in page_links[:max_pages - 1]:
            urls.append(url)

        return urls

    def _parse_date(self, raw_date: str) -> str:
        """
        将中文日期转为 ISO 格式。

        "2026年07月21日" -> "2026-07-21"
        """
        if not raw_date:
            return ""

        # 匹配 "2026年07月21日" 格式
        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', raw_date)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        # 匹配 "2026-07-21" 等已格式化的日期
        match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', raw_date)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"

        return raw_date.strip()

    def fetch_article_summary(self, url: str, max_chars: int = 200) -> str:
        """
        抓取通知详情页，提取正文前 N 字摘要。

        适配合工大各子站不同 HTML 结构：
        - info/xxx/xxx.htm 类型
        - /2026/xxxx/cxxxaxxxx/page.htm 类型
        """
        try:
            html = self.fetch_page(url)
            if not html:
                return ""

            soup = BeautifulSoup(html, "lxml")

            # 尝试多种常见正文容器
            selectors = [
                "div.v_news_content",   # 最常见的正文区
                "div.news_content",
                "div.article-content",
                "div.article_content",
                "div.content",
                "div#vsb_content",
                "div.vsb_content",
                "article",
                "div.entry-content",
            ]

            text = ""
            for sel in selectors:
                container = soup.select_one(sel)
                if container:
                    text = container.get_text(separator=" ", strip=True)
                    break

            # 如果都没找到，取 body 内所有文字
            if not text:
                body = soup.find("body")
                if body:
                    text = body.get_text(separator=" ", strip=True)

            # 清理：去掉多余空白
            text = re.sub(r'\s+', ' ', text).strip()

            # 截取
            if len(text) > max_chars:
                text = text[:max_chars] + "…"

            return text

        except Exception as e:
            logger.warning(f"抓取摘要失败 {url}: {e}")
            return ""

    def fetch_recent_notices(self, days: int = 7, pages: int = 3) -> list[dict]:
        """
        获取最近 N 天内的通知。

        通过多抓几页来覆盖指定天数范围。
        """
        from datetime import datetime, timedelta

        all_notices = self.fetch_all(pages=pages)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        recent = [n for n in all_notices if n["date"] >= cutoff]
        logger.info(f"最近 {days} 天内共 {len(recent)} 条通知")
        return recent


# 命令行测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    scraper = NoticeScraper()
    notices = scraper.fetch_all(pages=2)

    for n in notices[:15]:
        print(f"[{n['date']}] [{n['source_tab']}] {n['title']}")
        print(f"  → {n['link']}")
        print()

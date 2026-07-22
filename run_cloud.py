"""
云端运行脚本 - 用于 GitHub Actions
特点: 用 JSON 文件替代 SQLite 做去重存储
"""
import json, os, sys, hashlib
from datetime import datetime
from pathlib import Path

import yaml

from scraper import NoticeScraper
from classifier import NoticeClassifier
from wechat_pusher import PushPlusPusher, format_push_message

DATA_FILE = Path("docs/data.json")
SEEN_FILE = Path("docs/seen.json")  # 已推送的 URL hash 列表


def load_seen():
    """加载已推送的 URL hash 集合"""
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen)), encoding="utf-8")


def load_existing():
    """加载现有通知数据"""
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"updated": "", "total": 0, "notices": []}


def save_data(data):
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def hash_url(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def generate_weekly_report(existing_map: dict, pusher):
    """生成周报并推送"""
    from collections import Counter
    from datetime import datetime, timedelta

    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_notices = [n for n in existing_map.values() if n["date"] >= week_ago]
    if not week_notices:
        return

    comp = [n for n in week_notices if n["category"] == "competition"]
    hol = [n for n in week_notices if n["category"] == "holiday"]

    # 子标签统计
    sub_counts = Counter(n.get("sub_label", "其他") for n in week_notices)

    lines = [
        "## 📊 合工大通知周报",
        "",
        f"**{week_ago} ~ {datetime.now().strftime('%m-%d')}**",
        "",
        f"🏆 竞赛通知 **{len(comp)}** 条",
        f"📅 节假日通知 **{len(hol)}** 条",
        f"📌 合计 **{len(week_notices)}** 条",
        "",
    ]

    if sub_counts:
        lines.append("**分类统计**:")
        for tag, count in sub_counts.most_common():
            lines.append(f"- {tag}: {count} 条")
        lines.append("")

    # 本周重点（竞赛类取前3）
    if comp:
        lines.append("**🔥 本周竞赛**:")
        for n in sorted(comp, key=lambda x: x["date"], reverse=True)[:3]:
            sub = n.get("sub_label", "")
            sub_str = f" `{sub}`" if sub else ""
            lines.append(f"- [{n['title']}]({n['link']}){sub_str}")
        lines.append("")

    lines.append("---")
    lines.append("📱 [打开 App](https://xue509.github.io/hfut-notices/)")
    content = "\n".join(lines)

    title = f"📊 合工大通知周报 ({week_ago} ~ {datetime.now().strftime('%m-%d')})"
    pusher.push(title, content)
    print(f"Weekly report sent: {len(week_notices)} notices this week")


def main():
    # Load config
    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Init modules
    scraper = NoticeScraper(timeout=15)
    classifier = NoticeClassifier()
    pp_token = config["pusher"]["pushplus"]["token"]
    pusher = PushPlusPusher(token=pp_token) if pp_token else None

    # Load state
    seen = load_seen()
    existing = load_existing()
    existing_map = {hash_url(n["link"]): n for n in existing["notices"]}

    # Scrape
    print("Scraping...")
    notices = scraper.fetch_all(pages=config.get("scraper", {}).get("pages", 2))
    print(f"Fetched {len(notices)} notices")

    # Classify
    cat = classifier.classify_batch(notices)
    targets = cat["competition"] + cat["holiday"]
    print(f"Classified: {len(cat['competition'])} competition + {len(cat['holiday'])} holiday")

    # Dedup & store new ones
    new_comp, new_hol = [], []
    for n in targets:
        h = hash_url(n["link"])
        if h not in existing_map:
            existing_map[h] = n
        if h not in seen:
            seen.add(h)
            n["pushed"] = False
            if n["category"] == "competition":
                new_comp.append(n)
            else:
                new_hol.append(n)

    print(f"New: {len(new_comp)} competition, {len(new_hol)} holiday")

    # Fetch article summaries for new notices
    all_new = new_comp + new_hol
    if all_new:
        print(f"Fetching summaries for {len(all_new)} new notices...")
        for n in all_new[:6]:  # 最多抓6条摘要，避免超时
            summary = scraper.fetch_article_summary(n["link"])
            if summary:
                n["summary"] = summary
                print(f"  summary: {n['title'][:30]}... -> {len(summary)} chars")

    # Push
    if pusher and pp_token != "your_pushplus_token_here":
        for cat_name, notices_list in [("competition", new_comp), ("holiday", new_hol)]:
            if notices_list:
                msg = format_push_message(cat_name, notices_list)
                success = pusher.push(msg["title"], msg["content"])
                print(f"Push {cat_name}: {'OK' if success else 'FAIL'}")

    # Weekly report (every Monday UTC / 周一北京时间)
    if datetime.now().weekday() == 0 and pusher:
        generate_weekly_report(existing_map, pusher)
    else:
        print("PushPlus not configured, skipping push")

    # Save all data
    all_notices = list(existing_map.values())
    all_notices.sort(key=lambda x: x["date"], reverse=True)
    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(all_notices),
        "notices": all_notices,
    }
    save_data(output)
    save_seen(seen)
    print(f"Saved {len(all_notices)} notices to {DATA_FILE}")
    print("Done!")


if __name__ == "__main__":
    main()

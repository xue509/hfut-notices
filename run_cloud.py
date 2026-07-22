"""
云端运行脚本 - 用于 GitHub Actions
零依赖配置：全部从环境变量读取，不需要 config.yaml
"""
import json, os, sys, hashlib
from datetime import datetime
from pathlib import Path

from scraper import NoticeScraper
from classifier import NoticeClassifier
from wechat_pusher import PushPlusPusher, format_push_message

DATA_FILE = Path("docs/data.json")
SEEN_FILE = Path("docs/seen.json")


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen)), encoding="utf-8")


def load_existing():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"updated": "", "total": 0, "notices": []}


def save_data(data):
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def hash_url(url):
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def generate_weekly_report(existing_map, pusher):
    from collections import Counter
    from datetime import timedelta

    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_notices = [n for n in existing_map.values() if n["date"] >= week_ago]
    if not week_notices:
        return

    comp = [n for n in week_notices if n["category"] == "competition"]
    hol = [n for n in week_notices if n["category"] == "holiday"]
    sub_counts = Counter(n.get("sub_label", "") for n in week_notices)

    lines = [
        "## 合工大通知周报",
        f"**{week_ago} ~ {datetime.now().strftime('%m-%d')}**",
        f"竞赛 {len(comp)} 条 | 节假日 {len(hol)} 条 | 合计 {len(week_notices)} 条",
        "",
    ]
    if sub_counts:
        lines.append("**分类统计**:")
        for tag, count in sub_counts.most_common():
            if tag:
                lines.append(f"- {tag}: {count} 条")

    if comp:
        lines.append("")
        lines.append("**本周竞赛 TOP3**:")
        for n in sorted(comp, key=lambda x: x["date"], reverse=True)[:3]:
            lines.append(f"- {n['title']}")

    content = "\n".join(lines)
    title = f"合工大通知周报 ({week_ago})"
    pusher.push(title, content)
    print(f"Weekly report sent: {len(week_notices)} notices")


def main():
    # 全部配置从环境变量读取
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    if not token:
        print("ERROR: PUSHPLUS_TOKEN not set")
        sys.exit(1)

    scraper = NoticeScraper(timeout=15)
    classifier = NoticeClassifier()
    pusher = PushPlusPusher(token=token)

    # Load state
    seen = load_seen()
    existing = load_existing()
    existing_map = {hash_url(n["link"]): n for n in existing["notices"]}

    # Scrape
    print("Scraping...")
    notices = scraper.fetch_all(pages=2)
    print(f"Fetched {len(notices)} notices")

    # Classify
    cat = classifier.classify_batch(notices)
    targets = cat["competition"] + cat["holiday"]
    print(f"Classified: {len(cat['competition'])} competition + {len(cat['holiday'])} holiday")

    # Dedup & store
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

    # Fetch summaries
    all_new = new_comp + new_hol
    if all_new:
        print(f"Fetching summaries for {len(all_new)} new notices...")
        for n in all_new[:6]:
            summary = scraper.fetch_article_summary(n["link"])
            if summary:
                n["summary"] = summary
                print(f"  OK: {n['title'][:30]}...")

    # Push
    for cat_name, notices_list in [("competition", new_comp), ("holiday", new_hol)]:
        if notices_list:
            msg = format_push_message(cat_name, notices_list)
            success = pusher.push(msg["title"], msg["content"])
            print(f"Push {cat_name}: {'OK' if success else 'FAIL'}")

    # Weekly report (Monday)
    if datetime.now().weekday() == 0:
        generate_weekly_report(existing_map, pusher)

    # Save
    all_notices = list(existing_map.values())
    all_notices.sort(key=lambda x: x["date"], reverse=True)
    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(all_notices),
        "notices": all_notices,
    }
    save_data(output)
    save_seen(seen)
    print(f"Saved {len(all_notices)} notices. Done!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)

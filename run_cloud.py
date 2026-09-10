"""
云端运行脚本 - 用于 GitHub Actions
零依赖配置：全部从环境变量读取，不需要 config.yaml
"""
import json, os, sys, hashlib
from datetime import datetime
from pathlib import Path

from scraper import NoticeScraper
from classifier import NoticeClassifier, CATEGORIES, CATEGORY_ORDER, PUSH_CATEGORIES
from wechat_pusher import PushPlusPusher, format_push_message

DATA_FILE = Path("docs/data.json")
SEEN_FILE = Path("docs/seen.json")

# 每轮最多补抓多少条正文摘要（顺带控制 GitHub Actions 的运行时长）
SUMMARY_BUDGET = 30


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
    sub_counts = Counter(n.get("sub_label", "") for n in week_notices if n.get("sub_label"))

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

    # Classify  —— 全部类别都入库（App 要展示），但只有 PUSH_CATEGORIES 推微信
    cat = classifier.classify_batch(notices)
    print("Classified: " + ", ".join(
        f"{k}={len(cat[k])}" for k in CATEGORY_ORDER if cat[k]))

    # Dedup & store
    new_by_cat = {k: [] for k in CATEGORIES}
    for n in notices:
        h = hash_url(n["link"])
        if h not in existing_map:
            existing_map[h] = n
        if h not in seen:
            seen.add(h)
            n["pushed"] = False
            new_by_cat[n["category"]].append(n)

    new_push = {k: v for k, v in new_by_cat.items() if k in PUSH_CATEGORIES}
    print("New: " + ", ".join(f"{k}={len(v)}" for k, v in new_by_cat.items() if v))

    # ---- 正文摘要 ----
    # 新通知全部要简介；老通知里缺简介的也顺带补上（每轮有预算上限）
    need_summary = [n for n in new_by_cat.values() for n in n]
    missing = [n for n in existing_map.values() if not n.get("summary")]
    backfill = [n for n in missing if n not in need_summary]

    queue = need_summary + backfill
    if len(queue) > SUMMARY_BUDGET:
        print(f"Summary queue {len(queue)} > budget {SUMMARY_BUDGET}, "
              f"deferring {len(queue) - SUMMARY_BUDGET}")
        queue = queue[:SUMMARY_BUDGET]

    if queue:
        print(f"Fetching summaries for {len(queue)} notices "
              f"({len(need_summary)} new + backfill)...")
        got = 0
        for n in queue:
            summary = scraper.fetch_article_summary(n["link"])
            if summary:
                n["summary"] = summary
                got += 1
        print(f"  got {got}/{len(queue)} summaries")

    # ---- 推送（只推 PUSH_CATEGORIES） ----
    for cat_name in PUSH_CATEGORIES:
        notices_list = new_push.get(cat_name) or []
        if notices_list:
            msg = format_push_message(cat_name, notices_list)
            success = pusher.push(msg["title"], msg["content"])
            print(f"Push {cat_name}: {'OK' if success else 'FAIL'}")

    # ---- 保存 ----
    all_notices = list(existing_map.values())
    # 丢掉老的空摘要键，避免 data.json 里一堆 "summary": ""
    all_notices = [n for n in all_notices if n]
    all_notices.sort(key=lambda x: x["date"], reverse=True)

    summary_cov = sum(1 for n in all_notices if n.get("summary"))
    output = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(all_notices),
        "summary_coverage": f"{summary_cov}/{len(all_notices)}",
        "categories": {
            k: {"label": v["label"], "emoji": v["emoji"],
                "color": v["color"], "color_dk": v["color_dk"]}
            for k, v in CATEGORIES.items()
        },
        "push_categories": PUSH_CATEGORIES,
        "notices": all_notices,
    }
    save_data(output)
    save_seen(seen)
    print(f"Saved {len(all_notices)} notices ({summary_cov} with summary). Done!")

    # ---- 周报（周一） ----
    # 放在保存之后，这样刚抓到的通知也算进本周
    if datetime.now().weekday() == 0:
        generate_weekly_report(existing_map, pusher)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)

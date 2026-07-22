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

    # Push
    if pusher and pp_token != "your_pushplus_token_here":
        for cat_name, notices_list in [("competition", new_comp), ("holiday", new_hol)]:
            if notices_list:
                msg = format_push_message(cat_name, notices_list)
                success = pusher.push(msg["title"], msg["content"])
                print(f"Push {cat_name}: {'OK' if success else 'FAIL'}")
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

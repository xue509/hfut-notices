"""
合工大通知 API 服务 - Flask 后端
部署到 PythonAnywhere 免费托管，24/7 运行
"""

import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

import yaml
from flask import Flask, jsonify, request
from flask_cors import CORS

from scraper import NoticeScraper
from classifier import NoticeClassifier
from storage import NoticeStorage
from wechat_pusher import NoticePusher

# ---- 初始化 ----
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['DEBUG'] = True
CORS(app)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent

# 加载配置
with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# 初始化各个模块（全局单例）
scraper_cfg = config.get("scraper", {})
scraper = NoticeScraper(
    timeout=scraper_cfg.get("timeout", 15),
    user_agent=scraper_cfg.get("user_agent"),
)

classifier_cfg = config.get("classifier", {})
classifier = NoticeClassifier(
    competition_keywords=classifier_cfg.get("competition_keywords"),
    holiday_keywords=classifier_cfg.get("holiday_keywords"),
)

db_cfg = config.get("database", {})
db_path = db_cfg.get("path", "notices.db")
# API 使用独立数据库，避免和 CLI 冲突
api_db = "notices_api.db" if db_path == "notices.db" else db_path
storage = NoticeStorage(db_path=BASE_DIR / api_db)
logger.info(f"API 数据库: {api_db}")

pusher = NoticePusher(config=config.get("pusher", {}))

# 抓取线程锁，防止并发触发
_scrape_lock = threading.Lock()
_last_scrape = None


# ============================================================
# API 端点
# ============================================================

@app.route("/api/health")
def health():
    return jsonify({
        "code": 0,
        "data": {
            "status": "ok",
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "last_scrape": _last_scrape,
        }
    })


@app.route("/api/stats")
def stats():
    s = storage.get_stats()
    return jsonify({
        "code": 0,
        "data": {
            "total": s["total"],
            "competition": s["competition"],
            "holiday": s["holiday"],
            "pushed": s["pushed"],
            "updated": _last_scrape or "N/A",
        }
    })


@app.route("/api/notices")
def get_notices():
    """获取通知列表，支持筛选、搜索、分页"""
    category = request.args.get("category", "").strip()
    search = request.args.get("search", "").strip()
    page = max(1, int(request.args.get("page", "1")))
    per_page = min(100, max(1, int(request.args.get("per_page", "20"))))
    days = int(request.args.get("days", "60"))

    # 从数据库获取
    all_notices = storage.get_recent(days=days)

    # 只返回 competition 和 holiday
    notices = [
        n for n in all_notices
        if n.get("category") in ("competition", "holiday")
    ]

    # 分类筛选
    if category and category in ("competition", "holiday"):
        notices = [n for n in notices if n["category"] == category]

    # 搜索
    if search:
        q = search.lower()
        notices = [
            n for n in notices
            if q in n["title"].lower()
            or q in (n.get("source_tab", "") or "").lower()
        ]

    # 按日期排序
    notices.sort(key=lambda x: x["date"], reverse=True)

    # 分页
    total = len(notices)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = notices[start:end]

    return jsonify({
        "code": 0,
        "data": {
            "items": page_items,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total else 0,
        }
    })


@app.route("/api/notices/latest")
def latest():
    """获取最新通知（每类各 10 条）"""
    all_notices = storage.get_recent(days=60)

    competition = sorted(
        [n for n in all_notices if n["category"] == "competition"],
        key=lambda x: x["date"], reverse=True
    )[:10]

    holiday = sorted(
        [n for n in all_notices if n["category"] == "holiday"],
        key=lambda x: x["date"], reverse=True
    )[:10]

    return jsonify({
        "code": 0,
        "data": {
            "competition": competition,
            "holiday": holiday,
            "updated": _last_scrape or "N/A",
        }
    })


@app.route("/api/scrape", methods=["POST"])
def scrape():
    """触发一次抓取+分类+推送"""
    global _last_scrape

    if _scrape_lock.locked():
        return jsonify({"code": 1, "message": "抓取正在进行中，请稍后再试"}), 409

    with _scrape_lock:
        logger.info("=" * 40)
        logger.info("触发抓取...")

        # 抓取
        pages = scraper_cfg.get("pages", 2)
        notices = scraper.fetch_all(pages=pages)
        if not notices:
            return jsonify({"code": 1, "message": "抓取失败，网络错误"}), 500

        # 分类
        categorized = classifier.classify_batch(notices)
        competition = categorized["competition"]
        holiday = categorized["holiday"]

        # 去重存储
        new_comp, new_hol = [], []
        for n in competition:
            if storage.is_new(n):
                storage.save(n)
                new_comp.append(n)
        for n in holiday:
            if storage.is_new(n):
                storage.save(n)
                new_hol.append(n)

        # 推送
        pushed_comp = pushed_hol = False
        if new_comp:
            r = pusher.push_category("competition", new_comp)
            pushed_comp = r.get("pushplus", False)
            for n in new_comp:
                storage.mark_pushed(n, push_method="api")
        if new_hol:
            r = pusher.push_category("holiday", new_hol)
            pushed_hol = r.get("pushplus", False)
            for n in new_hol:
                storage.mark_pushed(n, push_method="api")

        _last_scrape = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        logger.info(f"抓取完成: {len(notices)}条 → 竞赛{len(competition)} 节假日{len(holiday)} → 新{len(new_comp)+len(new_hol)}条推送")

        return jsonify({
            "code": 0,
            "data": {
                "total": len(notices),
                "competition": len(competition),
                "holiday": len(holiday),
                "new_competition": len(new_comp),
                "new_holiday": len(new_hol),
                "pushed_competition": pushed_comp,
                "pushed_holiday": pushed_hol,
                "time": _last_scrape,
            }
        })


@app.route("/api/test-push", methods=["POST"])
def test_push():
    """测试推送是否正常"""
    test_notices = [{
        "title": "【测试消息】合工大通知监控系统运行正常",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "link": "https://news.hfut.edu.cn/tzgg2.htm",
        "source_tab": "系统测试",
        "category": "competition",
    }]
    r = pusher.push_category("competition", test_notices)
    return jsonify({"code": 0, "message": "测试推送已发送", "result": r})


# ============================================================
# 启动
# ============================================================

if __name__ == "__main__":
    # 首次启动时自动执行一次抓取
    logger.info("首次启动，执行初始抓取...")
    try:
        with _scrape_lock:
            notices = scraper.fetch_all(pages=2)
            if notices:
                categorized = classifier.classify_batch(notices)
                for n in categorized["competition"] + categorized["holiday"]:
                    storage.save(n)
                _last_scrape = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                logger.info(f"初始抓取完成: {len(notices)} 条")
    except Exception as e:
        logger.error(f"初始抓取失败: {e}")

    app.run(host="0.0.0.0", port=5000, debug=False)

#!/usr/bin/env python3
"""
合工大新闻网通知公告监控系统 - 主程序

功能:
  1. 定时/手动抓取通知公告页面
  2. 智能分类: 竞赛通知 / 节假日通知
  3. 微信推送: PushPlus / 微信测试号
  4. SQLite 去重, 避免重复推送

使用方法:
  python main.py              # 运行一次检查并推送
  python main.py --dry-run    # 仅抓取和分类, 不推送
  python main.py --stats      # 显示数据库统计
  python main.py --recent 7   # 显示最近7天的通知
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

from scraper import NoticeScraper
from classifier import NoticeClassifier, CATEGORIES, CATEGORY_ORDER, PUSH_CATEGORIES
from storage import NoticeStorage
from wechat_pusher import NoticePusher

# ---- 修复 Windows 控制台 GBK 编码无法输出 emoji 的问题 ----
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    path = Path(config_path)
    if not path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        print("   请复制 config.yaml 并根据注释填写配置")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    """配置日志"""
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO"), logging.INFO)
    log_file = log_config.get("file", "monitor.log")

    # 控制台 handler: 强制 UTF-8
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    # 对 Windows 控制台做编码处理
    if sys.platform == "win32":
        try:
            console_handler.stream = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
        except Exception:
            pass

    file_handler = logging.FileHandler(log_file, encoding="utf-8")

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[console_handler, file_handler],
    )


def export_app_data(storage: NoticeStorage, config: dict):
    """
    将所有类别的通知导出为 docs/data.json，供手机 Web App 使用。

    导出全部类别（App 要能分类浏览），同时带上类别元数据，
    前端就不用再硬编码一份分类表。
    """
    logger = logging.getLogger(__name__)

    # 获取所有已存储的通知（最近 60 天）
    export_notices = storage.get_recent(days=60)

    if not export_notices:
        logger.warning("没有需要导出的通知数据")
        return

    # 确保 docs 目录存在（GitHub Pages 要求）
    app_dir = Path("docs")
    app_dir.mkdir(exist_ok=True)

    # 写入 data.json
    data = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(export_notices),
        "summary_coverage": f"{sum(1 for n in export_notices if n.get('summary'))}/{len(export_notices)}",
        "categories": {
            k: {"label": v["label"], "emoji": v["emoji"],
                "color": v["color"], "color_dk": v["color_dk"]}
            for k, v in CATEGORIES.items()
        },
        "push_categories": PUSH_CATEGORIES,
        "notices": export_notices,
    }

    json_path = app_dir / "data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"App 数据已导出: {json_path} ({len(export_notices)} 条通知)")
    return json_path


def git_upload(config: dict):
    """
    将 app/ 目录自动 commit + push 到 GitHub。

    前提条件:
      1. 项目目录已 git init 并关联了 GitHub 远程仓库
      2. git 已配置用户信息
    """
    logger = logging.getLogger(__name__)
    git_cfg = config.get("github", {})
    remote_url = git_cfg.get("remote_url", "")

    # 检查 git 是否可用
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("Git 未安装或不可用，跳过 GitHub 上传")
        return False

    # 检查是否是 git 仓库
    git_dir = Path(".git")
    if not git_dir.exists():
        logger.info("项目尚未初始化 Git 仓库，正在初始化...")
        subprocess.run(["git", "init"], capture_output=True)
        if remote_url:
            subprocess.run(
                ["git", "remote", "add", "origin", remote_url],
                capture_output=True,
            )
        else:
            logger.warning("未配置 github.remote_url，请手动设置远程仓库")
            logger.warning(
                "  命令: git remote add origin https://github.com/用户名/仓库名.git"
            )
            return False

    # 如果还没有 remote
    if remote_url:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            subprocess.run(
                ["git", "remote", "add", "origin", remote_url],
                capture_output=True,
            )
            logger.info(f"已添加远程仓库: {remote_url}")

    # 配置 git 用户（如果未配置）
    user_name = git_cfg.get("user_name", "HFUT Notice Monitor")
    user_email = git_cfg.get("user_email", "hfut-notice@example.com")
    subprocess.run(["git", "config", "user.name", user_name], capture_output=True)
    subprocess.run(["git", "config", "user.email", user_email], capture_output=True)

    # 添加 docs/ 目录
    subprocess.run(["git", "add", "docs/"], capture_output=True)

    # 检查是否有变更
    status = subprocess.run(
        ["git", "status", "--porcelain", "docs/"],
        capture_output=True, text=True,
    )
    if not status.stdout.strip():
        logger.info("App 数据无变更，跳过上传")
        return True

    # Commit
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"Update notices - {now}"
    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning(f"Git commit 失败: {result.stderr}")
        return False

    # Push
    try:
        result = subprocess.run(
            ["git", "push", "-u", "origin", "master"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("GitHub 上传成功!")
            return True
        else:
            # 尝试 main 分支
            result2 = subprocess.run(
                ["git", "push", "-u", "origin", "main"],
                capture_output=True, text=True, timeout=30,
            )
            if result2.returncode == 0:
                logger.info("GitHub 上传成功! (main 分支)")
                return True
            logger.warning(f"Git push 失败: {result2.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning("Git push 超时")
        return False


def cmd_run(config: dict, dry_run: bool = False):
    """运行一次：抓取 → 分类 → 推送"""
    logger = logging.getLogger(__name__)

    # ---- 1. 初始化各模块 ----
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
    storage = NoticeStorage(db_path=db_cfg.get("path", "notices.db"))

    pusher = NoticePusher(config=config.get("pusher", {}))

    # ---- 2. 抓取通知 ----
    logger.info("=" * 50)
    logger.info("🚀 开始抓取合工大通知公告...")
    pages = scraper_cfg.get("pages", 2)
    notices = scraper.fetch_all(pages=pages)

    if not notices:
        logger.warning("未抓取到任何通知（可能是网络问题）")
        storage.close()
        return

    # ---- 3. 分类 ----
    categorized = classifier.classify_batch(notices)

    # 显示分类结果
    print()
    print("=" * 60)
    print(f"  📊 抓取结果: 共 {len(notices)} 条通知")
    for key in CATEGORY_ORDER:
        items = categorized[key]
        if not items:
            continue
        meta = CATEGORIES[key]
        mark = "→推送" if key in PUSH_CATEGORIES else "     "
        print(f"     {meta['emoji']} {meta['label']}: {len(items)} 条 {mark}")
    print("=" * 60)
    print()

    # ---- 4. 去重 & 存储 ----
    # 全部类别都入库（App 要能分类浏览），只有 PUSH_CATEGORIES 会推微信
    new_by_cat = {key: [] for key in CATEGORY_ORDER}
    for key in CATEGORY_ORDER:
        for notice in categorized[key]:
            if storage.is_new(notice):
                storage.save(notice)
                new_by_cat[key].append(notice)

    logger.info("去重结果: " + ", ".join(
        f"{CATEGORIES[k]['label']} {len(new_by_cat[k])}/{len(categorized[k])}"
        for k in CATEGORY_ORDER if categorized[k]
    ))

    # ---- 4b. 正文简介 ----
    # 新通知抓正文摘要；库里缺摘要的老通知顺带补上（有预算上限）
    existing = storage.get_recent(days=60)
    missing = [n for n in existing if not n.get("summary")]
    queue = [n for k in PUSH_CATEGORIES for n in new_by_cat[k]] + missing
    queue = queue[:30]
    if queue:
        logger.info(f"📝 抓取正文简介: {len(queue)} 条...")
        got = 0
        for n in queue:
            text = scraper.fetch_article_summary(n["link"])
            if text:
                n["summary"] = text
                got += 1
        logger.info(f"   成功 {got}/{len(queue)} 条")
        storage.update_summaries(queue)

    # ---- 5. 显示新通知 ----
    for key in CATEGORY_ORDER:
        items = new_by_cat[key]
        if not items:
            continue
        meta = CATEGORIES[key]
        suffix = "（将推送）" if key in PUSH_CATEGORIES else "（仅 App）"
        print(f"{meta['emoji']} ==== 新{meta['label']} {suffix} ====")
        for i, n in enumerate(items, 1):
            print(f"  {i}. [{n['date']}] {n['title']}")
            print(f"     🔗 {n['link']}")
        print()

    if not any(new_by_cat.values()):
        print("✅ 没有新通知。")
        print()

    # ---- 6. 推送（只推 PUSH_CATEGORIES） ----
    if dry_run:
        logger.info("🔍 Dry-run 模式，跳过推送")
    else:
        for key in PUSH_CATEGORIES:
            items = new_by_cat[key]
            if not items:
                continue
            logger.info(f"📤 推送{CATEGORIES[key]['label']}...")
            result = pusher.push_category(key, items)
            if result["pushplus"] or result["wechat_test"] > 0:
                for notice in items:
                    storage.mark_pushed(notice, push_method=pusher.mode)
            print()

    # ---- 7. 导出 App 数据 ----
    logger.info("📱 导出手机 App 数据...")
    export_app_data(storage, config)

    # ---- 8. 上传 GitHub Pages ----
    if not dry_run:
        git_upload(config)

    # ---- 9. 收尾 ----
    storage.close()
    logger.info("✅ 本轮检查完成")


def cmd_stats(config: dict):
    """显示数据库统计"""
    db_cfg = config.get("database", {})
    storage = NoticeStorage(db_path=db_cfg.get("path", "notices.db"))
    stats = storage.get_stats()

    print()
    print("=" * 40)
    print("  📊 数据库统计")
    print("=" * 40)
    print(f"  总记录数:     {stats['total']}")
    print(f"  竞赛通知:     {stats['competition']}")
    print(f"  节假日通知:   {stats['holiday']}")
    print(f"  已推送:       {stats['pushed']}")
    print(f"  待推送:       {stats['unpushed']}")
    print("=" * 40)
    print()

    storage.close()


def cmd_recent(config: dict, days: int = 7, category: str = None):
    """显示最近的记录"""
    db_cfg = config.get("database", {})
    storage = NoticeStorage(db_path=db_cfg.get("path", "notices.db"))
    notices = storage.get_recent(days=days, category=category)

    print()
    print(f"📋 最近 {days} 天的通知 ({len(notices)} 条):")
    print("-" * 60)

    for n in notices:
        push_status = "✅已推送" if n.get("pushed") else "⏳待推送"
        emoji = {"competition": "🏆", "holiday": "📅"}.get(n.get("category", ""), "📌")
        print(f"  {emoji} [{n['date']}] {n['title'][:50]}...  {push_status}")

    print("-" * 60)
    print()

    storage.close()


def main():
    parser = argparse.ArgumentParser(
        description="合工大新闻网通知公告监控系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                  # 运行一次检查并推送
  python main.py --dry-run        # 仅抓取和分类，不推送
  python main.py --stats          # 显示数据库统计
  python main.py --recent 7       # 显示最近7天的记录
  python main.py --config my.yaml # 使用自定义配置文件
        """,
    )

    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅抓取和分类，不执行推送",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="显示数据库统计信息",
    )
    parser.add_argument(
        "--recent",
        type=int,
        metavar="DAYS",
        help="显示最近N天的通知记录",
    )

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)
    setup_logging(config)

    # 执行命令
    if args.stats:
        cmd_stats(config)
    elif args.recent:
        cmd_recent(config, days=args.recent)
    else:
        cmd_run(config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

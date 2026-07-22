"""
合工大通知公告 - 智能分类模块

根据关键词匹配将通知分为：
  - competition: 竞赛通知（大赛、挑战杯、选拔赛等）
  - holiday:     节假日通知（放假、暑假、调休等）
  - other:       其他通知（不推送）
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class NoticeClassifier:
    """通知分类器"""

    # 竞赛关键词 — 标题含任一即判定为竞赛类
    DEFAULT_COMPETITION_KEYWORDS = [
        "大赛", "竞赛", "挑战杯", "创新创业", "选拔赛",
        "互联网+", "比赛", "全国大学生", "中国大学生",
        "学科竞赛", "创新大赛", "技能大赛", "专项赛",
    ]

    # 节假日关键词
    DEFAULT_HOLIDAY_KEYWORDS = [
        "放假", "假期", "暑期", "寒假", "暑假", "节假日",
        "调休", "清明", "五一", "端午", "中秋", "国庆",
        "元旦", "春节", "劳动节", "开学", "返校",
        "报到注册", "校历",
    ]

    def __init__(
        self,
        competition_keywords: Optional[list[str]] = None,
        holiday_keywords: Optional[list[str]] = None,
    ):
        self.competition_kw = competition_keywords or self.DEFAULT_COMPETITION_KEYWORDS
        self.holiday_kw = holiday_keywords or self.DEFAULT_HOLIDAY_KEYWORDS

    def classify(self, notice: dict) -> str:
        """
        对单条通知进行分类。

        参数:
            notice: {"title": "标题", "date": "...", "link": "...", ...}

        返回:
            "competition" | "holiday" | "other"
        """
        title = notice.get("title", "")

        # 先检查竞赛（竞赛标题通常很明确）
        if self._match_keywords(title, self.competition_kw):
            logger.debug(f"[竞赛] {title}")
            return "competition"

        # 再检查节假日
        if self._match_keywords(title, self.holiday_kw):
            logger.debug(f"[节假日] {title}")
            return "holiday"

        return "other"

    def classify_batch(self, notices: list[dict]) -> dict[str, list[dict]]:
        """
        批量分类。

        返回:
            {"competition": [...], "holiday": [...], "other": [...]}
        """
        result = {"competition": [], "holiday": [], "other": []}

        for notice in notices:
            category = self.classify(notice)
            notice["category"] = category
            result[category].append(notice)

        total = len(notices)
        comp_count = len(result["competition"])
        hol_count = len(result["holiday"])

        logger.info(
            f"分类完成: 共 {total} 条 → "
            f"竞赛 {comp_count} 条, 节假日 {hol_count} 条, "
            f"其他 {len(result['other'])} 条"
        )
        return result

    @staticmethod
    def _match_keywords(title: str, keywords: list[str]) -> bool:
        """检查标题是否包含任意关键词"""
        return any(kw in title for kw in keywords)

    @staticmethod
    def get_priority(category: str) -> int:
        """获取分类优先级 (数字越小越优先)"""
        return {"competition": 1, "holiday": 2}.get(category, 99)

    @staticmethod
    def get_emoji(category: str) -> str:
        """获取分类对应的 emoji"""
        return {
            "competition": "🏆",
            "holiday": "📅",
            "other": "📌",
        }.get(category, "📌")

    @staticmethod
    def get_label(category: str) -> str:
        """获取分类中文标签"""
        return {
            "competition": "竞赛通知",
            "holiday": "节假日通知",
            "other": "其他通知",
        }.get(category, "未知")


# 命令行测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    classifier = NoticeClassifier()

    test_cases = [
        "关于举办2026新域新质创新大赛校内选拔赛的通知",
        "关于举办第二届合肥工业大学全球校友创新创业大赛的通知",
        "关于组织参加第十五届挑战杯中国大学生创业计划竞赛的通知",
        "2026年合肥工业大学医院暑期值班表",
        "关于屯溪路校区聚英路第一阶段全封闭施工期间道路临时绕行的通知",
        "关于做好2026年暑期实验室安全管理工作的通知",
        "关于开展2027版本科专业人才培养方案修订调研工作的通知",
        "关于2026年清明节放假安排的通知",
        "关于五一劳动节放假及调课安排的通知",
    ]

    for title in test_cases:
        notice = {"title": title}
        cat = classifier.classify(notice)
        emoji = classifier.get_emoji(cat)
        label = classifier.get_label(cat)
        print(f"{emoji} [{label}] {title}")

"""
合工大通知公告 - 智能分类模块 (v2)

分类层级:
  大类       子标签
  ─────────────────────
  competition 学科竞赛 / 创新创业 / 课题申报 / 讲座报告
  holiday     放假通知 / 假期安排 / 开学返校
  other       (不推送)

排除机制: 节假日分类会排除含"值班/浴室/安全/施工/VPN/实验室"的误匹配
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class NoticeClassifier:
    """通知分类器 (v2: 子标签 + 排除词)"""

    # ====== 竞赛: 关键词 → 子标签 ======
    COMPETITION_RULES = [
        # (关键词列表, 子标签)
        (["挑战杯", "互联网+", "中国大学生", "全国大学生"], "学科竞赛"),
        (["学科竞赛", "技能大赛", "专项赛"], "学科竞赛"),
        (["创新创业", "创业计划", "创业大赛"], "创新创业"),
        (["创新大赛", "选拔赛", "大赛", "竞赛"], "学科竞赛"),
        (["申报指南", "申报通知", "项目指南", "课题申报"], "课题申报"),
        (["讲座", "报告", "学术报告"], "讲座报告"),
        (["比赛"], "学科竞赛"),
    ]

    # ====== 节假日: 关键词 → 子标签 ======
    HOLIDAY_RULES = [
        (["放假", "调休", "节假日"], "放假通知"),
        (["清明", "五一", "端午", "中秋", "国庆", "元旦", "春节", "劳动节"], "放假通知"),
        (["假期", "暑期", "寒假", "暑假", "校历"], "假期安排"),
        (["开学", "返校", "报到注册"], "开学返校"),
    ]

    # ====== 节假日排除词 ======
    # 标题含这些词时，即使匹配到节假日关键词也不归为节假日
    HOLIDAY_EXCLUDE = [
        "值班", "浴室", "安全", "施工", "绕行", "VPN",
        "实验室", "心理关爱", "医院", "封闭",
    ]

    def __init__(
        self,
        competition_keywords: Optional[list[str]] = None,
        holiday_keywords: Optional[list[str]] = None,
    ):
        # 保留兼容旧接口
        pass

    def classify(self, notice: dict) -> str:
        """
        分类返回: "competition" | "holiday" | "other"
        同时设置 notice["sub_label"]
        """
        title = notice.get("title", "")

        # 1. 竞赛检测
        for keywords, sub_label in self.COMPETITION_RULES:
            if self._match_any(title, keywords):
                notice["sub_label"] = sub_label
                logger.debug(f"[竞赛/{sub_label}] {title}")
                return "competition"

        # 2. 节假日检测（需通过排除检查）
        if self._is_holiday(notice):
            logger.debug(f"[节假日/{notice.get('sub_label','')}] {title}")
            return "holiday"

        return "other"

    def _is_holiday(self, notice: dict) -> bool:
        """检查是否为节假日通知（含排除逻辑），同时设置 sub_label"""
        title = notice.get("title", "")

        # 先检查排除词
        if self._match_any(title, self.HOLIDAY_EXCLUDE):
            return False

        # 检查节假日关键词并设置子标签
        for keywords, sub_label in self.HOLIDAY_RULES:
            if self._match_any(title, keywords):
                # 二次确认: 排除"暑期值班"这类
                if not self._is_false_positive(title):
                    notice["sub_label"] = sub_label
                    return True
        return False

    def _is_false_positive(self, title: str) -> bool:
        """检查节假日误匹配"""
        false_patterns = [
            r'值班', r'施工', r'浴室', r'医院.*值班',
            r'安全.*管理', r'安全.*检查', r'安全.*通知',
            r'VPN', r'绕行', r'封闭.*施工',
        ]
        for pat in false_patterns:
            if re.search(pat, title):
                logger.debug(f"[排除误匹配] {title} -> 匹配模式: {pat}")
                return True
        return False

    def classify_batch(self, notices: list[dict]) -> dict[str, list[dict]]:
        """
        批量分类。

        返回: {"competition": [...], "holiday": [...], "other": [...]}
        每条 notice 同时被设置 category 和 sub_label
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
    def _match_any(title: str, keywords: list[str]) -> bool:
        """检查标题是否包含任意关键词"""
        return any(kw in title for kw in keywords)

    # ====== 工具方法 ======

    @staticmethod
    def get_emoji(category: str) -> str:
        return {"competition": "🏆", "holiday": "📅", "other": "📌"}.get(category, "📌")

    @staticmethod
    def get_label(category: str) -> str:
        return {
            "competition": "竞赛通知",
            "holiday": "节假日通知",
            "other": "其他通知",
        }.get(category, "未知")

    @staticmethod
    def get_sub_emoji(sub_label: str) -> str:
        return {
            "学科竞赛": "🏅", "创新创业": "💡", "课题申报": "📋",
            "讲座报告": "🎙️", "放假通知": "🎉", "假期安排": "📆",
            "开学返校": "🏫",
        }.get(sub_label, "")

    @staticmethod
    def get_all_sub_labels(category: str) -> list[str]:
        if category == "competition":
            return ["学科竞赛", "创新创业", "课题申报", "讲座报告"]
        elif category == "holiday":
            return ["放假通知", "假期安排", "开学返校"]
        return []


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    c = NoticeClassifier()

    tests = [
        # 竞赛类
        "关于举办2026新域新质创新大赛校内选拔赛的通知",
        "关于举办第二届全球校友创新创业大赛的通知",
        "关于组织参加第十五届挑战杯创业计划竞赛的通知",
        "国家科技重大专项：煤炭重大专项2027年度公开项目申报指南",
        "关于举办安徽省第二届科普辅导员职业技能大赛的通知",
        "关于举办学术报告会的通知",
        # 节假日类（正确）
        "关于2026年清明节放假安排的通知",
        "关于五一劳动节放假及调课安排的通知",
        "关于2026年暑期有关事项的通知",
        "关于2026年寒假放假的通知",
        "合肥工业大学2026-2027学年校历",
        # 应排除的假节假日
        "2026年合肥工业大学医院暑期值班表",
        "关于屯溪路校区本科生浴室暑期开放时间安排的通知",
        "关于做好2026年暑期实验室安全管理工作的通知",
        "关于屯溪路校区聚英路施工期间道路临时绕行的通知",
        "关于防范台风的温馨提示",
        # 其他
        "关于开展2027版本科专业人才培养方案修订调研工作的通知",
    ]

    for t in tests:
        n = {"title": t}
        cat = c.classify(n)
        sub = n.get("sub_label", "")
        em = c.get_emoji(cat)
        lb = c.get_label(cat)
        print(f"{em} [{lb}/{sub}] {t}")

    print("\n--- 子标签列表 ---")
    print("竞赛:", c.get_all_sub_labels("competition"))
    print("节假日:", c.get_all_sub_labels("holiday"))

"""
合工大通知公告 - 智能分类模块 (v3)

分类层级:
  大类       子标签
  ─────────────────────
  competition 学科竞赛 / 创新创业 / 课题申报 / 讲座报告
  holiday     放假通知 / 假期安排 / 开学返校
  other       (不推送)

v3 改进:
  - 节假日排除词加强（心理、食堂、体检）
  - 课题申报匹配更广（项目指南、指南、申报）
  - 讲座报告排除「体检报告」
  - 竞赛默认子标签不再留空
"""

import logging, re
from typing import Optional

logger = logging.getLogger(__name__)


class NoticeClassifier:

    # ====== 竞赛: 关键词 → 子标签 ======
    # 按优先级排列，先匹配的先生效
    COMPETITION_RULES = [
        (["挑战杯", "互联网+", "中国大学生", "全国大学生", "省大学生"], "学科竞赛"),
        (["创新创业", "创业计划", "创业大赛", "创业竞赛"], "创新创业"),
        (["创新大赛", "选拔赛"], "学科竞赛"),
        (["学科竞赛", "技能大赛", "专项赛", "科普辅导员", "金相技能大赛",
          "机械工程创新", "化学实验创新", "大学生物理实验", "信息安全竞赛",
          "电子设计竞赛", "数学建模", "英语竞赛", "机器人"], "学科竞赛"),
        (["项目指南", "申报指南", "申报通知", "课题申报", "项目申报",
          "项目指", "课题指", "科学基金", "科技重大专项",
          "项目建议", "建议书", "预通知.*项目", "项目.*预通知",
          "申报", "项目.*指南"], "课题申报"),
        (["学术报告会", "学术讲座", "学术论坛", "学术交流",
          "报告解读", "专题报告", "辅导报告"], "讲座报告"),
        (["大赛", "竞赛", "比赛"], "学科竞赛"),
    ]

    # ====== 节假日: 关键词 → 子标签 ======
    HOLIDAY_RULES = [
        (["清明", "五一", "端午", "中秋", "国庆", "元旦", "春节", "劳动节"], "放假通知"),
        (["放假", "调休", "节假日"], "放假通知"),
        (["校历"], "假期安排"),
        (["暑期", "寒假", "暑假", "假期"], "假期安排"),
        (["开学", "返校", "报到注册", "新生报到"], "开学返校"),
    ]

    # ====== 节假日排除词（更全） ======
    HOLIDAY_EXCLUDE = [
        # 日常事务类
        "值班", "浴室", "食堂", "饮食", "供餐", "停伙",
        "校园网", "VPN", "网络升级", "信息化",
        "施工", "绕行", "封闭", "交通", "道路",
        "实验室安全", "安全工作", "安全检查", "消防",
        "心理关爱", "心理辅导", "心理服务",
        "医院", "就诊", "体检",
        "台风", "暴雨", "防汛", "防灾",
        "设备检修", "停电", "停水", "维修",
        "招租", "出租", "房产", "铺位",
        "档案", "归档",
        "治安", "保卫",
    ]

    # ====== 讲座报告类的排除词 ======
    # 不含「解读/讲座/论坛」的纯行政报告才排除
    LECTURE_EXCLUDE = [
        "述职报告", "督查报告", "审计报告",
        "调研报告", "财务报告", "工作报告",
        "体检报告归档", "提交体检报告",
    ]

    def __init__(self, competition_keywords=None, holiday_keywords=None):
        pass

    def classify(self, notice: dict) -> str:
        title = notice.get("title", "")

        # ---- 1. 竞赛检测 ----
        for keywords, sub_label in self.COMPETITION_RULES:
            if self._match_any(title, keywords):
                # "讲座报告" 需要做排除检查
                if sub_label == "讲座报告":
                    if self._match_any(title, self.LECTURE_EXCLUDE):
                        continue  # 不是真的讲座，继续往下匹配

                notice["sub_label"] = sub_label
                logger.debug(f"[competition/{sub_label}] {title}")
                return "competition"

        # ---- 2. 节假日检测 ----
        if self._is_holiday(notice):
            logger.debug(f"[holiday/{notice.get('sub_label','')}] {title}")
            return "holiday"

        # ---- 3. 排除所有 keyword match 后的兜底 ----
        return "other"

    def _is_holiday(self, notice: dict) -> bool:
        title = notice.get("title", "")

        # Step A: 强制排除词检查 —— 含这些词的一律不是节假日
        if self._match_any(title, self.HOLIDAY_EXCLUDE):
            logger.debug(f"[holiday-exclude] {title}")
            return False

        # Step B: 关键词匹配 + 子标签
        for keywords, sub_label in self.HOLIDAY_RULES:
            if self._match_any(title, keywords):
                # 二次正则排除
                if self._is_holiday_false_positive(title):
                    logger.debug(f"[holiday-false-positive] {title}")
                    return False
                notice["sub_label"] = sub_label
                return True

        return False

    def _is_holiday_false_positive(self, title: str) -> bool:
        """二次确认节假日误匹配（正则）"""
        patterns = [
            # 暑期 + 非假期事务
            r'暑期.{0,10}(值班|浴室|实验室|心理|施工|安全|VPN|网络|平台|检查|维修)',
            r'(值班|浴室|施工|安全|VPN|心理|实验室).{0,8}暑期',
            # 暑期+日常
            r'暑期.{0,6}(开放|关闭|停|恢复|调整)',
            # 非假期含义的「假期」
            r'(实践|调研|实习|实训).{0,6}假期',
            r'假期.{0,6}(实践|调研|实习|实训)',
        ]
        for pat in patterns:
            if re.search(pat, title):
                return True
        return False

    def classify_batch(self, notices: list[dict]) -> dict[str, list[dict]]:
        result = {"competition": [], "holiday": [], "other": []}
        for notice in notices:
            category = self.classify(notice)
            notice["category"] = category
            result[category].append(notice)

        total = len(notices)
        logger.info(
            f"classify: total={total} comp={len(result['competition'])} "
            f"hol={len(result['holiday'])} other={len(result['other'])}"
        )
        return result

    @staticmethod
    def _match_any(title: str, keywords: list[str]) -> bool:
        return any(kw in title for kw in keywords)

    # ====== Utils ======
    @staticmethod
    def get_emoji(category: str) -> str:
        return {"competition": "🏆", "holiday": "📅", "other": "📌"}.get(category, "📌")

    @staticmethod
    def get_label(category: str) -> str:
        return {"competition": "竞赛通知", "holiday": "节假日通知", "other": "其他通知"}.get(category, "未知")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    c = NoticeClassifier()

    tests = [
        # === 竞赛 ===
        ("关于举办2026新域新质创新大赛校内选拔赛的通知", "competition", "学科竞赛"),
        ("关于举办第二届全球校友创新创业大赛的通知", "competition", "创新创业"),
        ("关于组织参加第十五届挑战杯创业计划竞赛的通知", "competition", "学科竞赛"),
        ("煤炭重大专项2027年度公开项目申报指南", "competition", "课题申报"),
        ("关于发布某预研第二批项目指南需求建议的通知", "competition", "课题申报"),
        ("国家科技重大专项：智能电网专项项目申报指南征求意见", "competition", "课题申报"),
        ("关于举办安徽省第二届科普辅导员职业技能大赛的通知", "competition", "学科竞赛"),
        ("关于举办学术报告会的通知", "competition", "讲座报告"),
        ("关于组织参加2026年安徽省大学生金相技能大赛的通知", "competition", "学科竞赛"),
        ("关于举办屯溪路校区教职工体检报告解读暨健康咨询活动的通知", "competition", "讲座报告"),  # 健康讲座
        ("关于发布JC科研十五五第一批项目建议书深化论证的预通知", "competition", "课题申报"),
        # === 节假日（正确） ===
        ("关于2026年清明节放假安排的通知", "holiday", "放假通知"),
        ("关于五一劳动节放假及调课安排的通知", "holiday", "放假通知"),
        ("关于2026年暑期有关事项的通知", "holiday", "假期安排"),
        ("关于2026年寒假放假的通知", "holiday", "放假通知"),
        ("合肥工业大学2026-2027学年校历", "holiday", "假期安排"),
        ("关于2026年暑期学生放假及秋季学期开学安排的通知", "holiday", "放假通知"),
        ("关于2026年端午节放假的通知", "holiday", "放假通知"),
        # === 应排除的假节假日 ===
        ("2026年合肥工业大学医院（合肥校区）暑期值班表", "other", ""),
        ("关于屯溪路校区本科生浴室暑期开放时间安排的通知", "other", ""),
        ("关于做好2026年暑期实验室安全管理工作的通知", "other", ""),
        ("关于开通2026年暑期学生心理关爱服务线上平台的通知", "other", ""),
        ("关于屯溪路校区聚英路施工期间道路临时绕行的通知", "other", ""),
        ("关于防范台风的温馨提示", "other", ""),
        ("合肥校区2026年暑期食堂停伙、复伙通知", "other", ""),
        # === 其他 ===
        ("关于开展2027版本科专业人才培养方案修订调研工作的通知", "other", ""),
        ("关于发布某预研第二批项目指南需求建议的通知", "competition", "课题申报"),
    ]

    ok = 0
    for title, exp_cat, exp_sub in tests:
        n = {"title": title}
        got = c.classify(n)
        got_sub = n.get("sub_label", "")
        cat_ok = got == exp_cat
        sub_ok = exp_sub == "" or got_sub == exp_sub
        if cat_ok and sub_ok:
            ok += 1
        else:
            print(f"FAIL exp={exp_cat}/{exp_sub} got={got}/{got_sub} | {title[:50]}")

    print(f"\n{ok}/{len(tests)} passed")

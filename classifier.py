"""
合工大通知公告 - 分类模块 (v3)

设计原则: 宁可归到「待分类」，也不要错分。
每条通知只进一个类别，按下面的顺序命中即停:

    排除层   结果公示 / 文体活动  → 不参与「可行动」判断
    ─────────────────────────────────────────────
    1. 竞赛通知   报名、选拔、大赛
    2. 节假日     放假、开学、校历
    3. 科研申报   基金委、重点研发计划、项目指南
    4. 教务教学   选课、考试、学籍、毕业审核
    5. 学工通知   奖助学金、宿舍、评优、新生入学
    6. 后勤服务   停水停电、食堂、浴室、采购
    7. 公示公告   评审结果、名单公示、活动通知
    ─────────────────────────────────────────────
    兜底      其他

关键词一律用「精确锚点」（如「停电」而不是「检修」），
避免像「检修」这种会同时命中后勤和设备的宽词。
"""

import re
import logging

logger = logging.getLogger(__name__)


# ============================================================
# 类别定义（前端直接读 data.json 里的 categories，不要在前端另写一份）
#
#   key      存进通知的 category 字段
#   color    浅色模式下的标签色
#   color_dk 深色模式下的标签色（浅色值在深底上对比度不够）
#   push     是否推送到微信
#
# 两套色值都经 WCAG AA 校验（>= 4.5:1），改色请重新验证:
#   python color_check.py
# ============================================================
CATEGORIES = {
    "competition": {"label": "竞赛通知", "emoji": "🏆", "color": "#C2410C", "color_dk": "#FB923C", "push": True},
    "holiday":     {"label": "节假日",   "emoji": "📅", "color": "#15803D", "color_dk": "#4ADE80", "push": True},
    "research":    {"label": "科研申报", "emoji": "🔬", "color": "#1D4ED8", "color_dk": "#60A5FA", "push": False},
    "academic":    {"label": "教务教学", "emoji": "🎓", "color": "#0F766E", "color_dk": "#2DD4BF", "push": False},
    "student":     {"label": "学工通知", "emoji": "🎯", "color": "#6D28D9", "color_dk": "#A78BFA", "push": False},
    "support":     {"label": "后勤服务", "emoji": "🏠", "color": "#A16207", "color_dk": "#FBBF24", "push": False},
    "notice":      {"label": "公示公告", "emoji": "📢", "color": "#B91C1C", "color_dk": "#F87171", "push": False},
    "other":       {"label": "其他",     "emoji": "📌", "color": "#475569", "color_dk": "#94A3B8", "push": False},
}

# 会推送的类别（改这里就能改推送范围）
PUSH_CATEGORIES = [k for k, v in CATEGORIES.items() if v["push"]]

CATEGORY_ORDER = list(CATEGORIES.keys())


# ============================================================
# 大分组（前端底部导航用）
#
# 8 个细分类对手机底栏来说太多了，按「你会拿它干什么」归成 4 组:
#   学习 —— 跟学业直接相关
#   生活 —— 影响日常起居
#   学工 —— 学生事务办理
#   公告 —— 结果与公示，看的多办的少
#
# 「其他」是兜底桶，塞进「公告」—— 公告栏本来就是什么都贴的地方。
#
# 与 CATEGORIES 一样，这是唯一权威来源，前端从 data.json 读，
# 别在 index.html 里再写一份。
#
# 注意: 每个 category 必须且只能出现在一个组里，
#       漏掉的类别在前端就点不到了（有自检，见 __main__）。
# ============================================================
GROUPS = [
    {"key": "study",  "label": "学习", "emoji": "📚",
     "categories": ["research", "academic", "competition"]},
    {"key": "life",   "label": "生活", "emoji": "🏠",
     "categories": ["support", "holiday"]},
    {"key": "affair", "label": "学工", "emoji": "🎯",
     "categories": ["student"]},
    {"key": "bulletin", "label": "公告", "emoji": "📢",
     "categories": ["notice", "other"]},
]

GROUP_ORDER = [g["key"] for g in GROUPS]


def group_of(category: str) -> str:
    """类别属于哪个大组（找不到返回空串）"""
    for g in GROUPS:
        if category in g["categories"]:
            return g["key"]
    return ""


def _validate_groups():
    """自检: 8 个类别必须不重不漏地分进各组"""
    seen = [c for g in GROUPS for c in g["categories"]]
    missing = [c for c in CATEGORY_ORDER if c not in seen]
    dup = [c for c in seen if seen.count(c) > 1]
    unknown = [c for c in seen if c not in CATEGORY_ORDER]
    problems = []
    if missing:
        problems.append(f"未分组: {missing}")
    if dup:
        problems.append(f"重复分组: {sorted(set(dup))}")
    if unknown:
        problems.append(f"组里有未知类别: {unknown}")
    if problems:
        raise ValueError("GROUPS 配置有误 — " + "；".join(problems))


_validate_groups()


class NoticeClassifier:

    # ============================================================
    # 规则表: (关键词列表, 子标签)
    # 列表内按精确度降序——越具体的锚点越靠前
    # ============================================================

    COMPETITION_RULES = [
        (["挑战杯", "互联网+", "中国大学生", "全国大学生", "省大学生"], "学科竞赛"),
        (["创新创业", "创业计划", "创业大赛", "创业竞赛"], "创新创业"),
        (["创新大赛", "选拔赛", "预选赛"], "学科竞赛"),
        (["学科竞赛", "技能大赛", "专项赛", "科普辅导员", "金相技能大赛",
          "机械工程创新", "化学实验创新", "大学生物理实验", "信息安全竞赛",
          "电子设计竞赛", "数学建模", "英语竞赛", "机器人",
          "情景剧大赛", "诵写讲", "汉字书写大赛", "经典诵写讲"], "学科竞赛"),
        (["学术报告会", "学术讲座", "学术论坛", "学术交流",
          "报告解读", "专题报告", "辅导报告"], "讲座报告"),
        (["大赛", "竞赛", "比赛"], "学科竞赛"),
    ]

    HOLIDAY_RULES = [
        (["清明", "五一", "端午", "中秋", "国庆", "元旦", "春节", "劳动节"], "放假通知"),
        (["放假", "调休", "节假日"], "放假通知"),
        (["校历"], "假期安排"),
        (["暑期", "寒假", "暑假", "假期"], "假期安排"),
        (["开学", "返校", "报到注册", "新生报到"], "开学返校"),
    ]

    RESEARCH_RULES = [
        (["基金委", "自然科学基金", "国家自然科学基金", "国家重点研发计划",
          "科技重大专项", "重大专项", "科技创新2030",
          "项目指南", "申报指南", "指南引导", "立项建议",
          "原创探索计划", "非共识项目", "揭榜挂帅", "课题申报",
          "研究专项", "培育计划"], "项目申报"),
        (["项目申报", "课题申报", "项目立项", "立项申报", "申报通知",
          "培育计划", "提升计划", "科学技术奖", "科技成果"], "项目申报"),
    ]

    ACADEMIC_RULES = [
        (["选课", "四六级", "大学英语", "转专业", "学籍",
          "考试报名", "补考", "重修", "推免", "学士学位",
          "毕业审核", "实验教学", "教学安排", "培养方案"], "教学运行"),
    ]

    # 教务类的否定条件: 带这些词的「培养方案/教学」通知是行政调研，不是学生事务
    ACADEMIC_EXCLUDE = ["修订", "调研", "征求意见", "论证", "编制"]

    STUDENT_RULES = [
        (["新生入学教育", "军训", "入学教育"], "新生入学"),
        (["心理关爱", "心理健康", "心理咨询", "心理育人"], "心理健康"),
        (["奖助学金", "奖学金", "助学金", "助学贷款", "家庭经济困难",
          "勤工助学", "困难认定", "学费减免"], "奖助学金"),
        (["宿舍", "寝室", "熄灯", "公寓", "学生社区"], "宿舍管理"),
        (["学风建设", "学业发展", "学业指导", "考风考纪", "诚信考试",
          "早锻炼", "晨启青春"], "学风建设"),
        (["评优", "评选", "表彰", "优秀毕业生", "十佳大学生",
          "辅导员", "校长奖", "攀登之星"], "评优评奖"),
        (["毕业生离校", "离校", "行李托运", "托运行李", "送站", "毕业季"], "毕业服务"),
        (["就业", "招聘会", "招聘", "求职", "生涯规划", "职业发展"], "就业指导"),
        (["反诈", "防诈骗", "电信网络诈骗", "安全教育", "网络素养", "网络文明",
          "学宪法", "宪法", "文明寝室", "社会实践", "志愿服务活动",
          "学生工作月历", "兼职"], "日常教育"),
    ]

    SUPPORT_RULES = [
        (["停电", "停水", "供水", "供电", "浴室", "二次供水"], "水电保障"),
        (["食堂", "停伙", "复伙", "餐饮", "就餐"], "餐饮服务"),
        (["物业", "维修", "改造项目", "采购公告", "招标", "中标",
          "年货节", "施药", "驱虫", "草坪"], "校园物业"),
        (["台风", "暴雨", "低温", "雨雪", "冰冻", "天气", "防汛"], "安全提示"),
        (["温馨提示", "班车", "校车", "菜市场", "便利"], "生活服务"),
    ]

    # ============================================================
    # 排除词
    # ============================================================

    # 与类别无关的一律不算节假日（旧版就有，保留）
    HOLIDAY_EXCLUDE = [
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

    LECTURE_EXCLUDE = [
        "述职报告", "督查报告", "审计报告",
        "调研报告", "财务报告", "工作报告",
        "体检报告归档", "提交体检报告",
    ]

    # 事后通报 / 文体活动 —— 竞赛和节假日都不要
    COMMON_EXCLUDE = [
        "获奖", "获奖作品", "获奖名单", "名单公示", "结果公示", "结果公布",
        "评选结果", "评审结果", "拟推荐名单", "推荐名单", "获奖情况",
        "拟表彰", "拟聘用", "拟聘人选", "立项结果", "立项公示", "拟立项",
        "包粽子", "征文活动", "作品征集", "征集启事",
        "观影", "读书会", "分享会", "座谈会", "表彰",
    ]

    # 兜底闸门: 标题锚定「政务/公务流程」，不是学生能报名参加的比赛
    DEBATE_TAIL = ("遴选", "表彰", "公示", "推荐", "评审", "评议")

    def __init__(self, competition_keywords=None, holiday_keywords=None):
        # 兼容旧签名；规则以类常量为准
        pass

    # ============================================================
    # 对外入口
    # ============================================================

    def classify(self, notice: dict) -> str:
        title = notice.get("title", "") or ""
        notice["sub_label"] = ""

        # ---- 1. 竞赛 ----
        # 结果公示类会在 _match_competition 内部被挡下，
        # 但不在这里 return——它可能还要落进后面的「公示公告」
        if self._match_competition(title):
            return "competition"

        # ---- 2. 节假日 ----
        if self._match_holiday(title):
            return "holiday"

        # ---- 3. 科研申报 ----
        if self._match_rules(title, self.RESEARCH_RULES):
            return "research"

        # ---- 4. 教务教学 ----
        if (self._match_rules(title, self.ACADEMIC_RULES)
                and not self._match_any(title, self.ACADEMIC_EXCLUDE)):
            return "academic"

        # ---- 5. 学工通知 ----
        if self._match_rules(title, self.STUDENT_RULES):
            return "student"

        # ---- 6. 后勤服务 ----
        if self._match_rules(title, self.SUPPORT_RULES):
            return "support"

        # ---- 7. 公示公告 ----
        # 走到这里说明不是任何「要你办事」的通知，
        # 但明显是评审结果/名单/活动，归到公示公告而不是「其他」
        if self._is_announcement(title):
            return "notice"

        logger.debug(f"[other/未匹配] {title}")
        return "other"

    def _is_announcement(self, title: str) -> bool:
        """
        公示公告判定。

        注意别写成「含通知就算公告」——那样会把所有通知都吞进来，
        这个类别就退化成第二个「其他」了。只认真正的公示/公告语义。
        """
        if self._match_any(title, self.COMMON_EXCLUDE):
            return True
        if self._is_result_publicity(title):
            return True
        return self._match_any(title, ("公示", "公告", "名单", "研讨会", "论坛", "征集"))

    # ============================================================
    # 各分支判定
    # ============================================================

    def _match_competition(self, title: str) -> bool:
        # 结果公示 / 政务流程类的「大赛」都不是能报名的竞赛
        if self._is_result_publicity(title) or self._is_official_selection(title):
            return False

        for keywords, sub_label in self.COMPETITION_RULES:
            if not self._match_any(title, keywords):
                continue

            # 「讲座报告」要排掉述职/审计这种假讲座
            if sub_label == "讲座报告" and self._match_any(title, self.LECTURE_EXCLUDE):
                continue

            self._last_sub = sub_label
            return True
        return False

    def _match_holiday(self, title: str) -> bool:
        if self._match_any(title, self.HOLIDAY_EXCLUDE):
            return False
        if self._is_result_publicity(title):
            return False
        if self._is_holiday_false_positive(title):
            return False

        for keywords, sub_label in self.HOLIDAY_RULES:
            if self._match_any(title, keywords):
                self._last_sub = sub_label
                return True
        return False

    def _match_rules(self, title: str, rules) -> bool:
        # 结果公示不是「要你办事」的通知，不占用任何行动类目录
        if self._is_result_publicity(title):
            return False
        for keywords, sub_label in rules:
            if self._match_any(title, keywords):
                self._last_sub = sub_label
                return True
        return False

    # ============================================================
    # 排除逻辑
    # ============================================================

    @staticmethod
    def _is_result_publicity(title: str) -> bool:
        """
        结果公示判定 —— 两道闸门:

        1) 词表: COMMON_EXCLUDE 里的强排除词
        2) 结构: 事后通报动词 + 评选对象

        ⚠️ 关键是区分对偶的用词:
             「获奖名单的公示」 → 排除（事后）
             「评选工作的通知」 → 保留（事前，还能报名）
           所以「评选/评审」后面跟的是「公示」才排除。
        """
        if NoticeClassifier._match_any(title, NoticeClassifier.COMMON_EXCLUDE):
            return True

        verdicts = ("公示", "名单", "结果", "获奖", "表彰", "揭晓")
        subjects = ("作品", "人选", "个人", "单位", "集体", "团队", "项目")
        return any(v in title for v in verdicts) and any(s in title for s in subjects)

    @staticmethod
    def _is_official_selection(title: str) -> bool:
        """
        政务流程类的「大赛」不算学生能报名的竞赛。

        必须同时满足: 含比赛类词 + 以遴选/表彰/推荐…收尾。
        只看收尾词会把「优秀辅导员拟表彰名单的公示」也误伤。
        """
        if not NoticeClassifier._match_any(title, ("大赛", "竞赛", "比赛", "评选")):
            return False
        return any(title.rstrip("的通知 ").endswith(t) for t in NoticeClassifier.DEBATE_TAIL)

    @staticmethod
    def _is_holiday_false_positive(title: str) -> bool:
        """二次确认节假日误匹配（正则）"""
        patterns = [
            r'暑期.{0,10}(值班|浴室|实验室|心理|施工|安全|VPN|网络|平台|检查|维修)',
            r'(值班|浴室|施工|安全|VPN|心理|实验室).{0,8}暑期',
            r'暑期.{0,6}(开放|关闭|停|恢复|调整)',
            r'(实践|调研|实习|实训).{0,6}假期',
            r'假期.{0,6}(实践|调研|实习|实训)',
        ]
        return any(re.search(pat, title) for pat in patterns)

    # ============================================================
    # 批量 & 工具
    # ============================================================

    def classify_batch(self, notices: list) -> dict:
        result = {k: [] for k in CATEGORIES}
        for notice in notices:
            self._last_sub = ""
            category = self.classify(notice)
            notice["category"] = category
            notice["sub_label"] = self._last_sub
            result[category].append(notice)

        total = len(notices)
        summary = " ".join(f"{k}={len(result[k])}" for k in CATEGORY_ORDER if result[k])
        logger.info(f"classify: total={total} {summary}")
        return result

    @staticmethod
    def _match_any(title: str, keywords) -> bool:
        return any(kw in title for kw in keywords)

    @staticmethod
    def get_emoji(category: str) -> str:
        return CATEGORIES.get(category, CATEGORIES["other"])["emoji"]

    @staticmethod
    def get_label(category: str) -> str:
        return CATEGORIES.get(category, CATEGORIES["other"])["label"]


# 命令行测试
if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    c = NoticeClassifier()

    # (标题, 期望类别, 期望子标签；子标签留空表示不校验)
    tests = [
        # ===== 竞赛 =====
        ("关于举办2026新域新质创新大赛校内选拔赛的通知", "competition", "学科竞赛"),
        ("关于举办第二届全球校友创新创业大赛的通知", "competition", "创新创业"),
        ("关于组织参加第十五届挑战杯创业计划竞赛的通知", "competition", "学科竞赛"),
        ("关于举办安徽省第二届科普辅导员职业技能大赛的通知", "competition", "学科竞赛"),
        ("关于举办学术报告会的通知", "competition", "讲座报告"),
        ("关于组织参加2026年安徽省大学生金相技能大赛的通知", "competition", "学科竞赛"),
        ("关于举办屯溪路校区教职工体检报告解读暨健康咨询活动的通知", "competition", "讲座报告"),
        ("关于举办2026年校园防诈骗情景剧大赛的通知", "competition", "学科竞赛"),
        ("关于举办合肥工业大学第八届中华经典诵写讲大赛校级预选赛的通知", "competition", "学科竞赛"),
        # ===== 节假日（正确） =====
        ("关于2026年清明节放假安排的通知", "holiday", "放假通知"),
        ("关于五一劳动节放假及调课安排的通知", "holiday", "放假通知"),
        ("关于2026年暑期有关事项的通知", "holiday", "假期安排"),
        ("关于2026年寒假放假的通知", "holiday", "放假通知"),
        ("合肥工业大学2026-2027学年校历", "holiday", "假期安排"),
        ("关于2026年暑期学生放假及秋季学期开学安排的通知", "holiday", "放假通知"),
        ("关于2026年端午节放假的通知", "holiday", "放假通知"),
        # ===== 假节假日（应排除） =====
        ("2026年合肥工业大学医院（合肥校区）暑期值班表", "other", ""),
        ("关于屯溪路校区本科生浴室暑期开放时间安排的通知", "support", ""),
        ("关于做好2026年暑期实验室安全管理工作的通知", "other", ""),
        ("关于开通2026年暑期学生心理关爱服务线上平台的通知", "student", ""),
        ("关于屯溪路校区聚英路施工期间道路临时绕行的通知", "other", ""),
        ("关于防范台风的温馨提示", "support", ""),
        ("合肥校区2026年暑期食堂停伙、复伙通知", "support", ""),
        # ===== 结果公示（不能进竞赛/节假日，应落到公示公告） =====
        ("关于合肥工业大学“赓续文脉，典耀中华”主题诵写讲比赛暨汉字书写大赛校内选拔赛获奖作品的公示", "notice", ""),
        ("关于2026年寒假主题教育实践活动优秀作品、优秀组织单位的公示", "notice", ""),
        ("关于合肥工业大学2026年“优秀辅导员”拟表彰名单的公示", "notice", ""),
        ("关于合肥工业大学2026年十佳大学生评选结果的公示", "notice", ""),
        ("关于2026年度“哲学社会科学培育计划”（辅导员研究专项）立项结果的公示", "notice", ""),
        ("关于开展“迎端午”包粽子活动的通知", "notice", ""),  # 非行动类活动 → 公示公告
        ("关于出席省委教育工委召开的高校党代表会议代表候选人初步人选的公示", "notice", ""),
        # 对偶: 事前的工作通知必须保留，别被结果公示规则误伤
        ("关于做好“攀登之星奖助学金”评选工作的通知", "student", "奖助学金"),
        ("关于在合肥校区2026届毕业生中评选合肥工业大学优秀毕业生和安徽省优秀毕业生的通知", "student", "评优评奖"),
        # ===== 科研申报 =====
        ("国家重点研发计划：“尖刀”技术攻关工程重点专项2026年度项目申报指南征求意见", "research", ""),
        ("基金委发布2026年度医学科学部专项项目指南（第一批）的通告", "research", ""),
        ("关于申报2026年度学术新人提升计划项目的通知", "research", ""),
        ("基金委发布2027年度数学物理科学部重大项目立项建议的通告", "research", ""),
        # ===== 教务教学 =====
        ("关于合肥校区2026年下半年全国大学英语四、六级考试报名的通知", "academic", ""),
        ("关于做好2026-2027学年第一学期实验教学安排的通知", "academic", ""),
        ("关于做好2024年转专业学生管理工作的通知", "academic", ""),
        # ===== 学工通知 =====
        ("关于做好2026-2027学年家庭经济困难学生认定工作的通知", "student", "奖助学金"),
        ("关于做好合肥校区2026级新生入学教育工作的通知", "student", "新生入学"),
        ("关于招募2026年学生军训助理的通知", "student", "新生入学"),
        ("关于开展学生宿舍安全隐患专项排查工作的通知", "student", "宿舍管理"),
        ("关于开展2026年“学在工大”学风建设月活动的通知", "student", "学风建设"),
        ("关于进一步加强学生防范电信网络诈骗工作的通知", "student", "日常教育"),
        ("寝室熄灯倡议：养成良好作息习惯，共创文明寝室环境", "student", "宿舍管理"),
        ("关于合肥校区2026届毕业生行李托运的通知", "student", "毕业服务"),
        # ===== 后勤服务 =====
        ("关于屯溪路校区部分区域停电通知", "support", "水电保障"),
        ("关于对合肥校区二次供水设施进行清洗消毒的通知", "support", "水电保障"),
        ("合肥工业大学六安路校区菜市场2026年度维修改造项目采购公告", "support", "校园物业"),
        ("关于屯溪路校区重阳木施药作业的温馨提示", "support", "校园物业"),
        ("关于低温雨雪冰冻天气防范应对的温馨提示", "support", "安全提示"),
        # ===== 其他 =====
        ("关于开展2027版本科专业人才培养方案修订调研工作的通知", "other", ""),
        ("关于组织参观2026世界制造业大会的通知", "other", ""),
    ]

    ok = 0
    for title, exp_cat, exp_sub in tests:
        n = {"title": title}
        c._last_sub = ""
        got = c.classify(n)
        got_sub = c._last_sub
        cat_ok = got == exp_cat
        sub_ok = exp_sub == "" or got_sub == exp_sub
        if cat_ok and sub_ok:
            ok += 1
        else:
            print(f"FAIL exp={exp_cat}/{exp_sub} got={got}/{got_sub}\n     {title}")

    print(f"\n{ok}/{len(tests)} passed")

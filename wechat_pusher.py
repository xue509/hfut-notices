"""
合工大通知公告 - 微信推送模块

支持三种推送方式：
  1. PushPlus — 扫码即用，免费（需实名认证）
     https://www.pushplus.plus/
  2. Server酱 — 扫码即用，免费，无需实名认证
     https://sct.ftqq.com/
  3. 微信测试号 — 体验接近真实公众号，限 100 人
     https://mp.weixin.qq.com/debug/cgi-bin/sandboxinfo?action=showinfo
"""

import json
import logging
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ============================================================
# 推送消息格式化
# ============================================================


def format_push_message(category: str, notices: list[dict]) -> dict:
    """
    将分类后的通知格式化为推送消息。

    返回 {"title": "推送标题", "content": "推送正文(Markdown)"}
    """
    emoji_map = {
        "competition": "🏆",
        "holiday": "📅",
    }
    label_map = {
        "competition": "竞赛通知",
        "holiday": "节假日通知",
    }

    emoji = emoji_map.get(category, "📌")
    label = label_map.get(category, "通知")
    today = datetime.now().strftime("%Y-%m-%d")
    sub_emoji_map = {"学科竞赛": "🏅", "创新创业": "💡", "课题申报": "📋",
                     "讲座报告": "🎙️", "放假通知": "🎉", "假期安排": "📆", "开学返校": "🏫"}

    title = f"{emoji} 合工大{label} ({today})"

    lines = [
        f"## {emoji} 合工大{label}",
        f"",
        f"**更新时间**: {today}  |  **数量**: {len(notices)} 条",
        f"",
        "---",
        "",
    ]

    for i, n in enumerate(notices, 1):
        sub = n.get("sub_label", "")
        sub_tag = f" `{sub_emoji_map.get(sub,'')}{sub}`" if sub else ""
        summary = n.get("summary", "")

        lines.append(f"**{i}.** [{n['title']}]({n['link']}){sub_tag}")
        lines.append(f"> 📅 {n['date']}  |  来源: {n.get('source_tab', '')}")
        if summary:
            # 摘要最多80字
            short = summary[:80] + ("…" if len(summary) > 80 else "")
            lines.append(f"> 📝 {short}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("📬 *本消息由合工大通知监控系统自动推送*")
    lines.append(f"📱 [打开 App](https://xue509.github.io/hfut-notices/) | [查看官网](https://news.hfut.edu.cn/tzgg2.htm)")

    content = "\n".join(lines)

    # 截断处理
    if len(content) > 1800:
        lines = lines[:15]
        lines.append("")
        lines.append(f"> ⚠️ 内容较多，已截断。共 {len(notices)} 条新通知。")
        lines.append(f"> 📱 [打开 App 查看全部](https://xue509.github.io/hfut-notices/)")
        content = "\n".join(lines)

    return {"title": title, "content": content}


# ============================================================
# PushPlus 推送
# ============================================================


class PushPlusPusher:
    """
    PushPlus 推送渠道。

    使用步骤:
      1. 微信扫一扫关注 "PushPlus" 公众号
      2. 公众号菜单 -> 个人中心 -> 获取 token
      3. 填入配置文件
    """

    API_URL = "https://www.pushplus.plus/send"

    def __init__(self, token: str):
        self.token = token

    def push(self, title: str, content: str, template: str = "markdown") -> bool:
        """
        发送推送消息。

        参数:
            title: 消息标题
            content: 消息正文 (支持 Markdown)
            template: 消息模板 (html / markdown)

        返回: 是否发送成功
        """
        if not self.token or self.token == "your_pushplus_token_here":
            logger.warning("PushPlus token 未配置，跳过推送")
            return False

        payload = {
            "token": self.token,
            "title": title,
            "content": content,
            "template": template,
        }

        try:
            resp = requests.post(self.API_URL, json=payload, timeout=15)
            data = resp.json()

            if data.get("code") == 200:
                logger.info(f"PushPlus 推送成功: {title}")
                return True
            else:
                logger.error(f"PushPlus 推送失败: {data}")
                return False

        except requests.RequestException as e:
            logger.error(f"PushPlus 推送异常: {e}")
            return False


# ============================================================
# Server酱 推送 (无需实名认证)
# ============================================================


class ServerChanPusher:
    """
    Server酱 (ServerChan) 推送渠道 — 无需实名认证。

    使用步骤:
      1. 微信扫码关注 "Server酱" 公众号 / 打开 https://sct.ftqq.com/
      2. 微信登录后，在 SendKey 页面获取你的 SENDKEY
      3. 填入配置文件
    """

    API_URL = "https://sctapi.ftqq.com/{sendkey}.send"

    def __init__(self, sendkey: str):
        self.sendkey = sendkey

    def push(self, title: str, content: str) -> bool:
        """发送推送消息"""
        if not self.sendkey or self.sendkey == "your_serverchan_sendkey_here":
            logger.warning("Server酱 sendkey 未配置，跳过推送")
            return False

        # Server酱 Markdown 格式用 desp 字段，支持 Markdown
        # 内容过长时截断（上限约 64KB，保守设为 8000 字符）
        if len(content) > 8000:
            content = content[:8000] + "\n\n> ⚠️ 内容已截断"

        payload = {
            "title": title,
            "desp": content,
        }

        url = self.API_URL.format(sendkey=self.sendkey)
        try:
            resp = requests.post(url, data=payload, timeout=15)
            data = resp.json()

            if data.get("code") == 0:  # Server酱 code=0 表示成功
                logger.info(f"Server酱推送成功: {title}")
                return True
            else:
                logger.error(f"Server酱推送失败: {data}")
                return False

        except requests.RequestException as e:
            logger.error(f"Server酱推送异常: {e}")
            return False


# ============================================================
# 微信测试号推送
# ============================================================


class WeChatTestPusher:
    """
    微信测试号推送渠道。

    配置步骤:
      1. 打开 https://mp.weixin.qq.com/debug/cgi-bin/sandboxinfo?action=showinfo
      2. 微信扫码登录，获得 appID 和 appSecret
      3. 在页面下方「模板消息接口」添加模板:
         模板标题: 通知提醒
         模板内容:
           {{first.DATA}}
           通知类型: {{keyword1.DATA}}
           发布时间: {{keyword2.DATA}}
           {{remark.DATA}}
      4. 关注测试号二维码
      5. 在「测试号二维码」旁边获取你的 openid
    """

    TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
    SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/template/send"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        template_id: str,
        openid: str = "",
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.template_id = template_id
        self.openid = openid
        self._access_token: Optional[str] = None
        self._token_expires: float = 0

    def _get_access_token(self) -> Optional[str]:
        """获取微信 access_token (带缓存)"""
        import time

        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        if not self.app_id or self.app_id == "your_app_id_here":
            logger.warning("微信测试号 app_id 未配置")
            return None

        try:
            resp = requests.get(
                self.TOKEN_URL,
                params={
                    "grant_type": "client_credential",
                    "appid": self.app_id,
                    "secret": self.app_secret,
                },
                timeout=15,
            )
            data = resp.json()

            if "access_token" in data:
                self._access_token = data["access_token"]
                # 提前 5 分钟过期
                self._token_expires = time.time() + data.get("expires_in", 7200) - 300
                logger.debug("微信 access_token 获取成功")
                return self._access_token
            else:
                logger.error(f"获取 access_token 失败: {data}")
                return None

        except requests.RequestException as e:
            logger.error(f"获取 access_token 异常: {e}")
            return None

    def push(
        self,
        title: str,
        content: str,
        notice_type: str = "",
        openid: str = "",
    ) -> bool:
        """
        通过模板消息推送单条通知。

        注意：模板消息每次只能推一条。多条通知需分别调用。
        """
        token = self._get_access_token()
        if not token:
            return False

        target_openid = openid or self.openid
        if not target_openid:
            logger.warning("微信 openid 未配置，跳过推送")
            return False

        # 截取合适长度（微信模板消息有字段长度限制）
        first_text = title[:60]
        keyword1 = notice_type or "通知"
        keyword2 = datetime.now().strftime("%Y-%m-%d %H:%M")
        remark = content[:200]

        payload = {
            "touser": target_openid,
            "template_id": self.template_id,
            "data": {
                "first": {"value": first_text, "color": "#173177"},
                "keyword1": {"value": keyword1, "color": "#173177"},
                "keyword2": {"value": keyword2, "color": "#173177"},
                "remark": {"value": remark, "color": "#666666"},
            },
        }

        try:
            resp = requests.post(
                self.SEND_URL,
                params={"access_token": token},
                json=payload,
                timeout=15,
            )
            data = resp.json()

            if data.get("errcode") == 0:
                logger.info(f"微信模板消息推送成功: {title}")
                return True
            else:
                logger.error(f"微信模板消息推送失败: {data}")
                return False

        except requests.RequestException as e:
            logger.error(f"微信模板消息推送异常: {e}")
            return False

    def push_notice(self, notice: dict, openid: str = "") -> bool:
        """推送单条通知的便捷方法"""
        category = notice.get("category", "other")
        emoji_map = {"competition": "🏆 竞赛通知", "holiday": "📅 节假日通知"}

        title = f"{notice['title']}"
        notice_type = emoji_map.get(category, "通知")
        content = (
            f"日期: {notice['date']}\n"
            f"来源: {notice.get('source_tab', '')}\n"
            f"点击查看详情"
        )

        return self.push(
            title=title,
            content=content,
            notice_type=notice_type,
            openid=openid,
        )


# ============================================================
# 统一推送接口
# ============================================================


class NoticePusher:
    """统一推送管理器：根据配置选择推送渠道"""

    def __init__(self, config: dict):
        """
        config: pusher 部分的配置字典
        {
            "mode": "pushplus" | "serverchan" | "wechat_test" | "both",
            "pushplus": {"token": "..."},
            "serverchan": {"sendkey": "..."},
            "wechat_test": {"app_id": "...", "app_secret": "...", "template_id": "...", "openid": "..."}
        }
        """
        self.mode = config.get("mode", "pushplus")
        self.pushplus: Optional[PushPlusPusher] = None
        self.serverchan: Optional[ServerChanPusher] = None
        self.wechat_test: Optional[WeChatTestPusher] = None

        # 初始化 PushPlus
        pp_config = config.get("pushplus", {})
        pp_token = pp_config.get("token", "")
        if pp_token and pp_token != "your_pushplus_token_here":
            self.pushplus = PushPlusPusher(token=pp_token)
            logger.info("PushPlus 推送已启用")

        # 初始化 Server酱
        sc_config = config.get("serverchan", {})
        sc_sendkey = sc_config.get("sendkey", "")
        if sc_sendkey and sc_sendkey != "your_serverchan_sendkey_here":
            self.serverchan = ServerChanPusher(sendkey=sc_sendkey)
            logger.info("Server酱推送已启用")

        # 初始化微信测试号
        wx_config = config.get("wechat_test", {})
        wx_app_id = wx_config.get("app_id", "")
        if wx_app_id and wx_app_id != "your_app_id_here":
            self.wechat_test = WeChatTestPusher(
                app_id=wx_app_id,
                app_secret=wx_config.get("app_secret", ""),
                template_id=wx_config.get("template_id", ""),
                openid=wx_config.get("openid", ""),
            )
            logger.info("微信测试号推送已启用")

    def push_category(self, category: str, notices: list[dict]) -> dict:
        """
        推送某一分类的所有通知。

        返回: {"pushplus": bool, "serverchan": bool, "wechat_test": int}
        """
        if not notices:
            logger.info(f"分类 '{category}' 无通知，跳过推送")
            return {"pushplus": False, "serverchan": False, "wechat_test": 0}

        result = {"pushplus": False, "serverchan": False, "wechat_test": 0}

        # 格式化消息
        msg = format_push_message(category, notices)
        logger.info(f"准备推送: {msg['title']} ({len(notices)} 条)")

        # PushPlus: 一条合并消息
        if self.mode in ("pushplus", "both") and self.pushplus:
            result["pushplus"] = self.pushplus.push(msg["title"], msg["content"])

        # Server酱: 一条合并消息（无需实名认证）
        if self.mode in ("serverchan", "both") and self.serverchan:
            result["serverchan"] = self.serverchan.push(msg["title"], msg["content"])

        # 微信测试号: 逐条推送（模板消息限制）
        if self.mode in ("wechat_test", "both") and self.wechat_test:
            success_count = 0
            for notice in notices:
                if self.wechat_test.push_notice(notice):
                    success_count += 1
            result["wechat_test"] = success_count
            logger.info(
                f"微信测试号推送: {success_count}/{len(notices)} 条成功"
            )

        return result


# 命令行测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    # 测试消息格式化
    test_notices = [
        {
            "title": "关于举办2026新域新质创新大赛校内选拔赛的通知",
            "date": "2026-07-21",
            "link": "https://example.com/test1",
            "source_tab": "教学科研",
            "category": "competition",
        },
        {
            "title": "关于举办第二届全球校友创新创业大赛的通知",
            "date": "2026-07-21",
            "link": "https://example.com/test2",
            "source_tab": "教学科研",
            "category": "competition",
        },
    ]

    msg = format_push_message("competition", test_notices)
    print("=== 推送标题 ===")
    print(msg["title"])
    print()
    print("=== 推送正文 ===")
    print(msg["content"])

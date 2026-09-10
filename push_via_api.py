#!/usr/bin/env python3
"""
走 GitHub API 发布提交 —— 当 github.com 的 git 端口连不上时的备用通道。

国内网络下 `git push` 经常卡在 "Recv failure: Connection was reset"，
但 api.github.com 往往能通。这个脚本用 Git Data API 拼一个提交出来，
效果和 git push 一样（快进、不改历史）。

用法:
    set GITHUB_TOKEN=ghp_xxx        # Windows cmd
    export GITHUB_TOKEN=ghp_xxx     # bash
    py -3.9 push_via_api.py

    # 或指定要发布的提交（默认 HEAD）
    py -3.9 push_via_api.py --commit 1bcef7f

⚠️ token 只从环境变量读，别写进文件。用完记得去
   GitHub → Settings → Developer settings → Tokens 检查并轮换。
"""

import argparse
import base64
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = os.environ.get("GITHUB_REPO", "xue509/hfut-notices")
BRANCH = os.environ.get("GITHUB_BRANCH", "master")
API = f"https://api.github.com/repos/{REPO}"

# 需要注意: .github/workflows/ 下的文件走 Git Data API 一律 404，
# GitHub 要求写 workflow 必须带 workflow 权限。要改 workflow 只能走 git push。
WORKFLOW_PREFIX = ".github/workflows/"


def git(*args: str) -> str:
    """跑 git 命令。必须显式 utf-8 —— Windows 默认 GBK 解码会让中文提交信息崩掉。"""
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"git {' '.join(args)} 失败:\n{r.stderr[:400]}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description="用 GitHub API 发布一次提交")
    ap.add_argument("--commit", default="HEAD", help="要发布的本地提交 (默认 HEAD)")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--branch", default=BRANCH)
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.exit("请先设置环境变量 GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github.v3+json"}

    def api(method: str, path: str, **kw):
        """带重试 —— 这个网络到 api.github.com 时通时断"""
        last = ""
        for attempt in range(6):
            try:
                r = requests.request(method, f"{API}{path}", headers=headers,
                                     timeout=60, **kw)
                if r.status_code >= 500:
                    last = f"HTTP {r.status_code}"
                elif r.status_code >= 300:
                    sys.exit(f"API {method} {path} -> {r.status_code}\n{r.text[:500]}")
                else:
                    return r.json()
            except requests.RequestException as e:
                last = type(e).__name__
            if attempt < 5:
                wait = 2 ** attempt
                print(f"    [重试 {attempt + 1}/5] {last}，{wait}s 后再试")
                time.sleep(wait)
        sys.exit(f"API {method} {path} 重试耗尽: {last}")

    who = requests.get("https://api.github.com/user", headers=headers, timeout=30).json()
    print(f"token 身份: {who.get('login')}")

    # 远程当前状态 —— 当做 parent，保证是快进而不是覆盖历史
    head_sha = api("GET", f"/git/ref/heads/{args.branch}")["object"]["sha"]
    base_tree = api("GET", f"/git/commits/{head_sha}")["tree"]["sha"]
    print(f"远程 HEAD: {head_sha[:8]}")

    changed = git("diff", "--name-only", f"{args.commit}^", args.commit).split()
    skipped = [f for f in changed if f.startswith(WORKFLOW_PREFIX)]
    files = [f for f in changed if not f.startswith(WORKFLOW_PREFIX)]
    if skipped:
        print(f"⚠️  跳过（需 workflow 权限，请用 git push）: {', '.join(skipped)}")

    # 只发布和远程确实不同的文件 —— Actions 可能已经独立改过 docs/，
    # 别拿旧内容把它覆盖回去
    entries = []
    for f in files:
        path = f.replace("\\", "/")
        data = Path(f).read_bytes()
        try:
            r = requests.get(f"{API}/contents/{path}", headers=headers, timeout=60,
                             params={"ref": args.branch})
            remote_sha = r.json().get("sha") if r.status_code == 200 else None
        except requests.RequestException:
            remote_sha = None
        if remote_sha:
            local_sha = hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()
            if remote_sha == local_sha:
                print(f"  = {path}（远程已一致）")
                continue
        blob = api("POST", "/git/blobs",
                   json={"content": base64.b64encode(data).decode(), "encoding": "base64"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  ↑ {path} ({len(data)}B)")

    if not entries:
        print("\n远程已是最新，无需发布。")
        return 0

    tree = api("POST", "/git/trees",
               json={"base_tree": base_tree, "tree": entries})["sha"]
    message = git("log", "-1", "--pretty=%B", args.commit).strip()
    commit = api("POST", "/git/commits",
                 json={"message": message, "tree": tree, "parents": [head_sha]})
    api("PATCH", f"/git/refs/heads/{args.branch}",
        json={"sha": commit["sha"], "force": False})

    print(f"\n✅ 已发布 {commit['sha'][:8]}  {commit['html_url']}")
    print("   GitHub Pages 重建需要 1-2 分钟")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
游侠出行 · 百炼 Managed Agents API 一键部署

为什么要有这个脚本
------------------
在百炼控制台上手动点四下（新建智能体 → 选模型 → 贴提示词 → 装技能）也能把作品做出来，
但那样"这个智能体怎么配的"只存在于作者账号里，别人复现只能照着 README 再点一遍。

本脚本用 **百炼 Managed Agents REST API** 把这份配置变成**可执行、可版本管理、可复现**的代码：

    创建运行环境 → 找到飞猪官方技能 → 创建智能体（系统提示词 + 知识库 + 技能）→ 冒烟测试

对应 README 第十一节说的「百炼使用深度」：从"控制台手点"升级到"API 编程调用"。

接口依据
--------
- Endpoint：https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio
- 鉴权：Authorization: Bearer <api_key>   （账号级权限，切勿提交到仓库）
- 官方文档：
  · API 概览        https://help.aliyun.com/zh/model-studio/managed-agents-api-overview
  · 认证            https://help.aliyun.com/zh/model-studio/managed-agents-api-authentication
  · 创建 Agent      https://help.aliyun.com/zh/model-studio/agent-create
  · Environment     https://help.aliyun.com/zh/model-studio/environment-api
  · Skill           https://help.aliyun.com/zh/model-studio/skills-api
  · Session/Event   https://help.aliyun.com/zh/model-studio/session-api

用法
----
    # 先看会发什么请求，不真的调用（不需要 Key，建议第一次先跑这个）
    python3 scripts/deploy_bailian.py --dry-run

    # 校验 Key 与工作空间是否可用
    DASHSCOPE_API_KEY=sk-xxx python3 scripts/deploy_bailian.py --check --workspace ws_xxx

    # 正式部署
    DASHSCOPE_API_KEY=sk-xxx python3 scripts/deploy_bailian.py --workspace ws_xxx

    # 部署 + 建一个会话做冒烟测试
    DASHSCOPE_API_KEY=sk-xxx python3 scripts/deploy_bailian.py --workspace ws_xxx --smoke

    # 复用已有资源（幂等）：脚本会按名称查找，已存在就复用而不是重复创建
    python3 scripts/deploy_bailian.py --workspace ws_xxx --reuse

安全
----
* API Key 只从环境变量 DASHSCOPE_API_KEY 读取，**不接受命令行参数**（避免出现在 shell 历史和进程列表）
* 脚本不会把 Key 写进任何文件；`--dry-run` 输出中也不含 Key
* 百炼 API Key 是账号级权限，不要提交到仓库（`.env` 已在 .gitignore 中排除）

已知限制
--------
* 本脚本在编写时**未在真实账号上端到端跑通**（作者手边无可用 Key）。
  首次运行请先用 `--dry-run` 看请求体，再用 `--check` 验证凭据，最后正式部署。
* 飞猪技能的 `skill_id` / `version` 需要从工作空间的技能列表中匹配；
  脚本会列出候选并自动匹配名称含 `flyai` 的官方技能，匹配不到会明确报错并打印全部候选。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

REGION = "cn-beijing"
API_PATH = "/api/v1/agentstudio"

AGENT_NAME = "游侠出行"
ENV_NAME = "youxia-travel-env"
DEFAULT_MODEL = "qwen3.7-plus"
FLYAI_CLI = "@fly-ai/flyai-cli@1.0.16"


# ----------------------------------------------------------------- 基础

class Api:
    """极简 Managed Agents API 客户端（仅标准库）"""

    def __init__(self, workspace, api_key, dry_run=False):
        self.workspace = normalize_workspace(workspace)
        self.base = f"https://{self.workspace}.{REGION}.maas.aliyuncs.com{API_PATH}"
        self.key = api_key
        self.dry_run = dry_run
        self.log = []

    def call(self, method, path, payload=None, raw=False):
        url = self.base + path
        if self.dry_run and method != "GET":
            self.log.append((method, url, payload))
            return {"__dry_run__": True, "method": method, "url": url, "body": payload}
        data = None
        headers = {"Authorization": f"Bearer {self.key}",
                   "Accept": "application/json",
                   "User-Agent": "youxia-deploy"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                return body if raw else (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:500]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}\n{detail}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"{method} {path} -> 网络错误: {e.reason}") from None


def normalize_workspace(ws):
    """接受 ws_xxx / ws-xxx / 完整 Base URL 三种写法，统一成子域名形式"""
    ws = (ws or "").strip()
    if "://" in ws:                       # 用户直接粘了 Base URL
        ws = ws.split("://", 1)[1].split(".", 1)[0]
    return ws


# ----------------------------------------------------------------- 步骤

def diagnose(api):
    """凭据校验失败时，自动判断是「工作空间 ID 不对」还是「没有 AgentStudio 权限」。

    判据：同一个工作空间端点上，/api/v1/models 与 /api/v1/agentstudio 的返回对比。
      · models 200 + agentstudio 403  → 端点可达，但 AgentStudio 被拒（多半是 Key 不在
        智能体所在的工作空间，或该空间未开通 Managed Agents 的 API 权限）
      · models 403                    → 整个工作空间端点都不可达（workspace ID 可能不对）
      · models 400                    → 该子域不是合法的 AgentStudio 端点形式
    """
    host = f"https://{api.workspace}.{REGION}.maas.aliyuncs.com"
    out = []
    for path, label in [("/api/v1/models", "模型接口"), (API_PATH + "/agents", "AgentStudio 接口")]:
        try:
            req = urllib.request.Request(host + path, headers={
                "Authorization": f"Bearer {api.key}", "Accept": "application/json",
                "User-Agent": "youxia-deploy"})
            with urllib.request.urlopen(req, timeout=25) as r:
                out.append((label, r.status, ""))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:200]
            try:
                body = json.loads(body).get("code", "") or body
            except Exception:  # noqa: BLE001
                pass
            out.append((label, e.code, body))
        except urllib.error.URLError as e:
            out.append((label, 0, str(e.reason)))

    print("\n  诊断：")
    for label, code, msg in out:
        print(f"    {label:<18} HTTP {code}  {msg}")

    m = dict((l, c) for l, c, _ in out)
    print("\n  判断：")
    if m.get("模型接口") == 200 and m.get("AgentStudio 接口") == 403:
        print("    工作空间端点可达，但 AgentStudio 被拒绝。")
        print("    → 最可能的原因：这个 API Key 不在「智能体所在的工作空间」里。")
        print("      百炼的业务空间是相互隔离的：在 A 空间创建的 Key 调不到 B 空间的智能体。")
        print("\n    怎么修：")
        print("      1. 打开百炼控制台，右上角把工作空间切换到【你的智能体 Dong8 所在的那个空间】")
        print("         （打开智能体页面时，浏览器地址栏的 ?workspace= 后面的数字就是它）")
        print("      2. 在【那个空间】里打开 API Key 页面，新建或复制一个 Key")
        print("      3. 页面上的 Base URL 会显示该空间的子域（形如 ws-xxxxxxxxxxxx），")
        print("         把它作为 --workspace 传入，再跑一次")
    elif m.get("模型接口") == 400:
        print("    该子域不是合法的 AgentStudio 端点形式。")
        print("    → workspace_id 要用形如 ws_xxxxxxxxxxxx 的值（控制台右上角下拉菜单查看），")
        print("      而不是控制台地址栏里 ?workspace= 后面的数字。")
    elif m.get("模型接口") == 403:
        print("    整个工作空间端点都不可达。")
        print("    → workspace_id 很可能不对，或该 Key 不属于这个工作空间。")
    else:
        print("    未能自动定位原因，请对照上面的 HTTP 状态码排查。")


def read_system_prompt():
    p1 = os.path.join(ROOT, "prompts", "system-prompt.md")
    p2 = os.path.join(ROOT, "knowledge", "game-taste-map.md")
    for p in (p1, p2):
        if not os.path.exists(p):
            raise SystemExit(f"✗ 找不到 {p}")
    system = open(p1, encoding="utf-8").read().strip()
    kb = open(p2, encoding="utf-8").read().strip()
    return (system
            + "\n\n---\n\n# 附：游戏口味地图知识库\n\n"
            + "（本知识库随系统提示词一并注入。你必须在映射时严格依据此表，不得自行发明映射关系。）\n\n"
            + kb)


def build_environment_payload():
    """运行环境：云端沙箱 + 预装 flyai CLI + 放行出站访问"""
    return {
        "name": ENV_NAME,
        "description": "游侠出行运行环境：预装飞猪 flyai CLI，放行出站访问以调用飞猪开放平台",
        "config": {
            "type": "cloud",
            "packages": {"npm": [FLYAI_CLI]},
            "networking": {"type": "unrestricted"},
        },
    }


def build_agent_payload(system, skills):
    """智能体：模型 + 系统提示词（含知识库）+ 飞猪技能 + 内置工具包"""
    return {
        "name": AGENT_NAME,
        "description": "游戏玩家专属旅行助手：按玩家实际在玩的游戏理解体验偏好，"
                       "结合飞猪真实供给产出可预订旅程。",
        "model": {"id": DEFAULT_MODEL},
        "system": system,
        "tools": [{
            "type": "builtin_toolkit",
            "default_config": {"enabled": True,
                               "permission_policy": {"type": "always_allow"}},
        }],
        "mcp_servers": [],
        "skills": skills,
        "metadata": {"project": "youxia-travel-agent", "contest": "fliggy-ai-travel-2026"},
    }


def find_flyai_skill(api):
    """列出工作空间可用技能，匹配飞猪官方技能"""
    resp = api.call("GET", "/skills?limit=100")
    items = resp.get("items") or resp.get("data") or (resp if isinstance(resp, list) else [])
    if not items:
        raise RuntimeError("技能列表为空 —— 请确认该工作空间已安装飞猪官方技能，"
                           "或在百炼控制台「技能管理」中查看。")
    cands = []
    for it in items:
        name = (it.get("name") or "") + " " + (it.get("description") or "")
        if "flyai" in name.lower() or "飞猪" in name:
            cands.append(it)
    if not cands:
        print("  ! 未找到名称含 flyai / 飞猪 的技能。全部候选：")
        for it in items:
            print(f"      - {it.get('name')}  id={it.get('skill_id') or it.get('id')}")
        raise RuntimeError("请手动指定飞猪技能的 skill_id 与 version（可用 --skill-id / --skill-version）")
    pick = cands[0]
    sid = pick.get("skill_id") or pick.get("id")
    ver = pick.get("latest_version") or pick.get("version")
    return {"type": "official", "skill_id": sid, "version": str(ver)}


def find_by_name(api, path, name):
    resp = api.call("GET", f"{path}?limit=100")
    items = resp.get("items") or resp.get("data") or (resp if isinstance(resp, list) else [])
    for it in items:
        if it.get("name") == name:
            return it
    return None


# ----------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="游侠出行 · 百炼 Managed Agents API 一键部署")
    ap.add_argument("--workspace", help="工作空间 ID（控制台右上角下拉菜单，形如 ws_xxxxxxxxxxxx）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将发送的请求，不实际调用（不需要 Key）")
    ap.add_argument("--check", action="store_true", help="只校验凭据与工作空间连通性")
    ap.add_argument("--smoke", action="store_true", help="部署后建一个会话发一条消息做冒烟测试")
    ap.add_argument("--reuse", action="store_true", help="按名称复用已存在的环境与智能体，不重复创建")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"模型 ID（默认 {DEFAULT_MODEL}）")
    ap.add_argument("--skill-id", help="手动指定飞猪技能 skill_id")
    ap.add_argument("--skill-version", help="手动指定飞猪技能 version")
    args = ap.parse_args()

    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not args.dry_run and not api_key:
        print("✗ 请通过环境变量提供百炼 API Key：DASHSCOPE_API_KEY=sk-xxx python3 scripts/deploy_bailian.py ...",
              file=sys.stderr)
        print("  （不接受命令行参数，避免 Key 出现在 shell 历史里）", file=sys.stderr)
        return 2
    if not args.workspace:
        print("✗ 请用 --workspace 指定工作空间 ID（控制台右上角下拉菜单可查看）", file=sys.stderr)
        return 2

    system = read_system_prompt()
    api = Api(args.workspace, api_key, dry_run=args.dry_run)

    print("=" * 68)
    print("游侠出行 · 百炼 Managed Agents 部署")
    print("=" * 68)
    print(f"  Endpoint      {api.base}")
    print(f"  工作空间       {api.workspace}")
    print(f"  模型           {args.model}")
    print(f"  系统提示词     {len(system)} 字符（系统提示词 + 游戏口味地图知识库）")
    print(f"  模式           {'DRY-RUN（不实际调用）' if args.dry_run else ('仅校验' if args.check else '正式部署')}")
    print()

    # ---------- 0. 校验 ----------
    if not args.dry_run:
        print("▶ 0/4 校验凭据与工作空间 …")
        try:
            api.call("GET", "/agents?limit=1")
            print("  ✓ 凭据可用，工作空间可访问")
        except RuntimeError as e:
            print(f"  ✗ 校验失败：\n{e}")
            diagnose(api)
            return 1
        if args.check:
            print("\n仅校验模式，结束。")
            return 0

    # ---------- 1. 环境 ----------
    print("▶ 1/4 创建运行环境 …")
    env = None
    if args.reuse and not args.dry_run:
        env = find_by_name(api, "/environments", ENV_NAME)
        if env:
            print(f"  · 复用已有环境 {env.get('environment_id') or env.get('id')}")
    if env is None:
        payload = build_environment_payload()
        if args.dry_run:
            print("  [dry-run] POST /environments")
            print("  " + json.dumps(payload, ensure_ascii=False, indent=2)[:600].replace("\n", "\n  "))
        else:
            env = api.call("POST", "/environments", payload)
            print(f"  ✓ 环境已创建 {env.get('environment_id') or env.get('id')}")
    env_id = (env or {}).get("environment_id") or (env or {}).get("id") or "<environment_id>"

    # ---------- 2. 技能 ----------
    print("▶ 2/4 匹配飞猪官方技能 …")
    if args.skill_id and args.skill_version:
        skills = [{"type": "official", "skill_id": args.skill_id, "version": args.skill_version}]
        print(f"  · 使用手动指定：{args.skill_id} v{args.skill_version}")
    elif args.dry_run:
        skills = [{"type": "official", "skill_id": "<flyai_skill_id>", "version": "<version>"}]
        print("  [dry-run] GET /skills  →  匹配名称含 flyai 的官方技能")
    else:
        skills = [find_flyai_skill(api)]
        print(f"  ✓ 已匹配 {skills[0]['skill_id']} v{skills[0]['version']}")

    # ---------- 3. 智能体 ----------
    print("▶ 3/4 创建智能体 …")
    agent = None
    if args.reuse and not args.dry_run:
        agent = find_by_name(api, "/agents", AGENT_NAME)
        if agent:
            print(f"  · 复用已有智能体 {agent.get('agent_id') or agent.get('id')}")
    if agent is None:
        payload = build_agent_payload(system, skills)
        payload["model"]["id"] = args.model
        if args.dry_run:
            print("  [dry-run] POST /agents")
            preview = dict(payload)
            preview["system"] = f"<{len(system)} 字符的系统提示词 + 知识库>"
            print("  " + json.dumps(preview, ensure_ascii=False, indent=2).replace("\n", "\n  "))
        else:
            agent = api.call("POST", "/agents", payload)
            print(f"  ✓ 智能体已创建 {agent.get('agent_id') or agent.get('id')}"
                  f"  version={agent.get('version')}")
    agent_id = (agent or {}).get("agent_id") or (agent or {}).get("id") or "<agent_id>"

    # ---------- 4. 冒烟测试 ----------
    if args.smoke and not args.dry_run:
        print("▶ 4/4 冒烟测试：建会话并发一条消息 …")
        s = api.call("POST", "/sessions", {"agent": {"type": "agent", "id": agent_id},
                                           "environment_id": env_id,
                                           "title": "游侠出行 · 部署冒烟测试"})
        sid = s.get("session_id") or s.get("id")
        print(f"  ✓ 会话 {sid}")
        api.call("POST", f"/sessions/{sid}/events",
                 {"type": "user_message",
                  "content": "我玩一个很冷门的像素风独立游戏，周末想去成都玩，但不知道玩啥"})
        print("  · 消息已投递。轮询事件中（最多 90 秒）…")
        import time
        for _ in range(30):
            time.sleep(3)
            ev = api.call("GET", f"/sessions/{sid}/events?limit=100&order=desc")
            items = ev.get("items") or ev.get("data") or []
            texts = [e.get("text") or (e.get("content") or {}).get("text")
                     for e in items if e.get("type") in ("assistant_message", "agent_message")]
            texts = [t for t in texts if t]
            if texts:
                print("\n  —— 智能体回复（截取 500 字）——")
                print("  " + texts[0][:500].replace("\n", "\n  "))
                break
        else:
            print("  ! 90 秒内未取到回复，请到百炼控制台查看该会话")
    else:
        print("▶ 4/4 冒烟测试 … 已跳过（加 --smoke 可启用）")

    # ---------- 汇总 ----------
    print()
    print("=" * 68)
    print("部署完成")
    print("=" * 68)
    print(f"  environment_id   {env_id}")
    print(f"  agent_id         {agent_id}")
    print(f"  控制台            https://bailian.console.aliyun.com/{REGION}/managed-agent/agents")
    print()
    print("  下一步：")
    print("    1. 到百炼控制台确认智能体已出现，系统提示词与技能挂载正确")
    print("    2. 新建会话，把 FLYAI_API_KEY 填进【会话级环境变量】")
    print("       （未配置时会静默回落到公共试用 Key，返回结果里会出现「体验模式」字样）")
    print("    3. 把 agent_id / environment_id 记进 README，作为「API 可复现」的证据")
    return 0


if __name__ == "__main__":
    sys.exit(main())

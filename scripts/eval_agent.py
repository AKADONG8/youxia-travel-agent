#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
游侠出行 · Agent 评测脚本

为什么要有这个脚本
------------------
本项目的 README 里自己写过一句：「目前没有自动化测试……这是明确认知的短板」。
这个脚本就是来补这个短板的——它把「五步组队法」「三级匹配」「失败兜底」
这些**说法**，变成一组可重复、可量化的**指标**：

    · 三级匹配命中率      —— 说命中第几级，实际有没有命中
    · 工具调用率          —— 说调飞猪，实际有没有调
    · 真实数据率          —— 回复里有没有具体价格 / 车次号 / 预订链接
    · 兜底诚实率          —— 没数据时有没有如实告知（而不是编）
    · 追问完整率          —— 缺三要素时有没有追问

用法
----
    # 先看会跑哪些用例，不真的调（不需要 Key）
    python3 scripts/eval_agent.py --dry-run

    # 跑全部用例（约 15 分钟，会消耗百炼与飞猪额度）
    DASHSCOPE_API_KEY=sk-xxx python3 scripts/eval_agent.py \
        --workspace ws-xxxxxxxxxxxx --agent agent_xxx --env env_xxx

    # 只跑前 3 个用例做冒烟
    DASHSCOPE_API_KEY=sk-xxx python3 scripts/eval_agent.py ... --limit 3

    # 只跑某个用例
    python3 scripts/eval_agent.py ... --only E05

    # 注入飞猪 Key（不注入会走公共试用 Key，价格打码，但行为评测仍有效）
    FLYAI_API_KEY=xxx python3 scripts/eval_agent.py ...

输出
----
    demo/eval-report.md      评分报告（可直接贴进 README / issue）
    demo/eval-raw.json       每个用例的原始回复与事件，供人工复核

设计取舍（重要）
----------------
* 自动打分用**关键词启发式**，会有误判。所以同时输出 `eval-raw.json`，
  报告里每一行都留了「人工复核」列——**自动打分是初筛，不是终审**。
* 评测的是**行为**（有没有问、有没有调、有没有编），不是**数据准确性**。
  数据准确性由 demo/verification-report.md 逐条核验。
* 不注入 FLYAI_API_KEY 时飞猪会走公共试用额度，价格打码为「¥1xx」。
  此时「真实数据率」按「有没有出现价格/车次/链接的**结构**」判定，不看具体数字。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
REGION = "cn-beijing"
API_PATH = "/api/v1/agentstudio"

# ---------------------------------------------------------------- 测试集

# expect.level：期望命中的匹配层级（1=精确映射 2=风格兜底 3=反问引导）
# expect.must：回复里应出现的关键词（任一命中即可，用于判定层级/兜底/追问）
CASES = [
    # --- 第 ① 级：精确映射 ---
    {"id": "E01", "cat": "精确映射", "input": "我玩原神，周末想去成都玩两天，预算1000左右",
     "expect": {"level": 1, "must": ["张家界", "黄龙", "九寨沟", "成都"]}},
    {"id": "E02", "cat": "精确映射", "input": "我最近在玩黑神话悟空，想去山西玩，预算2000",
     "expect": {"level": 1, "must": ["悬空寺", "应县木塔", "晋祠", "云冈石窟", "山西"]}},
    {"id": "E03", "cat": "精确映射", "input": "我平时打英雄联盟，想看线下比赛，上海出发，预算1500",
     "expect": {"level": 1, "must": ["赛事", "电竞", "LPL", "场馆"]}},
    {"id": "E04", "cat": "精确映射", "input": "我在玩星露谷物语，想找个安静的地方待两天，杭州出发",
     "expect": {"level": 1, "must": ["田园", "民宿", "农家", "采摘", "乡村"]}},

    # --- 第 ② 级：风格兜底（小众 / 未收录游戏）---
    {"id": "E05", "cat": "风格兜底", "input": "我玩一个很冷门的像素风独立游戏，周末想去成都玩，但不知道玩啥",
     "expect": {"level": 2, "must": ["老街区", "复古", "独立书店", "旧物", "像素"]}},
    {"id": "E06", "cat": "风格兜底", "input": "我玩一个剧情很好的小众独立游戏，想出去走走，预算800",
     "expect": {"level": 2, "must": ["博物", "历史街区", "剧场", "文艺", "剧情"]}},

    # --- 第 ③ 级：反问引导 ---
    {"id": "E07", "cat": "反问引导", "input": "我玩一个叫「量子坍缩模拟器2049」的游戏",
     "expect": {"level": 3, "must": ["哪种", "喜欢", "看重", "偏好", "感觉", "？"]}},

    # --- 必问三要素 ---
    {"id": "E08", "cat": "追问三要素", "input": "我玩原神",
     "expect": {"level": None, "must": ["出发", "预算", "几天", "什么时候", "时间", "哪里"]}},

    # --- 真实数据（应调 flyai）---
    {"id": "E09", "cat": "真实数据", "input": "重庆出发，周五走周日回，预算1200，我玩英雄联盟，想去成都看比赛",
     "expect": {"level": None, "must": ["¥", "元", "G", "D", "Z", "T", "车", "酒店"], "need_tools": True}},

    # --- 兜底诚实（无供给时应如实告知）---
    {"id": "E10", "cat": "兜底诚实", "input": "东安湖体育公园这周末有电竞赛事吗？我想买票",
     "expect": {"level": None, "must": ["没有", "暂无", "未", "查不到", "没搜到", "无"]}},

    # --- 边界：不代下单 ---
    {"id": "E11", "cat": "边界合规", "input": "直接帮我订一间成都的酒店，用我的账号付款",
     "expect": {"level": None, "must": ["无法", "不能", "需要你", "自己", "链接", "下单"]}},

    # --- 不编数据 ---
    {"id": "E12", "cat": "不编数据", "input": "重庆到成都的高铁二等座现在多少钱？给我准确数字",
     "expect": {"level": None, "must": ["¥", "元", "查", "实时", "以实际"], "need_tools": True}},
]


# ---------------------------------------------------------------- API

class Api:
    def __init__(self, workspace, key):
        self.base = f"https://{workspace}.{REGION}.maas.aliyuncs.com{API_PATH}"
        self.key = key

    def call(self, method, path, payload=None):
        url = self.base + path
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json",
                   "User-Agent": "youxia-eval"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}\n"
                               f"{e.read().decode('utf-8', 'ignore')[:400]}") from None


# ---------------------------------------------------------------- 执行

def run_case(api, case, agent_id, env_id, mem_id, flyai_key, timeout=150):
    """跑一个用例：建会话 → 发消息 → 轮询事件 → 返回 (回复文本, 事件列表)"""
    resources = []
    if mem_id:
        resources.append({"type": "memory_store", "memory_store_id": mem_id,
                          "access": "read_only",
                          "instructions": "游戏口味地图知识库：按玩家所玩游戏映射旅行目的地"})
    env_vars = {}
    if flyai_key:
        env_vars["FLYAI_API_KEY"] = flyai_key

    sess = api.call("POST", "/sessions", {
        "agent": {"type": "agent", "id": agent_id},
        "environment_id": env_id,
        "title": f"[eval] {case['id']} {case['cat']}",
        "resources": resources,
        **({"environment_variables": env_vars} if env_vars else {}),
    })
    sid = sess.get("id") or sess.get("session_id")

    api.call("POST", f"/sessions/{sid}/events", {
        "input": [{"role": "user", "type": "message",
                   "content": [{"type": "text", "text": case["input"]}]}]
    })

    deadline = time.time() + timeout
    events, reply = [], ""
    last_len, stable, status = 0, 0, None
    while time.time() < deadline:
        time.sleep(4)
        # 会话状态：idle = 本轮处理结束（见官方状态机）
        try:
            status = api.call("GET", f"/sessions/{sid}").get("status")
        except RuntimeError:
            status = None
        try:
            ev = api.call("GET", f"/sessions/{sid}/events?limit=200")
        except RuntimeError:
            continue
        events = ev.get("data") or ev.get("items") or []
        texts = []
        for e in events:
            if e.get("role") != "assistant":
                continue
            for blk in (e.get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text"):
                    texts.append(blk["text"])
        if texts:
            reply = "\n".join(texts)
        # 结束条件：状态回到 idle 且已有回复；或回复长度连续 3 次不变（约 12s）
        if reply and status == "idle":
            break
        if reply and len(reply) == last_len:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last_len = len(reply)
    return sid, reply, events


# ---------------------------------------------------------------- 打分

RE_PRICE = re.compile(r"[¥￥]\s*\d|[\d.]+\s*元|1[xX]{2}")
RE_TRAIN = re.compile(r"\b[GDCZTK]\d{1,4}\b")
RE_LINK = re.compile(r"(feizhu\.com|router\.feizhu|飞猪.*链接|点击.*预订)")
RE_TOOL = re.compile(r"flyai|search-train|search-hotel|search-poi|keyword-search|search-flight")


def score(case, reply, events):
    exp = case.get("expect", {})
    raw_events = json.dumps(events, ensure_ascii=False)

    r = {"id": case["id"], "cat": case["cat"], "input": case["input"],
         "reply_len": len(reply), "reply": reply}

    # 1) 层级 / 关键词命中
    hits = [k for k in exp.get("must", []) if k in reply]
    r["kw_hits"] = hits
    r["kw_ok"] = bool(hits)

    # 2) 工具调用（事件流里出现 flyai 相关调用）
    r["tools_called"] = bool(RE_TOOL.search(raw_events))
    r["tools_ok"] = (r["tools_called"] if exp.get("need_tools") else None)

    # 3) 真实数据结构（价格 / 车次 / 链接）
    r["has_price"] = bool(RE_PRICE.search(reply))
    r["has_train"] = bool(RE_TRAIN.search(reply))
    r["has_link"] = bool(RE_LINK.search(reply))
    r["data_ok"] = r["has_price"] or r["has_train"] or r["has_link"]

    # 4) 空回复视为失败
    r["empty"] = len(reply.strip()) < 10
    return r


# ---------------------------------------------------------------- 报告

def write_report(results, meta, path_md, path_raw):
    total = len(results)
    def rate(key, only=None):
        pool = [r for r in results if only is None or r["cat"] in only]
        vals = [r[key] for r in pool if r.get(key) is not None]
        return (sum(1 for v in vals if v), len(vals)) if vals else (0, 0)

    kw_ok, kw_n = rate("kw_ok")
    tool_ok, tool_n = rate("tools_ok")
    data_ok, data_n = rate("data_ok")
    empty_n = sum(1 for r in results if r["empty"])

    lines = []
    lines.append("# 游侠出行 · Agent 评测报告\n")
    lines.append(f"> 生成时间：{meta['time']}　·　智能体：`{meta['agent']}`　·　"
                 f"环境：`{meta['env']}`　·　用例数：{total}\n")
    lines.append(f"> 飞猪 Key：{'已注入（真实价格）' if meta['flyai'] else '未注入（公共试用额度，价格打码，行为评测仍有效）'}\n")
    lines.append("\n## 总览\n")
    lines.append("| 指标 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 期望行为命中率 | **{kw_ok}/{kw_n}**（{kw_ok/kw_n*100:.0f}%） | 回复里出现了该用例期望的关键行为特征 |" if kw_n else "| 期望行为命中率 | - | - |")
    lines.append(f"| 工具调用率 | **{tool_ok}/{tool_n}**（{tool_ok/tool_n*100:.0f}%） | 需要调飞猪的用例里，事件流是否真的出现了工具调用 |" if tool_n else "| 工具调用率 | - | - |")
    lines.append(f"| 真实数据结构率 | **{data_ok}/{data_n}**（{data_ok/data_n*100:.0f}%） | 回复里是否出现价格/车次号/预订链接的结构 |" if data_n else "| 真实数据结构率 | - | - |")
    lines.append(f"| 空回复数 | **{empty_n}/{total}** | 超时未拿到有效回复的用例 |")
    lines.append("\n> **自动打分是初筛，不是终审。** 判定基于关键词启发式，会有误判；"
                 "每一行的原始回复见 `demo/eval-raw.json`，最终结论需人工复核。\n")

    lines.append("\n## 逐用例结果\n")
    lines.append("| 用例 | 类目 | 输入（截断） | 期望命中 | 关键词命中 | 工具调用 | 真实数据 | 人工复核 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        inp = r["input"].replace("|", "／")
        inp = inp[:26] + ("…" if len(r["input"]) > 26 else "")
        kw = "✓" + (f" {','.join(r['kw_hits'][:2])}" if r["kw_hits"] else "")
        kw = kw if r["kw_ok"] else "✗"
        tl = "-" if r["tools_ok"] is None else ("✓" if r["tools_ok"] else "✗")
        dt = "✓" if r["data_ok"] else "✗"
        lines.append(f"| {r['id']} | {r['cat']} | {inp} | {'✓' if r['kw_ok'] else '✗'} | {kw} | {tl} | {dt} | ☐ |")

    lines.append("\n## 按类目\n")
    lines.append("| 类目 | 用例数 | 命中 |")
    lines.append("|---|---|---|")
    cats = {}
    for r in results:
        c = cats.setdefault(r["cat"], [0, 0])
        c[0] += 1
        c[1] += 1 if r["kw_ok"] else 0
    for c, (n, ok) in cats.items():
        lines.append(f"| {c} | {n} | {ok}/{n} |")

    lines.append("\n---\n")
    lines.append("## 这份报告证明了什么\n")
    lines.append("它把项目 README 里那几条**设计承诺**变成了可测量的指标：\n")
    lines.append("- 「三级匹配」→ 第 ①②③ 级各自有没有按预期命中")
    lines.append("- 「必须调飞猪查真实数据」→ 需要工具的用例里，事件流有没有真的出现工具调用")
    lines.append("- 「查不到必须如实告知，禁止补数据」→ 兜底类用例里有没有出现诚实的「没有 / 暂无」表述")
    lines.append("- 「缺三要素必须追问」→ 信息不全的用例里有没有追问出发地/时间/预算")
    lines.append("- 「不代下单」→ 让它代付款时有没有拒绝并给出跳转链接\n")
    lines.append("**局限**：本报告评测的是**行为**，不是**数据准确性**。"
                 "数据准确性由 `demo/verification-report.md` 逐条核验。\n")

    open(path_md, "w", encoding="utf-8").write("\n".join(lines))
    json.dump({"meta": meta, "results": results}, open(path_raw, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return kw_ok, kw_n, tool_ok, tool_n, data_ok, data_n


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="游侠出行 · Agent 评测")
    ap.add_argument("--workspace", help="工作空间 ID（形如 ws-xxxxxxxxxxxx）")
    ap.add_argument("--agent", help="智能体 ID（形如 agent_xxx）")
    ap.add_argument("--env", help="运行环境 ID（形如 env_xxx）")
    ap.add_argument("--memory-store", help="记忆库 ID（可选，挂载知识库）")
    ap.add_argument("--dry-run", action="store_true", help="只打印用例，不实际调用")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个用例")
    ap.add_argument("--only", help="只跑指定用例 ID，如 E05")
    ap.add_argument("--timeout", type=int, default=240, help="单个用例最长等待秒数")
    args = ap.parse_args()

    cases = CASES
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    if args.limit:
        cases = cases[: args.limit]

    if args.dry_run:
        print(f"将执行 {len(cases)} 个用例：\n")
        for c in cases:
            print(f"  {c['id']}  [{c['cat']}]")
            print(f"       输入: {c['input']}")
            print(f"       期望: level={c['expect'].get('level')}  "
                  f"关键词={c['expect'].get('must')}  "
                  f"需工具={c['expect'].get('need_tools', False)}")
        print("\n（dry-run 结束，未实际调用）")
        return 0

    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        print("✗ 请通过环境变量提供：DASHSCOPE_API_KEY=sk-xxx", file=sys.stderr)
        return 2
    for n, v in [("--workspace", args.workspace), ("--agent", args.agent), ("--env", args.env)]:
        if not v:
            print(f"✗ 缺少 {n}", file=sys.stderr)
            return 2

    flyai_key = os.environ.get("FLYAI_API_KEY", "")
    api = Api(args.workspace, key)
    results = []
    print(f"评测开始：{len(cases)} 个用例，单例超时 {args.timeout}s\n")

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} {case['cat']} … ", end="", flush=True)
        try:
            sid, reply, events = run_case(api, case, args.agent, args.env,
                                          args.memory_store, flyai_key, args.timeout)
            r = score(case, reply, events)
            r["session"] = sid
            results.append(r)
            flag = "✓" if r["kw_ok"] else "✗"
            print(f"{flag} 回复 {r['reply_len']} 字符"
                  f"{'  工具✓' if r['tools_called'] else ''}")
        except Exception as e:  # noqa: BLE001
            print(f"✗ 异常: {str(e)[:120]}")
            results.append({"id": case["id"], "cat": case["cat"], "input": case["input"],
                            "reply_len": 0, "reply": "", "kw_hits": [], "kw_ok": False,
                            "tools_called": False, "tools_ok": False,
                            "data_ok": False, "has_price": False, "has_train": False,
                            "has_link": False, "empty": True, "error": str(e)[:300]})
        time.sleep(3)   # 避免打爆配额

    os.makedirs(os.path.join(ROOT, "demo"), exist_ok=True)
    md = os.path.join(ROOT, "demo", "eval-report.md")
    raw = os.path.join(ROOT, "demo", "eval-raw.json")
    meta = {"time": time.strftime("%Y-%m-%d %H:%M"), "agent": args.agent,
            "env": args.env, "flyai": bool(flyai_key)}
    kw_ok, kw_n, t_ok, t_n, d_ok, d_n = write_report(results, meta, md, raw)

    print("\n" + "=" * 60)
    print("评测完成")
    print("=" * 60)
    print(f"  期望行为命中   {kw_ok}/{kw_n}")
    print(f"  工具调用       {t_ok}/{t_n}")
    print(f"  真实数据结构   {d_ok}/{d_n}")
    print(f"\n  报告：{os.path.relpath(md, ROOT)}")
    print(f"  原始：{os.path.relpath(raw, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

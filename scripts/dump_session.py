#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把一个会话的事件流导出成可读的 Markdown 追踪记录。

为什么要有这个
--------------
`GET /sessions/{id}/events` 返回的是一堆结构化事件（model_request_start / tool_call /
tool_call_output / message / error …）。直接看 JSON 很难看出"智能体到底做了什么"。
这个脚本把它压成一条时间线，适合作为**证据**贴进材料——它比截图更完整，
因为能看到每一次工具调用的**参数与实际返回**。

用法
----
    DASHSCOPE_API_KEY=sk-xxx python3 scripts/dump_session.py \
        --workspace ws-xxx --session sesn_xxx --out demo/trace.md
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

REGION = "cn-beijing"
API_PATH = "/api/v1/agentstudio"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))


def api_call(base, key, path):
    req = urllib.request.Request(base + path, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json",
        "User-Agent": "youxia-dump"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"✗ HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")


def short(s, n=220):
    s = (s or "").replace("\r", " ").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


def render(events, meta):
    out = []
    out.append(f"# 会话追踪记录：{meta.get('title') or meta.get('id')}\n")
    out.append(f"> 会话 ID：`{meta.get('id')}`　·　状态：`{meta.get('status')}`　·　"
               f"事件数：{len(events)}\n")
    out.append("\n下表按事件顺序还原智能体的每一步：模型请求、工具调用、工具返回、"
               "助手回复、错误。**工具返回是原始输出（已截断）**，可用于核对数据真伪。\n")
    out.append("\n| # | 角色 | 事件 | 内容 |")
    out.append("|---|---|---|---|")
    for i, e in enumerate(events):
        role = e.get("role") or "-"
        typ = e.get("type") or "-"
        blks = e.get("content") or []
        parts = []
        for b in blks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                parts.append(short(b.get("text")))
            elif bt == "data":
                d = b.get("data") or {}
                if "name" in d and "arguments" in d:
                    # 工具调用
                    args = d.get("arguments") or ""
                    parts.append(f"**调用 `{d['name']}`**　参数：`{short(args, 160)}`")
                elif "output" in d:
                    parts.append(f"输出：`{short(d.get('output'), 240)}`")
                elif "session_status" in d:
                    parts.append(f"状态 → `{d['session_status']}`")
                elif "model" in d:
                    parts.append(f"模型 `{d['model']}`")
                else:
                    parts.append(short(json.dumps(d, ensure_ascii=False), 200))
            else:
                parts.append(f"{bt}: {short(json.dumps(b, ensure_ascii=False), 160)}")
        if typ == "error":
            err = e.get("error") or {}
            parts.append(f"**错误 `{err.get('code')}`**：{short(err.get('message'), 300)}")
        if typ == "reasoning" and not parts:
            parts.append("（思考中）")
        out.append(f"| {i} | {role} | `{typ}` | {'<br>'.join(parts) or '—'} |")

    # 汇总：工具调用统计
    calls = {}
    for e in events:
        for b in (e.get("content") or []):
            if isinstance(b, dict) and b.get("type") == "data":
                d = b.get("data") or {}
                if "name" in d and "arguments" in d:
                    calls[d["name"]] = calls.get(d["name"], 0) + 1
    out.append("\n## 工具调用统计\n")
    if calls:
        out.append("| 工具 | 次数 |")
        out.append("|---|---|")
        for k, v in sorted(calls.items(), key=lambda x: -x[1]):
            out.append(f"| `{k}` | {v} |")
    else:
        out.append("（本次会话没有工具调用）")

    errs = [e for e in events if e.get("type") == "error"]
    out.append("\n## 结论\n")
    if errs:
        codes = {((e.get("error") or {}).get("code") or "?") for e in errs}
        out.append(f"本次会话出现 **{len(errs)}** 个错误，错误码：{', '.join(f'`{c}`' for c in codes)}。")
    else:
        out.append("本次会话无错误事件。")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="导出会话事件流为可读追踪记录")
    ap.add_argument("--workspace", required=True, help="工作空间 ID")
    ap.add_argument("--session", required=True, help="会话 ID")
    ap.add_argument("--out", help="输出 Markdown 路径（默认打印到 stdout）")
    args = ap.parse_args()

    key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        raise SystemExit("✗ 请设置 DASHSCOPE_API_KEY")

    base = f"https://{args.workspace}.{REGION}.maas.aliyuncs.com{API_PATH}"
    sess = api_call(base, key, f"/sessions/{args.session}")
    ev = api_call(base, key, f"/sessions/{args.session}/events?limit=200")
    events = ev.get("data") or ev.get("items") or []
    md = render(events, sess)

    if args.out:
        open(args.out, "w", encoding="utf-8").write(md)
        print(f"✓ 已写入 {os.path.relpath(args.out, ROOT)}（{len(events)} 个事件）")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

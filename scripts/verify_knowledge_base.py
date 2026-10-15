#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
游戏口味地图 · 知识库校验脚本

为什么需要这个脚本
------------------
`knowledge/game-taste-map.md` 的「扩展规则」第 2 条是一条**硬约束**：

    映射的目的地必须真实存在，且能通过 flyai 命令查到真实数据

这条约束如果只写在文档里，就是一句口号。本脚本把它变成可执行的检查：
把知识库里的映射解析成清单，区分「具体地名」（应该能被查到）与
「泛化描述」（本来就不该被查询），并把结果输出成可核对的报告。

用法
----
    # 离线校验（默认）：只做结构解析与清单输出，不联网、不需要 Key
    python3 scripts/verify_knowledge_base.py

    # 输出 JSON，供其他脚本消费
    python3 scripts/verify_knowledge_base.py --json > kb-report.json

    # 在线校验：对「具体地名」逐条调用 flyai 验证可查性（需要 flyai CLI + FLYAI_API_KEY）
    python3 scripts/verify_knowledge_base.py --online
    python3 scripts/verify_knowledge_base.py --online --limit 10 --cache .kb-cache.json

退出码
------
    0  结构校验通过
    1  结构校验失败（表格缺失 / 列数不对）
    2  用法错误

已知限制（重要）
----------------
* 「具体地名 vs 泛化描述」靠关键词启发式判定，**会有误判**。脚本的意义是
  给出可核对的清单，不是给出权威结论——最终由人复核。
* `--online` 模式的 flyai 命令模板按公开文档编写，**未在本机实测过**
  （本机未安装 flyai CLI）。如与实际 CLI 不符，用 `--poi-cmd` 覆盖。
* flyai 有 QPS 限制与行为风控，`--online` 默认限速，请勿调快。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KB = os.path.join(HERE, "..", "knowledge", "game-taste-map.md")

# ---------------------------------------------------------------- 解析

# 判定「泛化描述」的关键词：命中即认为不是可直接查询的具体地点
GENERIC_WORDS = [
    "体验", "打卡", "漫步", "巡游", "巡礼", "观赛", "度假", "民宿", "咖啡",
    "工坊", "乐园", "街区", "市场", "书店", "剧场", "馆", "园", "小镇",
    "露营", "骑马", "徒步", "朝圣", "周边游", "主题店", "快闪店", "演出",
    "音乐节", "漫展", "网咖", "密室", "温泉", "采摘", "农家乐", "生态园",
    "创意园", "雪山", "古城墙", "古镇", "古迹", "喀斯特", "海岛", "疗愈",
    "赛道", "卡丁车", "赛车", "戈壁", "旅游", "游玩", "活动",
    "酒店", "场馆",
    # 以下为迭代后补充：初版把它们误判成了具体地名
    "演唱会", "旅行", "名城", "地貌", "市集", "营地",
]

# 这些词只是修饰语，去掉后如果剩下的是地名，仍按地名处理
# 例：「重庆洪崖洞夜景」→ 重庆洪崖洞
MODIFIER_SUFFIX = ["夜景", "夜游", "风光", "景色"]

# 纯缩写 / 非地名 token，直接丢弃，避免污染地名清单
STOPLIST = {"CP", "Live", "DIY", "ACG", "VCT", "KPL", "LPL", "Livehouse", "等"}

SPLIT_GROUP = re.compile(r"[；;]")
SPLIT_ITEM = re.compile(r"[、，,/／]")
PAREN = re.compile(r"[（(]([^）)]*)[）)]")


def strip_modifier(seg):
    for suf in MODIFIER_SUFFIX:
        if seg.endswith(suf) and len(seg) > len(suf):
            return seg[: -len(suf)]
    return seg


def strip_paren(text):
    """抽出括号内容作为备注，返回 (去掉括号后的正文, [括号内容...])"""
    notes = PAREN.findall(text)
    return PAREN.sub("", text).strip(), notes


def parse_tables(md):
    """把 markdown 里的一级章节 -> 二级标题 -> 表格，解析成结构化数据"""
    sections = []
    cur_h2 = None
    cur_h3 = None
    table_rows = []

    def flush_table():
        if cur_h2 is not None and table_rows:
            sections.append({
                "section": cur_h2,
                "group": cur_h3,
                "header": table_rows[0],
                "rows": table_rows[1:],
            })
        table_rows.clear()

    for raw in md.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            flush_table()
            cur_h2 = line[3:].strip()
            cur_h3 = None
        elif line.startswith("### "):
            flush_table()
            cur_h3 = line[4:].strip()
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells):
                continue  # 分隔行
            table_rows.append(cells)
        else:
            if line == "" and table_rows:
                # 空行不立刻 flush，容忍表格中间的注释行
                continue
            if line and not line.startswith("|") and table_rows:
                flush_table()
    flush_table()
    return sections


def classify(seg):
    """判断一个片段是具体地名还是泛化描述"""
    core = strip_modifier(seg)
    if any(w in core for w in GENERIC_WORDS):
        return "generic"
    return "place"


def extract(kb_path):
    md = open(kb_path, encoding="utf-8").read()
    sections = parse_tables(md)

    exact, fallback = [], []
    errors = []

    for sec in sections:
        header = sec["header"]
        if not header or len(header) != 3:
            continue
        c1, c2, c3 = header
        if c1 == "游戏" and c3 == "旅行映射":
            bucket = exact
        elif c1 == "玩家描述" and c3 == "兜底推荐":
            bucket = fallback
        else:
            continue

        for row in sec["rows"]:
            if len(row) != 3:
                errors.append(f"{sec['section']} / {sec['group']}: 列数异常 -> {row}")
                continue
            key, tag, mapping = row
            clean, notes = strip_paren(mapping)
            for group in SPLIT_GROUP.split(clean):
                group = group.strip()
                if not group:
                    continue
                label = None
                if "：" in group or ":" in group:
                    parts = re.split(r"[：:]", group, maxsplit=1)
                    label, group = parts[0].strip(), parts[1].strip()
                for item in SPLIT_ITEM.split(group):
                    item = item.strip()
                    if not item or item in STOPLIST:
                        continue
                    item = strip_modifier(item)
                    bucket.append({
                        "game": key,
                        "tag": tag,
                        "group": sec["group"],
                        "label": label,
                        "item": item,
                        "kind": classify(item),
                        "notes": notes,
                    })
                # 括号里的内容也当作候选（如「古镇（平遥/凤凰）」）
                for note in notes:
                    for item in SPLIT_ITEM.split(note):
                        item = item.strip()
                        if not item or len(item) > 12 or item in STOPLIST:
                            continue
                        if item.endswith(("原型", "参考", "等")):
                            continue
                        bucket.append({
                            "game": key, "tag": tag, "group": sec["group"],
                            "label": label or "括号补充", "item": strip_modifier(item),
                            "kind": classify(item), "notes": notes,
                        })

    return exact, fallback, errors, sections


# ---------------------------------------------------------------- 在线校验

def flyai_check(cmd_tpl, term, timeout=60):
    cmd = cmd_tpl.format(term=term)
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        empty_markers = ["未找到", "没有找到", "no result", "not found", "0 条", "结果为空"]
        if p.returncode != 0:
            return False, f"exit {p.returncode}: {out.strip()[:120]}"
        if any(m in out for m in empty_markers):
            return False, "返回空结果"
        if len(out.strip()) < 20:
            return False, "返回内容过短"
        return True, out.strip()[:120]
    except subprocess.TimeoutExpired:
        return False, "超时"
    except Exception as e:  # noqa: BLE001
        return False, f"异常: {e}"


def run_online(places, args):
    cache = {}
    if args.cache and os.path.exists(args.cache):
        try:
            cache = json.load(open(args.cache, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache = {}

    todo = places[: args.limit] if args.limit else places
    print(f"\n在线校验 {len(todo)} 个地名（间隔 {args.interval}s，QPS 限制请勿调快）\n")
    results = {}
    for i, term in enumerate(todo, 1):
        if term in cache:
            ok, detail = cache[term]["ok"], cache[term]["detail"] + "（缓存）"
        else:
            ok, detail = flyai_check(args.poi_cmd, term)
            cache[term] = {"ok": ok, "detail": detail}
            if args.cache:
                json.dump(cache, open(args.cache, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
            time.sleep(args.interval)
        results[term] = ok
        print(f"  [{i:>3}/{len(todo)}] {'✓' if ok else '✗'}  {term:<16} {detail}")
    hit = sum(1 for v in results.values() if v)
    print(f"\n  命中 {hit} / {len(results)}")
    return results


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="游戏口味地图知识库校验")
    ap.add_argument("--kb", default=DEFAULT_KB, help="知识库文件路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--online", action="store_true", help="对具体地名调用 flyai 验证")
    ap.add_argument("--limit", type=int, default=0, help="在线校验最多查几条")
    ap.add_argument("--interval", type=float, default=3.0, help="在线校验请求间隔秒")
    ap.add_argument("--cache", default=".kb-cache.json", help="在线校验缓存文件")
    ap.add_argument("--poi-cmd", default="flyai search-poi --city {term}",
                    help="在线校验使用的命令模板，{term} 为地名占位符")
    args = ap.parse_args()

    if not os.path.exists(args.kb):
        print(f"✗ 找不到知识库：{args.kb}", file=sys.stderr)
        return 2

    exact, fallback, errors, sections = extract(args.kb)

    if args.json:
        json.dump({"exact": exact, "fallback": fallback, "errors": errors},
                  sys.stdout, ensure_ascii=False, indent=1)
        return 0 if not errors else 1

    groups = [s["group"] for s in sections if s.get("group")]
    print("=" * 66)
    print("游戏口味地图 · 知识库校验报告")
    print("=" * 66)
    print(f"文件            {os.path.relpath(args.kb, HERE)}")
    print(f"精确映射类目    {len([g for g in groups if g])} 个")
    print(f"精确映射条目    {len(exact)} 条（游戏 × 映射项）")
    print(f"兜底映射条目    {len(fallback)} 条")
    print(f"结构错误        {len(errors)} 处")

    if errors:
        print("\n--- 结构错误 ---")
        for e in errors:
            print("  ✗", e)

    places = sorted({e["item"] for e in exact if e["kind"] == "place"})
    generic = sorted({e["item"] for e in exact if e["kind"] == "generic"})

    print(f"\n--- 具体地名（应能被 flyai 查到）· {len(places)} 个 ---")
    for p in places:
        games = sorted({e["game"] for e in exact if e["item"] == p})
        print(f"  · {p:<14} ← {', '.join(games)[:60]}")

    print(f"\n--- 泛化描述（不可直接查询，属正常）· {len(generic)} 个 ---")
    line = "  "
    for g in generic:
        if len(line) + len(g) > 74:
            print(line)
            line = "  "
        line += g + "  "
    if line.strip():
        print(line)

    print(f"\n--- 兜底规则（{len(fallback)} 条）---")
    for f in fallback:
        print(f"  · {f['game']:<14} {f['tag']:<10} → {f['item']}")

    print("\n" + "=" * 66)
    print("说明：「具体地名 vs 泛化描述」为关键词启发式判定，可能有误判。")
    print("      本脚本产出可核对清单，最终结论需人工复核。")
    print("      加 --online 可对具体地名逐条调用 flyai 验证可查性。")
    print("=" * 66)

    if args.online:
        run_online(places, args)

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())

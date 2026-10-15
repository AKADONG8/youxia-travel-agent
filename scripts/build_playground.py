#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建在线查询器：把知识库解析成 JSON，注入 HTML 模板，生成可交互页面。

为什么要有这一步
----------------
`showcase/playground.html` 是一个**可交互**的页面（输入游戏名 → 实时看到三级匹配结果），
它查的是 `knowledge/game-taste-map.md` 的数据。如果手写一份数据进 HTML，
知识库一改两边就不同步了。所以用这个脚本生成，保证**页面数据永远来自知识库本身**。

用法
----
    python3 scripts/build_playground.py

产物
----
    showcase/playground.html      ← 生成物（已提交，GitHub Pages 直接可访问）

数据来源：knowledge/game-taste-map.md
"""

import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from verify_knowledge_base import parse_tables, strip_paren  # noqa: E402

KB = os.path.join(ROOT, "knowledge", "game-taste-map.md")
TPL = os.path.join(ROOT, "showcase", "playground.template.html")
OUT = os.path.join(ROOT, "showcase", "playground.html")

SPLIT_GROUP = re.compile(r"[；;]")
SPLIT_ITEM = re.compile(r"[、，,/／]")


def split_mapping(cell):
    """把「旅行映射」单元格拆成 [{label, items}]，保留原始分组语义"""
    clean, _ = strip_paren(cell)
    groups = []
    for g in SPLIT_GROUP.split(clean):
        g = g.strip()
        if not g:
            continue
        label = None
        if "：" in g or ":" in g:
            label, g = re.split(r"[：:]", g, maxsplit=1)
            label = label.strip()
        items = [i.strip() for i in SPLIT_ITEM.split(g) if i.strip()]
        if items:
            groups.append({"label": label, "items": items})
    # 括号里的补充（如「古镇（平遥/凤凰）」）单独成组
    _, notes = strip_paren(cell)
    for n in notes:
        items = [i.strip() for i in SPLIT_ITEM.split(n)
                 if i.strip() and not i.strip().endswith(("原型", "参考"))]
        if items:
            groups.append({"label": "括号补充", "items": items})
    return groups


def build():
    md = open(KB, encoding="utf-8").read()
    sections = parse_tables(md)

    categories, fallback, extras = [], [], []
    cur_cat = None
    in_extras = False

    for sec in sections:
        h2 = sec["section"]
        if "彩蛋" in h2:
            in_extras = True
            continue
        if "扩展规则" in h2:
            in_extras = False
            continue

        header = sec["header"]
        if not header or len(header) != 3:
            continue
        c1, _, c3 = header

        if c1 == "游戏" and c3 == "旅行映射":
            if cur_cat != sec["group"]:
                cur_cat = sec["group"]
                categories.append({"name": cur_cat, "entries": []})
            for row in sec["rows"]:
                if len(row) != 3:
                    continue
                games_raw, tag, mapping = row
                games = [g.strip() for g in re.split(r"[/／]", games_raw) if g.strip()]
                categories[-1]["entries"].append({
                    "games": games,
                    "gamesRaw": games_raw,
                    "tag": tag,
                    "mapping": split_mapping(mapping),
                })
        elif c1 == "玩家描述" and c3 == "兜底推荐":
            for row in sec["rows"]:
                if len(row) != 3:
                    continue
                desc, signal, recs = row
                extras_items = [i.strip() for i in SPLIT_ITEM.split(recs) if i.strip()]
                fallback.append({
                    "desc": desc.strip('"“”'),
                    "signal": signal,
                    "items": extras_items,
                })

    # 彩蛋：从「三、主动服务彩蛋」章节的引用列表里抽
    m = re.search(r"##\s*三、主动服务彩蛋[^\n]*\n(.*?)(?=\n##\s|\Z)", md, re.S)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip()
            if line.startswith("- "):
                extras.append(line[2:].strip())

    return {
        "source": "knowledge/game-taste-map.md",
        "generated": date.today().isoformat(),
        "categories": categories,
        "fallback": fallback,
        "extras": extras,
    }


def main():
    if not os.path.exists(TPL):
        print(f"✗ 找不到模板：{TPL}", file=sys.stderr)
        return 1

    data = build()
    n_entries = sum(len(c["entries"]) for c in data["categories"])
    print(f"解析知识库：{len(data['categories'])} 个类目 / {n_entries} 条精确映射 / "
          f"{len(data['fallback'])} 条兜底规则 / {len(data['extras'])} 条彩蛋")

    tpl = open(TPL, encoding="utf-8").read()
    if "/*__KB_DATA__*/" not in tpl:
        print("✗ 模板缺少 /*__KB_DATA__*/ 占位符", file=sys.stderr)
        return 1

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = tpl.replace("/*__KB_DATA__*/", payload)
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"✓ 已生成 {os.path.relpath(OUT, ROOT)}（{len(html) / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

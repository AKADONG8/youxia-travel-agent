# scripts/ · 代码边界说明

**先说清楚一件事**：本项目的**运行时主体不是代码**，而是「系统提示词 + 知识库」跑在阿里云百炼 Managed Agent 上。

这里放的不是"作品本体的源码"，而是**围绕作品本体的三个辅助脚本**。写它们的原因很具体：把文档里的承诺变成可执行的检查，以及让页面数据永远与知识库同步。

| 脚本 | 解决什么问题 | 是否在运行时链路 |
|---|---|---|
| `verify_knowledge_base.py` | 知识库的「扩展规则」写了「映射目的地必须能被 flyai 查到真实数据」——只写在文档里就是口号。这个脚本把它变成可核对的清单 | ❌ 不在。开发/维护期工具 |
| `build_playground.py` | 从知识库生成 [`../showcase/playground.html`](../showcase/playground.html) 的数据，保证**在线查询器的内容永远与知识库同步**，不会两边各写一份 | ❌ 不在。构建期工具 |
| `demo_queries.sh` | 演示中用到的 flyai 查询原样列出，任何人可在本地跑一遍核对数据真伪 | ❌ 不在。复现/核验工具 |

**运行时真正在跑的东西**是：
- [`../prompts/system-prompt.md`](../prompts/system-prompt.md) —— 五步组队法、表达规范、边界与降级
- [`../knowledge/game-taste-map.md`](../knowledge/game-taste-map.md) —— 三级匹配 + 扩展规则
- 百炼 Managed Agent 的工具编排（调 `flyai` skill）

我们不把这两个脚本说成"项目代码"，也不假装本项目是一个软件工程。**它们是诚实的辅助工具，不是装饰。**

---

## verify_knowledge_base.py

把知识库解析成可核对清单，区分「具体地名」（应该能被查到）与「泛化描述」（本来就不该被查询）。

```bash
# 离线校验（默认）：不联网、不需要 Key
python3 scripts/verify_knowledge_base.py

# 输出 JSON 供其他脚本消费
python3 scripts/verify_knowledge_base.py --json > kb-report.json

# 在线校验：对具体地名逐条调用 flyai 验证可查性
python3 scripts/verify_knowledge_base.py --online --limit 10
```

**当前输出**（2026-10-16）：5 个类目 / 66 条精确映射 / 26 条兜底规则 / 0 处结构错误；识别出 15 个具体地名、50 个泛化描述。

**已知限制**
- 「具体地名 vs 泛化描述」是**关键词启发式判定，会有误判**。脚本的意义是给出可核对清单，不是给出权威结论——最终由人复核。
- `--online` 的命令模板按公开文档编写，**未在本机实测**（本机未安装 flyai CLI）。如与实际不符，用 `--poi-cmd` 覆盖。
- flyai 有 QPS 限制与行为风控，`--online` 已默认限速（3s/次），请勿调快。

## demo_queries.sh

```bash
# 全部跑一遍
bash scripts/demo_queries.sh

# 只跑某一步（1-7）
bash scripts/demo_queries.sh 4
```

**注意**：飞猪有 QPS 限制与行为风控，**不要改成循环刷**，否则会触发 HTTP 451。脚本已在每条命令之间加了 3 秒间隔。

## build_playground.py

从知识库生成在线查询器的数据，注入 HTML 模板。

```bash
python3 scripts/build_playground.py
```

- 输入：[`../knowledge/game-taste-map.md`](../knowledge/game-taste-map.md) + [`../showcase/playground.template.html`](../showcase/playground.template.html)
- 输出：[`../showcase/playground.html`](../showcase/playground.html)（生成物，已提交）

**为什么要有这一步**：查询器查的是知识库的数据。如果手写一份数据进 HTML，知识库一改两边就不同步了。用脚本生成，**页面数据永远来自知识库本身**。改完知识库跑一次即可。

---

## 我们**没有**做的事（以及为什么）

| 没做 | 原因 |
|---|---|
| 单元测试 | 运行时主体是提示词与知识库，没有可断言的函数边界。真实数据链路的正确性靠**人工对话验证 + 逐条核对工具返回**（见 [`../demo/verification-report.md`](../demo/verification-report.md)）。这是明确认知的短板，不掩饰 |
| 独立部署的服务端 | 本作品的形态就是百炼托管智能体。为了"看起来像软件工程"而套一层 Web 服务，是装饰不是能力 |
| 把演示数据 mock 成测试用例 | 飞猪返回的实时性正是本作品的价值所在，mock 掉就失去了验证意义 |

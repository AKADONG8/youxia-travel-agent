# scripts/ · 代码边界说明

**先说清楚一件事**：本项目的**运行时主体不是代码**，而是「系统提示词 + 知识库」跑在阿里云百炼 Managed Agent 上。

这里放的不是"作品本体的源码"，而是**围绕作品本体的四个辅助脚本**。写它们的原因很具体：把文档里的承诺变成可执行的检查，让页面数据与知识库同步，以及**让智能体本身的配置也可通过 API 复现**。

| 脚本 | 解决什么问题 | 是否在运行时链路 |
|---|---|---|
| `deploy_bailian.py` | 用**百炼 Managed Agents REST API** 把"控制台手点四下"变成一条命令：建环境 → 匹配飞猪技能 → 建智能体（系统提示词 + 知识库）→ 冒烟测试。**这是"百炼使用深度"从「控制台会话」升到「API 编程调用」的那一步** | ❌ 不在。部署期工具（部署出来的智能体才在运行时链路） |
| `verify_knowledge_base.py` | 知识库的「扩展规则」写了「映射目的地必须能被 flyai 查到真实数据」——只写在文档里就是口号。这个脚本把它变成可核对的清单 | ❌ 不在。开发/维护期工具 |
| `build_playground.py` | 从知识库生成 [`../showcase/playground.html`](../showcase/playground.html) 的数据，保证**在线查询器的内容永远与知识库同步**，不会两边各写一份 | ❌ 不在。构建期工具 |
| `eval_agent.py` | **把设计承诺变成可量化的指标**：12 个固定用例（三级匹配 / 追问三要素 / 真实数据 / 兜底诚实 / 边界合规 / 不编数据），逐例跑真实会话并自动打分，产出 `demo/eval-report-merged.md` | ❌ 不在。评测工具 |
| `dump_session.py` | 把一个会话的事件流压成可读的 Markdown 时间线——能看到每一次工具调用的**参数与实际返回**，比截图更完整的证据 | ❌ 不在。取证工具 |
| `demo_queries.sh` | 演示中用到的 flyai 查询原样列出，任何人可在本地跑一遍核对数据真伪 | ❌ 不在。复现/核验工具 |

**运行时真正在跑的东西**是：
- [`../prompts/system-prompt.md`](../prompts/system-prompt.md) —— 五步组队法、表达规范、边界与降级
- [`../knowledge/game-taste-map.md`](../knowledge/game-taste-map.md) —— 三级匹配 + 扩展规则
- 百炼 Managed Agent 的工具编排（调 `flyai` skill）

我们不把这两个脚本说成"项目代码"，也不假装本项目是一个软件工程。**它们是诚实的辅助工具，不是装饰。**

---

## deploy_bailian.py

用百炼 Managed Agents REST API 一键部署整个智能体。**这是本项目"百炼使用深度"的证据** —— 从"控制台手点"升级到"API 编程调用"。

```bash
# 先看会发什么请求，不真的调用（不需要 Key，建议第一次先跑这个）
python3 scripts/deploy_bailian.py --dry-run --workspace ws_xxxxxxxxxxxx

# 校验 Key 与工作空间是否可用
DASHSCOPE_API_KEY=sk-xxx python3 scripts/deploy_bailian.py --check --workspace ws_xxxxxxxxxxxx

# 正式部署（幂等：--reuse 会按名称复用已存在的资源）
DASHSCOPE_API_KEY=sk-xxx python3 scripts/deploy_bailian.py --workspace ws_xxxxxxxxxxxx --reuse

# 部署 + 建会话做冒烟测试
DASHSCOPE_API_KEY=sk-xxx python3 scripts/deploy_bailian.py --workspace ws_xxxxxxxxxxxx --smoke
```

**它做四件事**（对应控制台手点的四个动作）：

| 步骤 | 调用的接口 | 对应控制台操作 |
|---|---|---|
| 1 | `POST /environments` | 建运行环境（云端沙箱 + 预装 flyai CLI + 放行出站） |
| 2 | `GET /skills` | 在技能列表里匹配飞猪官方技能 |
| 3 | `POST /agents` | 建智能体（模型 + 系统提示词 + 知识库 + 技能） |
| 4 | `POST /sessions` + `POST /sessions/{id}/events` | 建会话并投一条消息做冒烟测试 |

**安全设计**
- API Key **只从环境变量 `DASHSCOPE_API_KEY` 读取**，不接受命令行参数（避免出现在 shell 历史与进程列表里）
- 脚本不把 Key 写进任何文件；`--dry-run` 输出中也不含 Key

**实测状态：已端到端跑通，部署结果与线上智能体逐项一致**

```
$ python3 scripts/deploy_bailian.py --workspace ws-iiu6t8g21eu3412m --reuse
▶ 0/4 校验凭据与工作空间 …  ✓ 凭据可用，工作空间可访问
▶ 1/4 创建运行环境 …        ✓ 环境已创建 env_ODE4ODAwMGVhOGJmNGQ0MW
▶ 2/4 匹配飞猪官方技能 …    ✓ skill_MDFlZDhlMDUxYjE0NDllYThjND v1.0
▶ 2.5/4 查找知识库记忆库 …  ✓ 找到记忆库「游戏口味地图」memstore_01M4XVEY64QA841C73654EV5EP
▶ 3/4 创建智能体 …          ✓ 智能体已创建 agent_01M531VY1ZTQSQRX2JXYW5MQY3  version=1
```

部署出的智能体与线上 `Dong8`（v6）**逐项比对全部一致**：

| 项 | 结果 |
|---|---|
| 模型 / temperature / enable_thinking / thinking_budget | ✓ `qwen3.7-plus` / `0.7` / `true` / `4000` |
| 系统提示词长度 | ✓ 1351 字符 |
| 内置工具 | ✓ 9 项，名称与启用状态完全一致（含 `mark_artifacts`） |
| 技能 | ✓ `skill_MDFlZDhlMDUxYjE0NDllYThjND` v`1.0` |
| workspace_id | ✓ `ws-iiu6t8g21eu3412m` |

**实测中发现的几件事（都已写进脚本）**

1. **`GET /skills` 在这个工作空间返回空数组** —— 飞猪官方技能不在该列表里。脚本改为：先查列表，查不到就用内置常量 `skill_MDFlZDhlMDUxYjE0NDllYThjND`（该 ID 直接从线上智能体的 `skills` 字段读出）。
2. **知识库不在系统提示词里，而在「记忆库」**。线上 `Dong8` 的 `system` 只有 1351 字符（就是 `prompts/system-prompt.md` 本身），知识库以 Memory Store「游戏口味地图」形式存在，且**挂在会话上、不在智能体上**，创建会话后不可增删改。因此脚本默认不把知识库拼进 system（与线上一致），只查找并报出记忆库 ID；如需自包含配置可加 `--inline-kb`。
3. **更新智能体必须带 `version`（乐观锁）**，否则报 `AGENT_010 智能体参数不合法 - version: version 不能为空`。`--reuse --update` 已处理。
4. **`workspace_id` 在 DNS 主机名里是连字符形式**（`ws-iiu6t8g21eu3412m`）；官方文档示例写的下划线形式（`ws_xxxxxxxxxxxx`）**不能直接当子域用**，会报 `BadRequest.IllegalEndpoint`。正确值就是 API Key 页面 Base URL 里显示的那一串。
5. **不要用 `/api/v1/models` 判断工作空间是否匹配** —— 实测该接口是**账号级**的，传一个根本不存在的子域（如 `ws-zzzzzzzzzzzzzzzz`）同样返回 200，它不校验 workspace。

**安全设计**
- API Key **只从环境变量 `DASHSCOPE_API_KEY` 读取**，不接受命令行参数（避免出现在 shell 历史与进程列表里）
- 脚本不把 Key 写进任何文件；`--dry-run` 输出中也不含 Key

**接口依据**：[API 概览](https://help.aliyun.com/zh/model-studio/managed-agents-api-overview)、[创建 Agent](https://help.aliyun.com/zh/model-studio/agent-create)、[Environment](https://help.aliyun.com/zh/model-studio/environment-api)、[Skill](https://help.aliyun.com/zh/model-studio/skills-api)、[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)。

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

## eval_agent.py

**把设计承诺变成可量化指标。** 12 个固定用例，逐例跑真实会话，自动打分。

```bash
# 先看会跑哪些用例（不需要 Key）
python3 scripts/eval_agent.py --dry-run

# 跑全部（约 15 分钟，会消耗百炼与飞猪额度）
DASHSCOPE_API_KEY=sk-xxx python3 scripts/eval_agent.py \
    --workspace ws-xxxxxxxxxxxx --agent agent_xxx --env env_xxx \
    --memory-store memstore_xxx

# 只跑前 3 个
python3 scripts/eval_agent.py ... --limit 3

# 只跑某个用例
python3 scripts/eval_agent.py ... --only E05
```

**用例覆盖六类设计承诺**：

| 类目 | 用例 | 验证什么 |
|---|---|---|
| 精确映射 | E01–E04（原神 / 黑神话 / LOL / 星露谷） | 第 ① 级是否按知识库命中 |
| 风格兜底 | E05–E06（像素风 / 剧情向） | 第 ② 级是否命中 |
| 反问引导 | E07（虚构游戏名） | 第 ③ 级是否反问而非硬猜 |
| 追问三要素 | E08（只说「我玩原神」） | 缺信息时是否追问 |
| 真实数据 | E09 / E12 | 是否真的调了 flyai |
| 兜底诚实 | E10（问一个可能无供给的赛事） | 查不到时是否如实告知 |
| 边界合规 | E11（要求代付款） | 是否拒绝并给跳转链接 |

**输出**：`demo/eval-report-merged.md`（报告）+ `demo/eval-raw-merged.json`（原始回复，供人工复核）

**设计取舍**：自动打分用**关键词启发式**，会有误判。所以报告里每一行都留了「人工复核」列，并同时输出原始回复。**自动打分是初筛，不是终审。**

**前置条件**：模型必须有可用额度。实测踩过 `AllocationQuota.FreeTierOnly`（免费额度耗尽），此时会话会在最后一次模型调用处报 `model_execution_failed`——工具链已经跑完，只是出不了最终回复。需在控制台充值或关闭「仅使用免费额度」模式。

## dump_session.py

把一个会话的事件流压成可读的 Markdown 时间线。

```bash
DASHSCOPE_API_KEY=sk-xxx python3 scripts/dump_session.py \
    --workspace ws-xxxxxxxxxxxx --session sesn_xxx --out demo/trace.md
```

输出包含：逐步时间线（模型请求 / 工具调用及参数 / 工具原始返回 / 助手回复 / 错误）+ 工具调用统计 + 结论。

**为什么有用**：截图只能证明"看起来对"，事件流能证明**每一次工具调用真的发生了、返回了什么**。做取证比截图更硬。

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

# scripts/ · 代码边界说明

**先说清楚一件事**：本项目的**运行时主体不是代码**，而是「系统提示词 + 知识库」跑在阿里云百炼 Managed Agent 上。

这里放的不是"作品本体的源码"，而是**围绕作品本体的四个辅助脚本**。写它们的原因很具体：把文档里的承诺变成可执行的检查，让页面数据与知识库同步，以及**让智能体本身的配置也可通过 API 复现**。

| 脚本 | 解决什么问题 | 是否在运行时链路 |
|---|---|---|
| `deploy_bailian.py` | 用**百炼 Managed Agents REST API** 把"控制台手点四下"变成一条命令：建环境 → 匹配飞猪技能 → 建智能体（系统提示词 + 知识库）→ 冒烟测试。**这是"百炼使用深度"从「控制台会话」升到「API 编程调用」的那一步** | ❌ 不在。部署期工具（部署出来的智能体才在运行时链路） |
| `verify_knowledge_base.py` | 知识库的「扩展规则」写了「映射目的地必须能被 flyai 查到真实数据」——只写在文档里就是口号。这个脚本把它变成可核对的清单 | ❌ 不在。开发/维护期工具 |
| `build_playground.py` | 从知识库生成 [`../showcase/playground.html`](../showcase/playground.html) 的数据，保证**在线查询器的内容永远与知识库同步**，不会两边各写一份 | ❌ 不在。构建期工具 |
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

**已知限制**
- **本脚本尚未在真实账号上端到端跑通。** 实测过程与结论：
  - `--dry-run` 已验证（请求体按官方文档逐字段核对，不需要 Key）
  - 用真实 Key 实测：`/api/v1/models` 返回 **200**（凭据有效），但 `/api/v1/agentstudio/*` 返回 **403 `Endpoint.AccessDenied`**
  - 按官方文档，**最可能的原因是 API Key 的「归属业务空间」不是智能体所在的那个**。原文：
    > 「API Key 的调用权限完全由其归属业务空间决定。」
    > 「默认业务空间下的 API Key：可调用所有标准模型，以及默认业务空间内的应用。」
    > 「子业务空间下的 API Key：可调用该子业务空间已获得模型调用授权的标准模型，以及该业务空间内的应用。」
  - 修复路径：在智能体所在的业务空间里创建 API Key（创建弹窗中的「归属业务空间」选那个空间，「权限」选「全部」），再用页面上该 Key 对应的 Base URL 子域作为 `--workspace`
  - 脚本的 `diagnose()` 会打印上述排查步骤
- **排查时的一个坑（已写进脚本注释）**：**不要用 `/api/v1/models` 的返回判断工作空间是否匹配**。实测该接口是**账号级**的——传一个根本不存在的子域（如 `ws-zzzzzzzzzzzzzzzz`）同样返回 200，它不校验 workspace。
- **关于 workspace_id 的写法**：官方文档示例是 `ws_xxxxxxxxxxxx`（下划线），但 DNS 主机名不允许下划线，所以实际端点用的是 `ws-xxxxxxxxxxxx`（连字符）。直接用 API Key 页面 Base URL 里显示的那一串即可。
- 飞猪技能的 `skill_id` / `version` 从工作空间技能列表自动匹配；匹配不到会打印全部候选，可用 `--skill-id` / `--skill-version` 手动指定。
- 接口依据：[API 概览](https://help.aliyun.com/zh/model-studio/managed-agents-api-overview)、[创建 Agent](https://help.aliyun.com/zh/model-studio/agent-create)、[Environment](https://help.aliyun.com/zh/model-studio/environment-api)、[Skill](https://help.aliyun.com/zh/model-studio/skills-api)、[获取 API Key](https://help.aliyun.com/zh/model-studio/get-api-key)。

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

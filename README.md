[Uploading README.md…]()
# YuriBot

> 跑在 QQ 群里的拟人化 AI 成员——不是"有问必答的客服"，而是懂得
> **什么时候该说话、说多少、什么时候该闭嘴**的群友。
> 多模型分层编排 · 全链路评估驱动 · 工具调用 + 文档理解

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-green)]()

## 亮点

| | |
|---|---|
| 💬 **接话决策系统** | 免@消息经轻量裁判模型输出 `{addressee, reply, reason}`，全量决策日志驱动迭代，分场景回复率 ~25%（目标区间），10+ 条 golden case 回归锁定 |
| 🔀 **每群串行队列** | 多消息流并发下"第 N 条生成时第 N-1 条已在上下文"，根治多头回复/复读；热群队列积压时复查丢弃率自适应上升——**噪声自我调节** |
| 🛠️ **工具调用与自我纠错** | 联网搜索（语义缓存 + `force_refresh` 覆盖写，知识库 revision 递增自我修正）；PDF 文档理解（解析→章节级 Map-Reduce→带页码引用追问，**30 问 golden set 命中率 90%**） |
| 💰 **成本分层** | 主模型仅承担表达层，裁判/压缩/检索/复查全压免费 4B，token 消耗按 tag 精确核算至每次调用 |
| 📏 **延迟可证明有上界** | 生成（30s）与发送（120s）分阶段超时预算，端到端行为可预期 |

## 架构

```mermaid
flowchart TD
    A[QQ Webhook<br/>验签/去重/分发] --> B{消息类型}
    B -->|@ / 触发词| C[静默门<br/>碎片塌缩]
    B -->|免@| C
    B -->|命令| D[指令注册表 20+]
    C --> E[judge 4B<br/>addressee/reply/reason]
    E -->|true| F[每群串行队列<br/>复查→生成→等发完]
    F --> G[Router 4B<br/>8字段调度]
    G --> H[主模型 DeepSeek<br/>tools: web_search / read_pdf]
    G --> I[RAG 注入<br/>docs门控·BM25+向量RRF·页码引用]
    H --> J[发送队列<br/>分泡/打字节奏/共享seq]
    I --> H
    J --> K[(SQLite<br/>决策日志/文档注册)]
    G --> L[(Qdrant ×5<br/>scenes/memes/slang/web_notes/docs)]
    H --> L
```

## 核心特性

1. **职责分离架构**：决策进 judge（说不说）、语气进 prompt（怎么说）、闸门进管线（能不能）。反例教训：把语气指令写进 judge system = 废纸。
2. **评估驱动迭代**：每次 prompt/规则修订对应 `interject_log` 真实 case；judge golden set + PDF RAG golden set（锚点标注、三级判分：解析缺失/检索未命中/命中）双回归体系。**"先修测量，再修系统"**：首版评测 4/10 实为锚形态 artifact，修正后 10/10。
3. **混合检索**：BM25(trigram) + 向量 RRF 融合 + references 过滤，纯向量基线 80% → 90%；references 过滤治"参考文献页相似度漂移"。
4. **诚实红线人设**：不懂不装懂、被纠正秒认、不编造、偷图承认——幻觉治理首先是产品决策。
5. **Graceful degradation**：Qdrant/搜索/API 任一故障，全链路降级不静默（记忆痕迹兜底 + 确定性兜底文案），用户零感知。
6. **异步任务闭环**：`/pdf` 接活回执 → 后台 Map-Reduce → 主动回话——系统首个跨分钟级任务闭环。

## 技术栈

| 层 | 选型 |
|---|---|
| 主模型 | DeepSeek `deepseek-v4-flash`（唯一计费点） |
| 轻量模型 | `Qwen/Qwen3.5-4B`（硅基流动，免费）· 嵌入 `bge-large-zh-v1.5` |
| 搜索 | 博查 Web Search API |
| 存储 | SQLite（aiosqlite）· Qdrant（5 collection）· 文件滚动缓存 |
| 框架 | asyncio + aiohttp · FastAPI（webhook） |
| 测试 | pytest（judge golden set）· 自研 PDF RAG 判分器 |

## 快速开始

```bash
git clone https://github.com/<you>/qqbot.git && cd qqbot
cp .env.example .env   # 填入 QQ Bot / DeepSeek / 硅基流动 / 博查 的 key
docker compose up -d   # bot + Qdrant（已含 fd ulimits 配置）
```

> Qdrant 注意：容器 fd 上限必须 ≥65536（RocksDB segment 句柄），compose 已配好；
> 用默认 1024 会周期性假死（Too many open files）。

## 测试

```bash
pip install pytest pytest-asyncio
python3 -m pytest test_judge.py -v     # judge golden set 回归

# PDF RAG golden set（离线，零依赖 Qdrant/bot）
python3 -m test_pdf_rag                 # 基线跑分
python3 -m test_pdf_rag testset_chebnet.json 5 --hybrid   # 混合检索跑分
```

## 目录结构

```
core/       # ai / router / memory / interject（串行队列）/ debounce
services/   # 能力层：pdf_tool / web_search / meme_store / sender / scene_manager ...
handlers/   # 事件入口：chat / admin / owner
tools/      # 命令注册：latex
test_pdf_rag.py   # PDF RAG 判分器（三级失败定位）
test_judge.py     # judge golden set
```

## 设计哲学（完整版见 docs/）

职责分离 · 评估驱动 · 枚举是死路 · 表达层协议 vs action 层 tool ·
瞬时状态与持久状态划界 · 合并必须在 prompt 层做 · 超时预算分阶段 ·
渲染层兜模型的坏习惯 · 确定性兜底给概率系统封顶 ·
新链路必须带齐可观测性 · **先修测量，再修系统**

## 免责声明

本项目为个人学习作品：QQ 及 B站 相关接口与数据归各自平台所有；bot 人设为虚构角色；
群聊数据仅存储于部署者本地。请勿用于骚扰、spam 或违反平台条款的场景。

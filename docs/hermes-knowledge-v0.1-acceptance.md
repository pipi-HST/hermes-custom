# Hermes Knowledge v0.1 功能说明与验收手册

更新日期：2026-06-01

> **历史文档（v0.1，2026-06）**：文中的 Obsidian、MOC、Canvas、Garden 等是早期产品形态。当前口径是 Web-native 三栏工作台 + 自研卡片板 + 知识图谱，卡片固定四类（洞察/金句、新知/概念、方法/框架、避坑/边界）；Obsidian 仅作为可选导出/外部编辑兼容。面试引用请看 `docs/PRODUCT-REFACTOR-STORY.md`。

## 1. 产品定位

Hermes 主 Agent 是唯一入口，负责理解你的微信/飞书消息、判断意图、选择工具并组织回复。

`hermes-custom` 不是另一个大 Agent，而是 Hermes 的知识库能力插件。它提供 Capture、Library、RAG、Theme、Garden、Canvas、Review 等专用能力模块。模块内部可以调用 LLM 做判断，但系统级编排仍由 Hermes 主 Agent 完成。

这版的目标不是“全自动知识库”，而是一个可控的知识库工作台：

- 你负责提出目标和确认关键结果。
- Hermes 负责选择路径、调用工具、生成草稿、维护索引和解释状态。
- 涉及入库、生成 Garden Card、修复知识库等重要动作时，应保留确认或清晰反馈。

## 2. v0.1 已纳入范围

### Capture：材料进入知识库

支持内容：

- 微信公众号链接。
- 小红书图文链接。
- 普通文本。
- 图片 OCR / 图片转写链路。
- 长文草稿预览和分页查看。
- 用户指定整理方式，例如“全文保存”“总结核心内容”“方法论详细一些”。

预期行为：

- 默认先生成草稿，不直接入库。
- 微信回复应包含标题、重点回应、简短总结和下一步指令。
- “展开草稿”查看草稿全文或分页内容。
- “存库”后写入 Knowledge File `captures/`，原文写入 `_archive/raw/`（Obsidian 仅是可选外部编辑器）。
- 入库后自动触发 Library 后处理：补属性、补目录/章节、更新 embedding。

### Library：知识库卫生维护

支持内容：

- DB 与 Knowledge File 同步检查（资料正文以 Markdown 为源，卡片/专题/图谱以 DB 为源）。
- 清理重复开放草稿。
- 补 capture/raw properties。
- 为长文补 `目录纲要`。
- 对没有 Markdown 章节的长文，按目录范围插入标题，不改写正文内容。
- 更新 identity embedding 和 section embedding。

预期行为：

- 全库维护可以手动触发。
- Capture 入库后会自动对当前文章做定向维护。
- 真实 DB 修复前应备份。
- 报告写入 `data/repair_reports/`，备份写入 `data/backups/`。

### RAG：基于知识库回答

支持内容：

- 基于 capture identity / section embedding 检索。
- 命中高层结构后沿图谱扩展相关 Theme、Garden、Capture。
- 回答中保留来源引用。
- 长答案可按需生成文件，而不是强塞进微信。

预期行为：

- 普通问题直接给答案。
- 来源应显示可读标题，而不是只显示 id。
- 参考来源只列实际使用的材料。
- 问“继续”“展开一点”时，应沿已有检索上下文继续，而不是重新乱搜。

### Theme：发现主题关联

支持内容：

- 扫描最近保存的内容。
- 没有最近内容时，可按用户要求扫描全库。
- 基于 identity / section embedding 发现关联。
- 生成候选主题或 MOC。

预期行为：

- 不要求你判断“这是方法还是观点”。
- 应先解释材料之间的关系，再给 2-3 个可沉淀方向。
- MOC 可以保持单向链接，不强制回写所有 capture 的 wiki 双链。

### Garden：沉淀知识卡片

支持内容：

- 从一组相关材料生成 Garden Card 草稿。
- 支持观点、事实、方法、风险/注意事项、开放问题等卡片形态。
- 卡片类型由系统推断，不要求用户预先分类。
- 生成前做重复检查，避免同一观点反复入库。

预期行为：

- 先生成草稿，再等你确认。
- 如果命中重复卡，应提示已有卡片，而不是再建一张。
- 引用材料应显示 Obsidian 可读链接或明确标题，避免裸 id。

### Canvas：Garden Card 的结构化视图

支持内容：

- 以 Garden Card 为主角生成 Obsidian Canvas。
- 当卡片之间形成清晰结构时，生成聚类/关系视图。
- 当只是一组同主题材料、没有结构关系时，应拒绝生成 Canvas，并说明原因。

预期行为：

- Canvas 不是普通 MOC 可视化。
- Canvas 应展示 Garden Card 之间的关系。
- 来源材料作为支撑信息展示，不应抢占主视觉。

### Review：知识点回顾

支持内容：

- 每天讲一个知识点。
- 长文章可以按章节生成 Review item。
- 记录材料或知识点被看过的次数。
- 支持对刚讲过的点继续追问。

预期行为：

- Review 是讲解，不是考试。
- 不要求你记复习编号。
- 针对刚刚讲过的内容追问时，应只围绕当前 review item 展开。

### Run Ledger / 可观测性

支持内容：

- 记录工具调用的开始、成功、失败、耗时、输入摘要和输出摘要。
- 支持查询 Agent 运行状态。
- 支持把长报告写成文件。
- RAG、Capture、Library、Theme、Garden、Graph、Canvas 已接入按需文件输出。

预期行为：

- 调试时优先看 `data/agent_runs.db`、`data/*.log` 和 `~/.hermes/logs/`。
- 网关重启后应清空或轮转 `~/.hermes/sessions`，避免旧会话污染微信测试。

## 3. v0.1 暂不纳入范围

这些不是核心闭环阻塞项，可以作为下一阶段：

- Garden Canvas 的精美 UI 持续打磨。
- Life Assistant 与知识库的深度融合。
- 更多边缘工具的长输出文件化。
- 更完整的后台 dashboard。
- 多轮任务流的完整可视化监控。
- 自动从全网发现新材料。

## 4. 手动验收清单

### Capture 验收

1. 微信发送一篇公众号文章：
   - 预期：返回草稿预览，不直接入库。
   - 回复“展开草稿”：能看到草稿正文。
   - 回复“存库”：生成 capture/raw 文件。

2. 微信发送一篇小红书图文：
   - 预期：能提取图文内容，不只拿 OCR 中一句话硬总结。
   - 标题应有真实含义，不应只是“小红书”。
   - 存库后 raw 进入 `_archive/raw/`。

3. 发送“全文保存”：
   - 预期：正文按全文入库，不被压缩成总结。
   - 如果文章很长，Library 后续补目录/章节，而不是 Capture 强行压缩。

### Library 验收

1. 对刚入库的长文检查 Obsidian 文件：
   - 有 `type: capture`。
   - 有可读标题。
   - 长文有 `## 目录纲要`。
   - 正文有 Markdown 章节标题。

2. 查 DB embedding：
   - 当前 capture 至少有 1 条 identity embedding。
   - 长文应有多条 section embedding。

3. 触发“整理/检查知识库”：
   - 不应把可自动补的 properties 全丢给你手动处理。
   - 修复动作应有清晰报告和备份路径。

### RAG 验收

1. 问一篇刚入库文章的具体问题：
   - 预期：命中对应 capture 或章节。
   - 回答里有材料标题/来源，不是裸 id。

2. 问跨多篇材料的问题：
   - 预期：能引用多篇材料。
   - 参考来源只列实际用到的材料。

3. 回复“展开一点”：
   - 预期：沿同一上下文继续，不误调用 Capture 草稿工具。

### Theme / Garden 验收

1. 说“扫描最近保存的内容，看看有没有可以整理成知识卡片的关联”：
   - 预期：先给候选关联。
   - 候选应说明材料之间的关系，而不是只给主题名。

2. 选择其中一组：
   - 预期：生成 Garden 草稿。
   - 草稿含标题、一句话、核心内容和参考来源。

3. 对同一组重复生成：
   - 预期：提示已有重复或近似卡片，不应无限新建。

### Canvas 验收

1. 对已有 Garden Card 的主题生成 Canvas：
   - 预期：如果卡片之间有结构关系，生成 `.canvas` 文件。
   - 如果只是同主题堆叠，应说明不适合生成 Canvas。

2. 打开 Obsidian Canvas：
   - 预期：主角是 Garden Card。
   - 来源材料链接可读，不显示无意义裸 id。

### Review 验收

1. 说“复习一下知识点”：
   - 预期：讲一个点，不是考你。
   - 回复中有参考来源。

2. 追问刚讲过的点：
   - 预期：围绕刚才那篇/那节材料解释。
   - 不应调用 `show_capture_draft`。

## 5. 自动化验收命令

在仓库根目录运行（Windows 用 `.venv\Scripts\python.exe`）：

```bash
.venv/bin/python -m pytest tests -q
```

自动可验项（当前）：

- 后端全量：`pytest tests`（Windows 实测 1290+ 用例，以收集数为准）
- 评测阈值契约：`pytest tests/test_eval_rag_benchmark.py -q`
- 重置脚本：`pytest tests/test_reset_hermes_local_data.py tests/test_reset_smoke.py -q`
- 前端构建：在 `hermes-agent-main/web` 运行 `npm run build`

微信/飞书“connected”、Obsidian/Canvas 目视类检查仍是人工项，不要把它们写成自动通过。

当前主线还建议保留这些重点测试文件：

- `tests/test_capture_draft_workflow.py`
- `tests/test_media_capture.py`
- `tests/test_xiaohongshu_extractor.py`
- `tests/test_library_agent.py`
- `tests/test_rag_continue_bridge.py`
- `tests/test_canvas_agent.py`
- `tests/test_canvas_bridge.py`
- `tests/test_garden_structure_agent.py`
- `tests/test_run_ledger.py`

## 6. Hermes 重启验收

大改代码后执行：

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/Users/pipi/.hermes/session_backups/$stamp"
mkdir -p "$backup_dir" /Users/pipi/.hermes/logs /Users/pipi/.hermes/sessions
cp -a /Users/pipi/.hermes/sessions/. "$backup_dir/" 2>/dev/null || true
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 -delete 2>/dev/null || true
pkill -f "[h]ermes_cli.main gateway run" 2>/dev/null || true
nohup /Users/pipi/Downloads/hermes-agent-main/.venv/bin/python -m hermes_cli.main gateway run --replace > "/Users/pipi/.hermes/logs/gateway-restart-$stamp.log" 2>&1 &
sleep 4
pgrep -fl "hermes_cli.main gateway run"
cat /Users/pipi/.hermes/gateway_state.json
find /Users/pipi/.hermes/sessions -mindepth 1 -maxdepth 1 | wc -l | tr -d ' '
```

验收标准：

- 只有一个 gateway 进程。
- `gateway_state` 是 `running`。
- `weixin` 是 `connected`。
- `feishu` 是 `connected`。
- session 目录为空，或只有本轮新测试产生的会话。

## 7. 下一阶段建议

v0.1 收尾后，建议不要同时开很多方向。优先级建议：

1. Garden Canvas 可视化体验：让 Garden Card 聚类和引用关系真正可读。
2. 长输出文件协议：RAG 长答案、Capture 长草稿、Library 报告按需发文件。
3. Life Assistant 独立推进：作息、饮食、喝水、睡眠、个性化提醒。

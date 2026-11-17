---
status: historical
owner: hermes-knowledge
last_verified: 2026-11-17
---

# Theme Agent 增强设计方案

## 现状

当前 vault 的问题：
- `themes/` 目录空 — 没有主题聚合页
- `captures/` 之间零 `[[wiki-links]]` — 笔记完全孤立
- `garden/` 目录空 — 没有知识卡片
- Tags 是唯一的关系线索（frontmatter 中的 tags 列表）

当前 theme_agent 的能力：
- 三级过滤（向量 → LLM 精筛 → 全文确认）发现 capture 间关联
- 生成 `pending_question` 问用户要不要合成卡片
- **但不产出任何 Obsidian 内容** — 发现关联后只存 DB，不写 .md

## 设计理念

> Theme Agent 不只是"发现关联"，而是**主动编织知识网络**。
> 每次扫描都应该在 vault 里留下可见的痕迹 — 链接、主题页、图谱。

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| MOC 页创建方式 | **自动创建**（无需用户确认） | theme_scan 已做过三级过滤确认关联，质量有保障；减少用户决策负担 |
| Wiki-link 格式 | **标题** `[[芯片又杀疯了]]` | 可读性好，Obsidian 图谱显示更直观；slug 格式虽然稳定但用户不友好 |
| 主题命名 | **LLM 生成** | tags 太碎片化（ai-education, chip-industry），LLM 能生成更自然的中文主题名（如"AI与教育变革"） |
| L4 图谱洞察 | **cron 定期报告** | 图谱分析不需要实时交互，定期推送摘要即可；避免增加 Hermes 工具复杂度 |

## 增强方案：4 层能力

### Layer 1: Wiki-Link 注入（P0 — 改 capture 笔记本身）

**触发时机**：theme_scan 发现 strong 关联后

**做什么**：
- 在相关 capture 的 .md 末尾追加 `## 相关笔记` 段落
- 用 `[[笔记标题]]` 格式互相引用（Obsidian 原生正反链接）
- 如果已有 `## 相关笔记` 段落，追加而非覆盖

```markdown
## 相关笔记
- [[芯片又杀疯了...]] — 寒武纪市值攀升与AI教育冲击
- [[AI时代的电力协议]] — 算力基础设施的电力瓶颈
```

**实现**：`agents/markdown_writer.py` 新增 `append_links(vault_path, file_path, links: list[dict])` 方法：
- 读取 .md，找到 `## 相关笔记` 段落（不存在则追加）
- 检查已有链接，只追加新的（不重复）
- 写回文件

**效果**：
- Obsidian 自动生成正反链接（backlinks）
- 图谱视图开始出现连线
- 用户打开任何一篇笔记都能看到相关笔记

### Layer 2: 主题 MOC 页（P1 — Map of Content）

**触发时机**：theme_scan 发现聚类 ≥3 篇笔记时，自动创建

**做什么**：
- LLM 根据聚类内容生成自然语言主题名（如"AI与教育变革"而非"ai-education"）
- 在 `themes/` 目录创建主题页
- 包含：主题描述、相关笔记列表（带摘要和 wiki-link）、开放问题

```markdown
---
type: theme
theme_name: AI与教育变革
tags:
  - ai-education
  - reading-notes
created: 2026-05-27
captures: 5
---

# AI与教育变革

AI正在重塑教育的形态，从传统名校模式到个性化学习路径。
这个主题探讨了AI对知识获取、深度思考和传统教育体系的冲击。

## 相关笔记
1. [[芯片又杀疯了...]] — AI将削弱名校优势，催生私塾式灵活教育
2. [[多读一点难读的书]] — AI时代碎片化阅读异化思维，需深度思考

## 开放问题
- AI真的能替代深度阅读吗？
- 私塾模式在AI时代可行吗？
```

**实现**：`agents/theme_agent.py` 新增方法：
- `generate_theme_name(captures) -> str` — LLM 生成主题名
- `build_moc(theme_name, captures) -> str` — 生成 MOC 页 Markdown
- `agents/markdown_writer.py` 新增 `write_moc(vault_path, theme_name, content)` — 写入 themes/ 目录

**效果**：
- Obsidian 图谱中出现"主题节点"，自然形成星形拓扑
- 用户可以在主题页纵览一个领域的所有笔记
- 主题页本身也可以被 garden_agent 合成为知识卡片

### Layer 3: 主题演化追踪（P2 — MOC 页持续维护）

**触发时机**：每次 theme_scan 时，检查新 capture 是否属于已有主题

**做什么**：
- 通过 tag 重叠或向量相似度判断新 capture 是否属于已有 MOC 的主题
- 如果是，更新 MOC 页：追加笔记到列表、更新 captures 计数、追加更新日志

```markdown
## 更新日志
- 2026-05-28: 新增《AI编程助手的真相》，关联度 high
- 2026-05-27: 新增《芯片又杀疯了》，寒武纪市值与AI教育关联
- 2026-05-26: 主题创建，初始 3 篇笔记
```

**实现**：`agents/theme_agent.py` 新增 `update_moc(vault_path, theme_path, new_capture)` 方法：
- 读取已有 MOC 页
- 在"相关笔记"列表末尾追加新条目
- 在"更新日志"顶部追加新记录
- 更新 frontmatter 的 captures 计数
- 写回文件

**效果**：
- 主题页是"活的"，随知识库增长而演化
- 用户可以看到每个主题的发展脉络

### Layer 4: 图谱洞察（P3 — cron 定期报告）

**触发时机**：cron 定期（如每周一次）或手动触发 `knowledge_graph_report` bridge

**做什么**：
- 扫描所有 capture 的 `[[links]]` + tags + embeddings
- 计算图谱统计：
  - **孤岛笔记**：没有任何 `[[wiki-links]]` 的 capture → 建议关联
  - **桥接笔记**：连接两个不同主题 MOC 的 capture → 高亮
  - **主题健康度**：每个主题的笔记数、平均关联度、最近更新
- 生成摘要，通过 Hermes 推送给用户

```
📊 知识图谱周报：
- 总笔记: 13 篇 → 本周 +3
- 主题: 4 个 (AI与教育变革、芯片产业、国际博弈、读书方法论)
- 已连接: 9 篇 (69%) → 上周 40%
- 孤岛笔记: 4 篇 — 建议关联
  · 《特朗普关税政策》→ 建议归入"国际博弈"
  · 《AI电力协议》→ 建议关联"芯片产业"
- 桥接笔记: [[芯片又杀疯了]] (AI与教育变革 ↔ 芯片产业)
```

**实现**：
- `agents/theme_agent.py` 新增 `graph_report(vault_path, repo) -> str`
- `bridge/theme_bridge.py` 新增 `run_graph_report()` 入口
- 可选：注册 Hermes cron 工具

**效果**：
- 定期了解知识库的连接健康度
- 发现知识盲区和新关联机会

## 数据流设计

```
theme_scan 触发（cron 或手动）
  ↓
新 captures（theme_scanned_at IS NULL）
  ↓
[现有] 三级过滤发现 strong 关联
  ↓
[Layer 1] 写 [[wiki-links]] 到相关 capture 的 .md
  ↓
[Layer 2] 聚类 ≥3 篇？→ LLM 生成主题名 → 创建 themes/*.md MOC 页
  ↓
[Layer 3] 新 capture 匹配已有主题？→ 更新 MOC 页 + 追加更新日志
  ↓
标记 theme_scanned_at，提交 DB

graph_report 触发（独立 cron，如每周一次）
  ↓
[Layer 4] 扫描全图谱 → 统计孤岛/桥接/健康度 → 推送周报
```

## 需要修改的文件

| 文件 | 改动 |
|------|------|
| `agents/theme_agent.py` | scan() 调用 link 注入；新增 `generate_theme_name()`、`build_moc()`、`update_moc()`、`graph_report()` |
| `agents/markdown_writer.py` | 新增 `append_links()`、`write_moc()`、`read_moc()` |
| `bridge/theme_bridge.py` | scan 后调用 L1 link 注入 + L2/L3 MOC 生成；新增 `run_graph_report()` |
| `db/schema.py` | 可选：captures 表新增 `theme_id` 列 |
| `tests/test_theme_agent.py` | 新增 L1-L3 各 2-3 个测试 |

## 实现顺序

1. **L1 Wiki-Link 注入** — ✅ 完成（`append_links()` + scan() 集成）
2. **L2 主题 MOC 页** — ✅ 完成（`generate_theme_name()` + `build_moc()` + `write_moc()`）
3. **L3 主题演化** — ✅ 完成（`read_moc()` + `update_moc()` + `match_to_themes()` + `_evolve_themes()`）
4. **L4 图谱洞察** — ✅ 完成（`graph_report()` + `run_graph_report()` cron 入口）

## 实现偏差记录

- L3 `update_moc()` 实际放在 `agents/markdown_writer.py` 而非 `agents/theme_agent.py`（与 L1/L2 的 markdown 工具保持一致）
- L3 匹配策略：仅用 tag 重叠（≥2 个共同 tag），暂不加 embedding 相似度（简单有效，可后续扩展）
- L3 bridge 层抽出 `_evolve_themes()` 辅助函数便于测试
- L4 `graph_report()` 直接扫描文件系统而非依赖 DB（captures/ 和 themes/ 目录），因为图谱洞察基于 .md 文件中的 [[wiki-links]]
- L4 孤岛定义：capture 正文中没有任何 [[wiki-link]]（简单启发式，未考虑反向链接）
- L4 桥接定义：capture 标题被 ≥2 个不同的 MOC 引用（通过扫描 MOC 正文中的 [[title]]）
- L4 暂未实现"本周新增"、"上周对比"等增量统计（可作为后续增强）

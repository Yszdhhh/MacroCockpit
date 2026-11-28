# 架构决策记录（ADR）

每项架构决策均须经 **Deletion Test** 检验：如果删除这一层，复杂性会消失，还是会扩散到调用方？只有真正封装复杂性、降低全系统总熵的模块才保留。

---

## ADR-001：沉淀 Level-0 运行时底座 `backend/runtime.py`
- **背景**：`BJT`（28 处）、`_safe_home`（7 处）、`_read_json`（6 处）、`atomic_write_json`（4 套 + 11 处 `os.replace`）、`COCKPIT_ROOT`/`DATA_DIR`（16+ 处）分散复制；16 个业务模块反向导入 `fred_and_market` 仅为了获取路径与写盘函数。
- **决策**：新建零内部依赖的 `backend/runtime.py`，统一提供时钟、路径解析（含 Windows 服务账户回退）、环境变量规格表、跨进程可重入 `file_lock` 与安全 JSON/文本原子写盘。
- **Deletion Test**：若删除该层，路径环境漂移（如 `MACROCOCKPIT_DATA_DIR` 被忽略）与跨模块反向依赖将立即扩散回 20+ 个业务模块，且导致无锁并发写风险。**保留**。

## ADR-002：沉淀 Level-0 公共门禁底座 `backend/guardrails.py`
- **背景**：`FORBIDDEN_TERMS` 与正则清洗表在 10+ 个文件中各自维护并已出现替换词漂移；隐私正则散布在 `public_snapshot`、`deep_research_runner`、`guru_sources`、`portfolio_store`；SSRF 校验埋在 `rss_sources.py`，导致 `guru_sources.py` 未做重定向禁跳与 IP 钉扎校验。
- **决策**：新建零内部依赖的 `backend/guardrails.py`，作为违禁词清洗（支持保留换行与量化细粒度变体）、隐私内容拦截、安全正则校验及 SSRF 安全 URL/HTTP 抓取的唯一权威来源；清理各业务模块重复表，仅在外部测试直接引用的模块保留 1 行引用别名。
- **Deletion Test**：若删除该层，10 处违禁词表将继续漂移，且 `guru_sources` 等下载器将失去统一的 SSRF 与隐私门禁保护。**保留**。

## ADR-003：以 `signal_text.py`、`article_store.py`、`agy_runtime.py` 与桌面扩展注入清零循环依赖与跨包反向依赖
- **背景**：代码库存在 3 组强连通环路（6 模块核心环、`guru_sources <-> rss_sources`、`periodic_report <-> deep_research_runner`）及 7 处 `backend -> desktop` 懒导入。
- **决策**：
  1. 提取 `backend/signal_text.py` 收敛 XML/HTML 清洗、标的/子主题抽取、摘要去重与多空词典；OPML 汇总由订阅方单向注入。
  2. 提取 `backend/article_store.py` 收敛 `articles/` 目录白名单、Markdown frontmatter 解析、去重均衡排序与深研入库，使 `periodic_report -> deep_research_runner -> article_store` 严格单向。
  3. 将无 UI 的本地 AGY CLI 运行时下沉至 `backend/agy_runtime.py`（`desktop/acp_bridge.py` 转发重导出）；将 `mcp_server.py` 对 `desktop/*` 的 4 个扩展工具改为可注入 `desktop_handlers` 回调，由 `desktop/mcp_extensions.py` 与 `desktop/pro_server.py` 装配，使 `backend/` 静态 AST 导入 `desktop/` 数为 **0**。
- **Deletion Test**：每层均封装真实跨源复杂性（RSS/Markdown/进程通信/插件边界）并彻底断开环路与跨层倒挂，而非无意义的透传转发层。**保留**。

## ADR-004：统一 HTTP 安全与 RBAC 边界 `backend/http_security.py` 并收口私有耦合
- **背景**：`server.py`、`mcp_server.py`、`pro_server.py` 重复实现常量时间比对、回环 Origin 校验、JSON 请求体读取与五角色 RBAC 判定，且全仓存在 53 处跨模块 `_foo` 私有导入。
- **决策**：提取 `backend/http_security.py` 统一安全边界与结构化日志异常兜底；将 `cloud_collector`、`signal_aggregator`、`public_snapshot`、`kazike_engine` 等跨模块复用的能力提升为清晰的公开函数契约，仅对现有单测 monkeypatch 靶点保留显式兼容别名，删除 5 处零调用死函数。
- **Deletion Test**：统一安全边界直接消除 `pro_server /mcp` 绕过协议校验与 `server.py` GET 鉴权分叉风险；删除零调用死代码直接降低维护熵。**保留**。

## ADR-005：前端无构建直出条件下的设计 Token 与公共工具归一
- **背景**：`tests/test_macro_cockpit.py` 与 `backend.server --build-static` 依赖 `web/index.html`、`web/styles.css`、`web/app.js` 原生无构建单文件内联；同时存在 Token 漂移、13 种 `@media` 断点、死函数 `formatMoney` 及 `renderPortfolioView` 数量未遮罩缺陷。
- **决策**：不引入 Node/Webpack 打包器；通过共享 `web/tokens.css`（并在 `build_static_bundle` 中内联）或严格对齐变量表、收敛 `@media` 断点至 3–4 个规范层级、补齐 `qtyHtml()` 隐藏金额遮罩、清除静态默认值 `100000` 与死函数 `formatMoney`、统一客户端 API/格式化工具。
- **Deletion Test**：不增加构建链路复杂性，直接消除双端 CSS 漂移与隐私脱敏漏洞。**保留**。

## ADR-006：机器可执行的 AST 架构门禁 `tests/test_architecture_governance.py`
- **背景**：仅靠文档规范无法防止后续 Agent 并发开发重新引入环路、重复违禁词表与 `backend -> desktop` 倒挂。
- **决策**：新增基于 Python `ast` 的自动化架构门禁测试并接入 CI，硬性断言：0 循环依赖、0 `backend -> desktop` 静态导入、L0 基座 0 内部导入、违禁词/运行时/HTTP安全唯一权威定义、0 生产代码跨模块私有符号导入。
- **Deletion Test**：以不到 200 行零依赖测试代码长期锁死前 5 个阶段的治理成果。**保留**。

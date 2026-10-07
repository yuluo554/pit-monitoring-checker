# HANDOFF · M4（内置基准评测 `pmc bench`）

> 用法：新对话直接说「读取 plan/HANDOFF-M4.md 并继续完成任务」。
> 上一棒（M3 条款核对 + 频率时效检核）已完成并本地提交；本文件是唯一续接载体，零上下文也能开工。

## 一、当前进度（M3 交付了什么）

| 面 | 已落地 | 验证方式 |
|---|---|---|
| 条款核对文档 | `plan/08 §二`：五条依据逐条给「渠道 + 原文定位 + 核对结论 + 是否供货数值」，并留了本轮实测的**失败证据**（openstd 明示工程建设类国标未纳入收录；建标库 TLS 证书主机名不符；gov.cn 域内检索 37 号令正文零命中） | `test_m3_discipline.py` 要求代码里的类别/原因码/规则 id 必须先在 `08` 出现（带阳性对照） |
| 依据登记表 | 标准级 `GB50497-2019` 由 `pending → located`（编号/名称/发布 2019-11-22/实施 2020-06-01 已按官方条目逐字核实）；三条条文级 + `MOHURD-37` + `JGJ120-2012` 仍 `pending`，**一条数值都没供货** | `selfcheck` 出「verified 0 / located 1 / pending 4」仍 `SELF_CHECK_OK`；`test_data_discipline.py` 全绿 |
| 频率规则集 | `data/rulesets/frequency_compliance.json` **v1 → v2**：间隔上限按开挖深度分档（新增规则键 `depth_max`，至多一条空值兜底档），三条 `interval` + 三条 `sequence` | `test_rules_loader.py`（规则总数 15 → 17）、`test_m3_discipline.py::test_ruleset_version_bumped_and_recorded` |
| 检核内核 | `pmc/compliance/auditor.py`：四类应核实事项 —— `missed` / `over_interval` / `stale_frequency` / `no_intensified_after_alarm`；结论 DTO 复用契约层 `ViolationRecord`；档位取不到就**不判**并记原因码；整段替换落库（同工程先 DELETE 再 INSERT） | `test_compliance_auditor.py` 22 项（规则与条款在内存构造，同 M2 做法） |
| 只读未闭环 | 报警后加密观测**只读** `alarm_state.unclosed=1` 与 `first_alarm_round_id`，不 import `pmc.alarm`、不重算报警；`alarm_state` 零行时报 `alarm_state_empty`（"没跑 check"不等于"没问题"） | AST 断言 + 行为断言（已处置行不进清单） |
| `pmc audit` | `--db [--project] [--from N] [--to N] [--rules-dir DIR]`；输出 `AUDIT_SCOPE / AUDIT_SUMMARY / AUDIT_CLAUSE / 明细行 / AUDIT_QUEUE`；退出码 0/1/2；结论列只有「应核实」一项 | `test_cli_m3.py` 16 项端到端；`test_cli_surface.py` 的未实装清单去掉 audit |
| 检核轴词汇 | 5 个原因码：`no_applicable_interval_band`、`excavation_depth_unrecorded`、`condition_not_recorded`、`alarm_state_empty`、`condition_change_blocked` —— 与阈值轴 9 码、引擎轴 2 码分账 | `08 §五` 定文；`test_m3_discipline.py` 逐条要求登记 |
| 夹具数据面 | `tests/fixtures/data_freq/`（自带 `clauses/` + `rulesets/` + 空 `dict/` 占位，构成 `find_data_dir` 认得的完整数据目录替身）：频率条款标 verified + 7/3/2 天三档，全部带 `FIXTURE-FREQ` / `syn-m3-fixture-1` 自述 | `FIXTURE_MARKERS` 扩容后，这些串出现在 `data/` 即红 |
| 回退供货证明 | 同一条序列在「仅档案设计值」与「仅标准回退」两档下结论逐列一致，回退落库行 `clause_ids` 非空；DTO 与 DDL 两处反证 | `test_clause_fallback.py` 5 项 |
| 回归对账 | 夹具面实测：`SYN-YYCG` `missed=2 over_interval=19 stale_frequency=3 no_intensified=2`；`SYN-ZHDQ` `missed=2 / over_interval=0 / stale_frequency=0 / no_intensified=2`；`SYN-LJ3`（8 轮零导入）8 条工程级 `round_not_imported` | `test_cli_m3.py` 逐条断言（数量对不上即视为口径漂移） |
| 全量测试 | **331 项，py3.8 与 py3.12 各一轮，全绿 0 跳过**（12–15 s） | `py -3.12 -X utf8 -m pytest -rs` |

**M3 的既成事实（不是缺陷）**：
`data/rulesets/*` 仍一条数值都没有、登记表条文级仍全 `pending` —— 所以不带夹具时
`pmc check` 出 1568 行全待定值、`pmc audit` 出**零违规 + 6 条不生效频率规则**，两者退出码都是 1。
数值只有两个合法入口：① 用户在测点档案录入设计报警值；② 用户提供材料（当地报送规定原文页 / 监测方案原文）后按 `08 §三` 的分档结构填。
**M4 不许为了让 bench 出数字就把夹具天数写进 `data/`**（禁项一，`test_data_discipline.py` 会红，那是刻意的）。

**两站结论相反的准确表述（已写进 `05 §三之四` #2）**：检核按**逐个未闭环报警**判定，
`SYN-ZHDQ` 四处报警里 R9/R11 两处被加密轮次 R10/R11/R12 覆盖（不报）、R14/R17 两处报；`SYN-YYCG` 两处全报。
整站二值化会让"报过一次加密就永远合规"，与题面"必须给可核验的判定"不符。

**未验证项（如实留档）**：① `pip install -e ".[dev]"` 仍未在干净 venv 按 README 原文逐条跑过；② CI 四矩阵未实跑（M3 仍未 push）。
M3 新增 4 个测试文件让本机收集数 274 → 331；CI 收集数必须与本机一致，M4 若再新增文件记得复核。

**M3 收尾基线**：`git status --porcelain` 只剩 `.qoder-credits/`（工具产物，不提交）；`synth --check` 过；双解释器 331 项同数全绿。

## 二、M4 待办（按顺序，每步都能单独演示）

1. **先解一道结构题（P08，需用户拍板或按建议先做）**：`bench` 是产品命令，onedir exe 里没有 `tests/`，
   而 M2/M3 的判定档位夹具住在 `tests/fixtures/`。三条路：
   - **建议**：`bench` 从 `pmc.synth.profile`（合成自证档位，M1 起就存在且**不在** `data/`）取档位，
     `bench` 层允许 import `pmc.synth`（`test_synth_freeze.py` 的禁令只覆盖判定/规则/导入/台账/契约层，bench 不在名单里）；
     档位数值仍然一个字不进 `data/`。
   - 弃：把夹具搬到 `data/`（撞禁项一与 C7）。
   - 弃：只支持 `--rules-dir` 外部传入（README 与 CI 三连就要求用户自己造档位，破坏"一键复现"）。
2. **写 `plan/10-基准与评测.md`**：四态指标口径的确切定义（分子/分母/边界）、
   `also_expect` token 的对账语义（`07 §6.1`，M1 定稿的 7 列真值不许改）、
   `unit_error` 与 `duplicate_report` 两类"给导入器出的考题"怎么算 pass（`04 §二` 已定：它们不许出现在引擎漏报清单里）、
   `bench --json` 的字段契约与 `--markdown` 的表格列序、门槛值（召回 ≥ 0.95 / 误报 = 0 / 定位误差中位 0 且 P95 ≤ 1）。
3. **`pmc/bench/runner.py`**：读 `data/truth/*.truth.csv` + 台账 + 判定结果，产出
   ① 逐起事件对账表（命中/漏报/晚报 N 轮/早报 N 轮）② 聚合四态指标 ③ 漏报清单。
   **期望值必须来自真值文件，不许在代码里硬编事件条数**；真值面缩水要当场红（M2 的 `EXPECTED_ALARM_EVENTS` 是同一思路）。
4. **`pmc bench [--json] [--markdown] [--sites …]`**：退出码 0 达标 / 1 有降级项（存在漏报、指标不可判、依据未核对）/ 2 输入不可用；
   `--markdown` 产出的表格必须能直接替换 README 指标节的基准行。
5. **README 指标表四行**（召回率 / 误报率 / 定位误差 / 漏报清单）由 `bench --markdown` 生成后**如实**落盘：
   达标才写「达标」，量不了写「不可判/不可用」，**基准类指标行不得写凭空数值**（C11 + `test_readme_honesty.py`）。
6. **门禁脚本**：`selfcheck → synth --check → pytest → bench` 四连（README 与 CI 用同一条链），任何一环红就退出码非 0。
7. **回归**：M2/M3 的逐起对账必须仍然成立（bench 的逐起表与 `test_alarm_regression.py` 的期望互相印证）；
   新增测试双解释器同数。
8. **收尾回写**：`plan/00` 状态、`plan/05` M4 行打勾（含偏差说明）、`plan/06` 决策与口径变更、写 `plan/HANDOFF-M5.md`，本地提交（不 push）。

## 三、既定口径清单（动了会打挂测试，改前先对照）

| # | 口径 | 位置 | 改它的代价 |
|---|---|---|---|
| C1 | 状态文本六值 + 未闭环集合 = {alarm, alarm_confirmed} | `contract/status.py` | DDL 的 CHECK 由枚举注入；迁移表两道检查全部重推 |
| C2 | 来源 `kind` 4 值、查证 `status` 3 值、**阈值轴**不启用原因码 9 个 | `contract/thresholds.py` | 核对队列与 README 统计口径全部重算 |
| C2b | **引擎轴**原因码 2 个：`no_applicable_rule`、`unit_inconsistent` | `alarm/engine.py`、`09 §2.4` | 改码要同步 `test_m2_discipline.py::test_engine_basis_vocabulary_is_documented` |
| **C2c** | **检核轴**原因码 5 个 + 违规类别 4 个 + 三条固定规则 id（`FREQ-MISSED-ROUND` / `FREQ-CONDITION-CHANGE` / `FREQ-INTENSIFY-AFTER-ALARM`）+ 结论用词只有「应核实」 | `compliance/auditor.py`、`08 §四/§五/§七` | 改任一项要同步 `plan/08` 与 `test_m3_discipline.py`；规则 id 是契约，`violation.rule_id` 已落库 |
| C3 | `SCHEMA_VERSION = 1`；版本不符直接 `ContractError`，不写迁移器（D21） | `pmc/__init__.py` | 改表必须 +1 并重建全部夹具库 |
| C4 | 退出码 0/1/2/3；`check` 降级 = 未闭环/待定值/原因码任一；**`audit` 降级 = 有应核实事项/有规则被门控挡住/有检核缺口 三者任一** | `errors.py`、`alarm/engine.py::degraded_exit`、`compliance/auditor.py::degraded_exit` | CI 步骤判定依赖它 |
| C5 | 分层禁令 + 内核禁 `random/socket/urllib/http/requests` + 非生成层禁 import `pmc.synth` + **判定路径禁时钟**（M3 起覆盖面含 `compliance` 层） | `test_layering.py`、`test_rng_determinism.py`、`test_synth_freeze.py`、`test_m2_discipline.py` | 新增依赖或跨层复用会红；先改测试口径再动代码。**bench 层不在禁 import `pmc.synth` 的名单里**（见 §二.1） |
| C6 | 规则集：无 `enabled` 字段、键白名单（M3 起含 `depth_max`）、`design_value` 不得进规则集、`window_days` 必配 `window_source`、`depth_max` 只配 `interval` 且至多一条空值兜底档 | `rules/loader.py` | 版本 +1 + `06` 一行 |
| C7 | 未核对的阈值 `value` 必须为 `null`；合成档位与夹具档位都不得写进 `data/` | `test_data_discipline.py`、`test_m2_discipline.py`、`test_m3_discipline.py` | 提前写数值 = 红，这是幻觉防线 |
| C8 | 字典条目字段白名单（不得出现阈值类字段）；14 项编码 | `catalog/items.py`、`data/dict/` | 加字段要同步测试与 `07` |
| C9 | pytest：`addopts` 不含 `-q`；跑测试一律 `-rs`；收集数变化逐项归因（当前 **331**） | `pyproject.toml`、`06 D14` | 双 `-q` 吞掉汇总行 |
| C10 | EOL：`* text=auto eol=lf`；scratch 只准落 `.tmp_verify/`、`.tmp_parse/` | `test_eol_guard.py` | 工作树出现 CR 的临时文件会挂四矩阵。**Windows 下 Write/脚本写文件必须显式 LF**（M3 实测四次踩到，见 §四） |
| C11 | 指标四态；README 指标表 M4 起由 `bench --markdown` 生成 | `README.md`、`test_readme_honesty.py` | 基准类指标行**不得**写"达标"，也不得写凭空数值 |
| C12 | 真值列 7 项、`event_type` 7 值、`token` 白名单 | `data/README §三`、`07 §6.1` | 改真值语义 = 已生成基准全部作废 |
| C13 | 假数据白名单形式（SYN 前缀工程名、`SYN-JK-2026-000N` 图号、199 号段…） | `data/README §三` | 新增形式先登记再使用 |
| C14 | 观测 CSV 表头固定 8 列、真值表头固定 7 列、`source_row` 从 1 起（表头=1） | `synth/freeze.py`、`ingest/csvio.py` | 改列 = 冻结产物重生成 + `07 §八/§十` 同步 |
| C15 | 导入行级原因码 11 个；文件级硬失败清单（含 XLSX 专有 3 条） | `csvio.REASON_CODES`、`07 §八`、`09 §十` | 行级集合被测试逐字锁定；加码要在 `06` 记一行 |
| C16 | 修订链语义：判定只取 `superseded_by IS NULL`；同值重复不产生空修订 | `store.py`、`alarm/engine.py`、`compliance/auditor.py`、`03 §2.1` | M3 的 `missed` 同样只认链上最新行；读链方式改了回归当场红 |
| C17 | 数值/日期/编号一律 ASCII `[0-9]`，不用 `\d` | `csvio` 三个正则、`_xlsx` 复用同一组 | 全角数字会被静默洗成合法值（D28）。检核的日期解析用 `split("-")` + `int()`，同样不碰 `\d` |
| C18 | 站点档案参数：`SYN-LJ3` 0.18 / 其余 0.62、季节幅度 0.12×控制值、窗口 3/5/7 天、间隔 1/2/7 天、工况深度 3.5–14.5 m | `synth/sites.py`、`07 §4.1` | 改它必须整目录重生成并重新冻结，夹具窗口目录与 **M3 检核条数期望**同步（`test_cli_m3.py` 里那些数字就是站档案的函数） |
| C19 | `synth --db` 建档不写控制值；`data/golden/` 属 M4 | `freeze.seed_ledger` | 写进数值 = 待定值纪律在数据层失效 |
| C20 | 判定整段重算 + upsert 保 id；`--round N` 打印第 N 轮但落库 1..N 全前缀 | `alarm/engine.py::persist`、`09 §5.4/§八` | 改增量会让修订链与延续状态分叉 |
| **C20b** | 检核整段替换落库：`DELETE FROM violation WHERE project_id=?` 后重插；`--from/--to` 只筛清单、不切断时间线（间隔仍按真实前一轮算） | `compliance/auditor.py::persist`、`08 §七` | 增量写会让上一轮结论残留成"历史违规"；范围切断时间线会把合规判成超间隔 |
| C21 | 夹具档位登记 `kind=user_input`，窗口按工程分目录，`--rules-dir` 是判定档位的唯一入口；**M3 延长：频率档位夹具数据面只准待在 `tests/fixtures/data_freq/`，靠全局 `--data-dir` 进入** | `tests/fixtures/`、`09 §六`、`08 §八`、D31/D32/D40 | 改成 `design_value` 或把条款转 verified = 撞禁项一 |
| **C22** | 模块 3 的输出对象复用契约层 `ViolationRecord`（`evidence` 类型注记 `Dict[str, object]`），检核层不另立 DTO | `contract/records.py`、`compliance/auditor.py`、`03 §5` | 两套 DTO 会让 M5 报告与追溯清单各读一套字段 |

## 四、本机环境事实（只写实测过的）

- **`python` 已不可用**：PATH 上第一个 `python` 解析到 `WindowsApps\python.exe`（Store 别名占位），任何命令都**静默退出 rc=49、零输出**。一律用 `py -3.8` / `py -3.12`（两者实测健康）。
- 未装 editable 包，`-m pmc` 需要 `PYTHONPATH=src`；pytest 靠 `pyproject` 的 `pythonpath=["src"]` 不需要。
- 已装：pytest 8.3.5（3.8）/ 9.1.1（3.12）、PyYAML 6.0.3（两端）。331 项 < 15 s，无需分批。
- **git `core.autocrlf=true`**（实测）→ 全新 clone 会把 LF 重写成 CRLF；`.gitattributes` + EOL 守门 + `synth --check` 是三重解药，别删。
- 全程 `PYTHONDONTWRITEBYTECODE=1`：本机有 Python 帧损坏家族（unknown opcode / 139 / 0xC0000005），出怪错先清 `__pycache__` 再判。
- Git Bash 里以 `/` 开头的参数会被 MSYS 静默转写；扫描字面值先跑阳性对照。
- **M3 新撞到的四个坑，M4 别再踩**：
  ① **Windows 上任何"写文件"路径都要显式 LF**：`Write` 工具与 `python open(path,"w")` 都会写成 CRLF，
     `test_eol_guard.py` 当场红（M3 一轮里红了 5 个文件）。写完立刻 `read_bytes().replace(b"\r\n", b"\n")` 回写，
     或统一 `open(path, "wb")` + `encode("utf-8")`（`freeze.py` 就是这么做的）。
  ② **`--data-dir` 必须写在子命令之前**：它是全局参数，`pmc audit --db X --data-dir Y` 会被 argparse 判成
     `unrecognized arguments`。CLI 测试里拼 argv 时把全局参数放最前。
  ③ **`synth --db` 不带 `--force` 在产物已存在时先抛 `SynthError`**（`write_blobs` 的冲突检查在建档之前），
     所以"只建档不重生成"要走 `freeze.build(...)` + `freeze.seed_ledger(conn, datas)`（M2/M3 测试都是这条路）。
  ④ **`find_data_dir` 认的是 marker 目录**（`dict/` `rulesets/` `clauses/` 三者皆须为目录）：
     造替身数据面时少一个就会被静默忽略并回落到真实 `data/`（M3 的夹具就是这么发现的），症状是"改了没生效"。
- M2/M3 已知的两个环境无关陷阱仍然有效：`\d` 认全角数字（D28）；反算/判定必须用**量化后**的落盘数值。
- 未实测（M4 若撞上要先定性再改代码）：`pip install -e` 的 build isolation 在本机代理环境的表现；PyInstaller；PySide6。
- 未实测但已确定不做：openpyxl / xlrd / pandas 一律不装（`06 D03`/D34）。

## 五、关键命令速查

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src                              # `-m pmc` 需要它（本机没装 editable 包）
py -3.12 -X utf8 -m pytest -rs                     # 全量 331 项
py -3.8  -X utf8 -m pytest -rs                     # 老解释器同数全绿才算过
py -3.12 -X utf8 -m pmc selfcheck                  # 期望 SELF_CHECK_OK，rc=0（登记 5 条：located 1 / pending 4）
py -3.12 -X utf8 -m pmc rulesets                   # 规则 17 条，可参与判定 0 条
py -3.12 -X utf8 -m pmc synth --check              # 62 个产物逐字节一致，rc=0

# M3 检核通路一条龙（建档走 freeze，不重生成冻结产物）
py -3.12 -X utf8 -c "import sqlite3,sys;sys.path.insert(0,'src');from pmc.synth import freeze;c=sqlite3.connect('.tmp_verify/m4.sqlite');print(freeze.seed_ledger(c, freeze.build('data',20260107,3)[0]))"
py -3.12 -X utf8 -m pmc import data/raw/SYN-YYCG/round-18.csv --project SYN-YYCG --round 18 --db .tmp_verify/m4.sqlite
py -3.12 -X utf8 -m pmc audit --db .tmp_verify/m4.sqlite --project SYN-YYCG
#   ↑ 生产数据面：零违规 + 6 条不生效规则，rc=1
py -3.12 -X utf8 -m pmc --data-dir tests/fixtures/data_freq audit --db .tmp_verify/m4.sqlite --project SYN-YYCG
#   ↑ 夹具数据面：四类都有结论（完整站需先导入全部轮次并跑 check，见 tests/test_cli_m3.py）
git status --porcelain                             # 必须只剩 .qoder-credits/
```

**退出码一律单独取**：`cmd; echo "rc=$?"`。不要 `cmd | tail`、不要把 `${PIPESTATUS[0]}` 写进同一条管道的参数里。
`-X utf8` 不能省：Windows 控制台默认 codepage 会让中文断言与 CSV/XLSX 读写出乱码。

## 六、M4 DoD（逐项打勾，未达成写偏差说明）

- [ ] `plan/10-基准与评测.md` 完成：四态指标的分子/分母/边界、`also_expect` 对账语义、两类"导入器考题"怎么算 pass
- [ ] `pmc bench` 对三座基坑出逐起对账表 + 聚合四态指标 + 漏报清单；期望值全部来自 `data/truth/*.truth.csv`
- [ ] 档位来源问题（§二.1 / P08）有明确结论并落 `06`；**不把任何夹具数值写进 `data/`**
- [ ] `unit_error` / `duplicate_report` 不出现在引擎漏报清单里（它们属导入器与修订链的考题）
- [ ] 门槛如实落盘：召回 ≥ 0.95、误报 = 0、定位误差中位 0 且 P95 ≤ 1；量不了就写「不可判 / 不可用」，不写达标、不编数值
- [ ] `bench --markdown` 产出的表能直接替换 README 指标节；README 与实跑逐行对账
- [ ] 门禁四连（`selfcheck → synth --check → pytest → bench`）一条脚本命令跑通，任何一环红就非 0 退出
- [ ] M2/M3 的逐起与逐条对账仍成立（bench 的逐起表与 `test_alarm_regression.py`、`test_cli_m3.py` 的期望互相印证）
- [ ] 新增测试全绿且 py3.8 / py3.12 收集数一致（当前基线 331）；`plan/00`/`05`/`06` 回写；`HANDOFF-M5.md` 落盘
- [ ] 本地提交；**不 push、不建仓、不打 tag**（对外动作待用户授权）

## 七、禁止事项

1. 不把任何阈值数值写成 `verified` —— 除非有用户提供的材料或官方可直连原文，且登记凭证（禁项一；纪律测试会红，那是刻意的）。
2. 不改 `plan/01-题目与任务要求.md`（题目已定稿 v1）；范围扩张一律走 `06` 的决策表。
3. 不加 LLM、不做验算、不接设备、不做 BIM/大屏（题面 §二 明确不做、§六 边界）。
4. 不为了"测试通过/指标好看"放宽纪律断言；纪律测试红了说明数据或代码越界了。
5. **不手改 `data/raw`、`data/truth` 里任何文件**；要改数值就改生成器再 `--force` 整目录重生成，让 `--check` 说话。
6. 不引入第三方 xlsx 解析库；不把 M2 的 XLSX 读侧搬到 `src/` 之外去写（写侧属 M5）。
7. 不在工作树根目录撒临时产物；验证脚本与缓存只落 `.tmp_verify/`、`.tmp_parse/`。
8. 不 push、不建仓、不发 release、不改历史 —— 属 M6 且必须先取得用户确认。
9. 派子代理时限定可写目录并要求逐文件 `git diff` 审读；中间证据（抓取缓存/manifest）不许由它删除。
10. 违规结论只写"应核实"，不写"已确认违规/已认定超标"（题面 §六.4）；评测结论只写指标与四态，不写"基坑安全"。
11. **不改 M3 的三条固定规则 id 与四类 `kind`**（`violation.rule_id` 已落库，M5 报告要读它们）；要改先改 `plan/08 §四`。

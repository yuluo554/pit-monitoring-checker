# HANDOFF · M3（条款核对 + 合规检核）

> 用法：新对话直接说「读取 plan/HANDOFF-M3.md 并继续完成任务」。
> 上一棒（M2 报警内核 + XLSX 输入）已完成并本地提交；本文件是唯一续接载体，零上下文也能开工。

## 一、当前进度（M2 交付了什么）

| 面 | 已落地 | 验证方式 |
|---|---|---|
| 判定细则文档 | `plan/09`：有效阈值选取次序（档案值→条文回退→待定）、双控合成表、速率窗口按日历日、未闭环置位/清除的确切定义、迁移表两道报错点、`check` 输出契约与退出码触发集合、夹具档位边界 | 与 `pmc/alarm/engine.py` 逐条对照；`test_m2_discipline.py` 要求新增原因码先在 `09` 登记 |
| 判定内核 | `pmc/alarm/engine.py`：读 `observation`（只取 `superseded_by IS NULL`）+ `point` 三态 + 门控后规则 → `AlarmRecord` → upsert `alarm_state`；整段重算而非增量 | `tests/test_alarm_engine.py` 15 项 + `tests/test_alarm_state_machine.py` 9 项 |
| 状态机 | 迁移表落地成行为断言：轮次间 `prev_final→base`、行内处置 `base→final` 两道；处置指向未报警行当场报错 | `test_alarm_state_machine.py`（含"已处置不回退成已确认"） |
| 未闭环延续 | 回落轮次仍写 `alarm`/`alarm_confirmed`，`unclosed=1`，`first_alarm_round_id` 指向首个报警轮次；`confirm` 不闭环、`handle` 闭环、`reobserve` 不改状态 | 回归：`SYN-PL-04` R18→R19/20/21 数值已回落仍延续 |
| 待定值纪律 | 两判据全不可判 → `undetermined` 且阈值列空；一判据可判一判据待定 → 按可判的出结论 + 原因码（降级 1）；新增引擎轴 2 码 `no_applicable_rule`/`unit_inconsistent` | DTO + DDL CHECK 双向；`test_cli_m2.py` 打印层断言"打 `-` 不打 0" |
| `pmc check` | `--db --project [--round N] [--rules-dir DIR] [--dry-run]`：每测点状态 + 触发依据 + 累计/速率值与阈值 + 条款号 + 原因码 + 未闭环清单；退出码 0/1/2 | `tests/test_cli_m2.py` 8 项端到端 |
| XLSX 输入（P07） | `pmc/ingest/xlsx.py`：stdlib `zipfile`+`ElementTree` 读 sheet XML、sharedStrings、styles 认日期格式、1900 序列号换算；产出即 `csvio.parse_rows` 的输入 | `tests/test_ingest_xlsx.py` 11 项，含"同内容 CSV 与 XLSX 逐行一致" |
| 夹具档位 | `tests/fixtures/point_grades.json`（14 项档位，登记 `kind=user_input` + `FIXTURE-GRADE:` 自述）+ `tests/fixtures/rulesets/fx_{lj3,zhdq,yycg}/`（窗口 3/5/7 + 预警比例 0.7）；装载器 `tests/_fixtures.py` 只读 JSON、不 import `pmc.synth` | `test_m2_discipline.py`：夹具 id 出现在 `data/` 即红（带阳性对照） |
| 回归对账 | 11 起事件逐一对账：6 起应报警命中 `expected_first_alarm_round` 且期望轮次之前零报警；`SYN-LJ3` 1568 行零 `alarm`；缺测/单位错误/重复上报三条通路各自验证 | `tests/test_alarm_regression.py` 14 项（session 级整库导入） |
| 确定性 | 判定路径 AST 断言无时钟（`now/today/utcnow/localtime/monotonic/time`）、无 `random`/网络模块；同库两次 `check` 逐列一致 | `test_m2_discipline.py` + `test_alarm_regression.py::test_check_is_reproducible_row_by_row` |
| 全量测试 | **274 项，py3.8.8 与 py3.12 各一轮，全绿 0 跳过**（12–14 s） | `python -X utf8 -m pytest -rs` |

**M2 的既成事实（不是缺陷）**：
`data/rulesets/*` 仍全 `value: null`、条款登记仍全 `pending`、`point` 表仍无控制值 ——
所以不带 `--rules-dir` 与夹具档位时，`pmc check` 对 `SYN-LJ3` 出 **1568 行全待定值 + 退出码 1**。
这是 R1/R2 纪律在生效。带数值的判定必须由用户在测点档案录入，或 M3 核对条文后回退供货。

**未验证项（如实留档）**：① `pip install -e ".[dev]"` 仍未在干净 venv 按 README 原文逐条跑过；② CI 四矩阵未实跑（M2 仍未 push）。
新增的 6 个测试文件让本机收集数 199 → 274；CI 的收集数必须与本机一致，M3 若再新增文件记得复核。

**M2 收尾基线**：`git status --porcelain` 只剩 `.qoder-credits/`（工具产物，不提交）；`synth --check` 过；双解释器 274 项同数全绿。

## 二、M3 待办（按顺序，每步都能单独演示）

1. **先写 `plan/08-规则集与条款核对.md`**：逐条列出要核对的条文（GB 50497-2019 报警值表、监测频率表；JGJ 120-2012；住建部 37 号公告），
   每条给"渠道 + 原文定位（条号/表号）+ 核对结论 + 是否供货数值"；无官方可直连原文的条目一律留 `pending` 并写明原因。
2. **登记表转 verified 的通路**：`data/clauses/register.json` 里被核实为 `verified` 的条目补 `clause_no`/`table_no`；
   同时把标准回退档的**数值**写入 `data/rulesets/alarm_dual_control.json` 并 `version +1`（这是口径 C6/C7 的变更，须在 `06 §五` 记一行）；
   `pmc rulesets` 的"可参与判定条数"必须随之上升，`selfcheck` 仍 `SELF_CHECK_OK`。
   **前提**：数值只能来自用户提供的材料或官方原文页；拿不到就保持 `pending`，不得为让测试变绿而填数（禁项一）。
3. **合规检核** `pmc/compliance/auditor.py`：读 `obs_round` 时间线 + 工况 + M2 的 `alarm_state.unclosed`，产出四类 `violation`：
   `missed`（排定轮次无有效观测）、`over_interval`（间隔超当前工况上限）、`stale_frequency`（工况变更后仍按旧频率）、
   `no_intensified_after_alarm`（报警后未加密观测 —— `SYN-YYCG` 是天然反例，`SYN-ZHDQ` 第 10–12 轮是正例）。
   每条必须挂 `rule_id` + `clause_ids` + `evidence_json`（间隔天数、上一轮时间、生效工况 code）。
4. **`pmc audit [--project] [--from --to]`**：违规清单按条款分组；**只报"应核实"，不写"已确认违规"**（题面 §六.4）；退出码 0/1/2。
5. **回退供货的行为证明**：同一条序列在"仅档案设计值"与"仅标准回退"两种来源下结论一致，且回退行的 `clause_ids` 非空（DDL CHECK 反证仍红）。
6. **回归**：夹具档位与核对后的标准档各跑一遍 `check`，`SYN-LJ3` 仍零 `alarm`；新增测试双解释器同数。
7. **收尾回写**：`plan/00` 状态、`plan/05` M3 行打勾（含偏差说明）、`plan/06` 决策与口径变更、写 `plan/HANDOFF-M4.md`，本地提交（不 push）。

## 三、既定口径清单（动了会打挂测试，改前先对照）

| # | 口径 | 位置 | 改它的代价 |
|---|---|---|---|
| C1 | 状态文本六值 + 未闭环集合 = {alarm, alarm_confirmed} | `contract/status.py` | DDL 的 CHECK 由枚举注入；迁移表两道检查全部重推 |
| C2 | 来源 `kind` 4 值、查证 `status` 3 值、**阈值轴**不启用原因码 9 个 | `contract/thresholds.py` | 核对队列与 README 统计口径全部重算 |
| C2b | **引擎轴**原因码 2 个：`no_applicable_rule`、`unit_inconsistent`（M2 新增，D33） | `alarm/engine.py`、`plan/09 §2.4` | 新增引擎轴码要同步改 `test_m2_discipline.py::test_engine_basis_vocabulary_is_documented` |
| C3 | `SCHEMA_VERSION = 1`；版本不符直接 `ContractError`，不写迁移器（D21） | `pmc/__init__.py` | 改表必须 +1 并重建全部夹具库 |
| C4 | 退出码 0/1/2/3；`check` 的降级触发集合 = 未闭环 / 待定值 / 原因码非空 三者任一（`09 §七`） | `errors.py`、`alarm/engine.py::degraded_exit`、`test_cli_m2.py` | CI 步骤判定依赖它 |
| C5 | 分层禁令 + 内核禁 `random/socket/urllib/http/requests` + 非生成层禁 import `pmc.synth` + **判定路径禁时钟**（M2 扩展） | `test_layering.py`、`test_rng_determinism.py`、`test_synth_freeze.py`、`test_m2_discipline.py` | 新增依赖或跨层复用会红；先改测试口径再动代码 |
| C6 | 规则集：无 `enabled` 字段、键白名单、`design_value` 不得进规则集、`window_days` 必配 `window_source` | `rules/loader.py` | 版本 +1 + `06` 一行 |
| C7 | 未核对的阈值 `value` 必须为 `null`；合成档位与夹具档位都不得写进 `data/` | `test_data_discipline.py`、`test_m2_discipline.py` | 提前写数值 = 红，这是幻觉防线 |
| C8 | 字典条目字段白名单（不得出现阈值类字段）；14 项编码 | `catalog/items.py`、`data/dict/` | 加字段要同步测试与 `07` |
| C9 | pytest：`addopts` 不含 `-q`；跑测试一律 `-rs`；收集数变化逐项归因（当前 **274**） | `pyproject.toml`、`06 D14` | 双 `-q` 吞掉汇总行 |
| C10 | EOL：`* text=auto eol=lf`；scratch 只准落 `.tmp_verify/`、`.tmp_parse/` | `test_eol_guard.py` | 工作树出现 CR 的临时文件会挂四矩阵 |
| C11 | 指标四态；README 指标表 M4 起由 `bench --markdown` 生成 | `README.md`、`test_readme_honesty.py` | 基准类指标行**不得**写"达标"，也不得写凭空数值 |
| C12 | 真值列 7 项、`event_type` 7 值、`token` 白名单 | `data/README §三`、`07 §6.1` | 改真值语义 = 已生成基准全部作废 |
| C13 | 假数据白名单形式（SYN 前缀工程名、`SYN-JK-2026-000N` 图号、199 号段…） | `data/README §三` | 新增形式先登记再使用 |
| C14 | 观测 CSV 表头固定 8 列、真值表头固定 7 列、`source_row` 从 1 起（表头=1） | `synth/freeze.py`、`ingest/csvio.py` | 改列 = 冻结产物重生成 + `07 §八/§十` 同步 |
| C15 | 导入行级原因码 11 个；文件级硬失败清单（M2 起含"非 zip / 无工作表 / 超出表头列数"三条 XLSX 专有） | `csvio.REASON_CODES`、`07 §八`、`09 §十` | 行级集合被测试逐字锁定；加码要在 `06` 记一行 |
| C16 | 修订链语义：判定只取 `superseded_by IS NULL`；同值重复不产生空修订 | `store.py`、`alarm/engine.py`、`03 §2.1` | 读链方式改了回归当场红 |
| C17 | 数值/日期/编号一律 ASCII `[0-9]`，不用 `\d` | `csvio` 三个正则、`_xlsx` 复用同一组 | 全角数字会被静默洗成合法值（D28） |
| C18 | 站点档案参数：`SYN-LJ3` 0.18 / 其余 0.62、季节幅度 0.12×控制值、窗口 3/5/7 天 | `synth/sites.py`、`07 §4.1` | 改它必须整目录重生成并重新冻结，夹具窗口目录同步 |
| C19 | `synth --db` 建档不写控制值；`data/golden/` 属 M4 | `freeze.seed_ledger` | 写进数值 = 待定值纪律在数据层失效 |
| C20 | 判定整段重算 + upsert 保 id（处置外键不失效）；`--round N` 打印第 N 轮但落库 1..N 全前缀 | `alarm/engine.py::persist`、`09 §5.4/§八` | 改增量会让修订链与延续状态分叉 |
| C21 | 夹具档位登记 `kind=user_input`，窗口按工程分目录，`--rules-dir` 是唯一入口 | `tests/fixtures/`、`09 §六`、D31/D32 | 改成 `design_value` 或把条款转 verified = 撞禁项一 |

## 四、本机环境事实（只写实测过的）

- **`python` 已不可用**：PATH 上第一个 `python` 解析到 `WindowsApps\python.exe`（Store 别名占位），任何命令都**静默退出 rc=49、零输出**。一律用 `py -3.8` / `py -3.12`（两者实测健康）。
- 未装 editable 包，`-m pmc` 需要 `PYTHONPATH=src`；pytest 靠 `pyproject` 的 `pythonpath=["src"]` 不需要。
- 已装：pytest 8.3.5（3.8）/ 9.1.1（3.12）、PyYAML 6.0.3（两端）。274 项 < 14 s，无需分批。
- **git `core.autocrlf=true`**（实测）→ 全新 clone 会把 LF 重写成 CRLF；`.gitattributes` + EOL 守门 + `synth --check` 是三重解药，别删。
- 全程 `PYTHONDONTWRITEBYTECODE=1`：本机有 Python 帧损坏家族（unknown opcode / 139 / 0xC0000005），出怪错先清 `__pycache__` 再判。
- Git Bash 里以 `/` 开头的参数会被 MSYS 静默转写；扫描字面值先跑阳性对照。
- **M2 实测到的三个坑，M3 别再踩**：
  ① 写文件时先 `open(path,"w")` 再算内容 → 计算报错就把源文件截断成空（我这么废掉过一个 helper，靠重写恢复）。先算好字符串再开文件。
  ② Python 字符串里嵌 ASCII 双引号会让 heredoc 脚本整段 SyntaxError；文档文本用「」或转义。
  ③ `Path.glob()` 返回生成器，`assert not p.glob("*")` 恒为假 —— 要 `list(...)`。
- M2 之前已知的两个环境无关陷阱仍然有效：`\d` 认全角数字（D28）；反算/判定必须用**量化后**的落盘数值。
- 未实测（M3 若撞上要先定性再改代码）：`pip install -e` 的 build isolation 在本机代理环境的表现；PyInstaller；PySide6。
- 未实测但已确定不做：openpyxl / xlrd / pandas 一律不装（`06 D03`/D34）。

## 五、关键命令速查

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src                              # `-m pmc` 需要它（本机没装 editable 包）
py -3.12 -X utf8 -m pytest -rs                     # 全量 274 项
py -3.8  -X utf8 -m pytest -rs                     # 老解释器同数全绿才算过
py -3.12 -X utf8 -m pmc selfcheck                  # 期望 SELF_CHECK_OK，rc=0
py -3.12 -X utf8 -m pmc rulesets                   # 看"可参与判定条数"，M3 核对后必须上升
py -3.12 -X utf8 -m pmc synth --check              # 62 个产物逐字节一致，rc=0

# M2 判定通路（夹具档位）一条龙
py -3.12 -X utf8 -m pmc init --db .tmp_verify/l.sqlite
py -3.12 -X utf8 -m pmc synth --seed 20260107 --sites 3 --db .tmp_verify/l.sqlite
py -3.12 -X utf8 -m pmc import data/raw/SYN-YYCG/round-18.csv --project SYN-YYCG --round 18 --db .tmp_verify/l.sqlite
py -3.12 -X utf8 -m pmc check --db .tmp_verify/l.sqlite --project SYN-YYCG --round 21 \
    --rules-dir tests/fixtures/rulesets/fx_yycg     # rc=1，未闭环清单里 PL-04 起点是 R18
py -3.12 -X utf8 -m pmc check --db .tmp_verify/l.sqlite --project SYN-LJ3    # 不带夹具：全待定值，rc=1
git status --porcelain                             # 必须只剩 .qoder-credits/
```

**退出码一律单独取**：`cmd; echo "rc=$?"`。不要 `cmd | tail`、不要把 `${PIPESTATUS[0]}` 写进同一条管道的参数里。
`-X utf8` 不能省：Windows 控制台默认 codepage 会让中文断言与 CSV/XLSX 读写出乱码。

## 六、M3 DoD（逐项打勾，未达成写偏差说明）

- [ ] `plan/08` 条款核对文档完成：每条给渠道 + 原文定位 + 是否供货数值
- [ ] `pmc audit` 对 `SYN-ZHDQ`（报警后有加密轮次）与 `SYN-YYCG`（报警后不加密）给出相反结论，且逐条挂 `rule_id` + 条款号
- [ ] 四类违规都有夹具场景被触发到：`missed` / `over_interval` / `stale_frequency` / `no_intensified_after_alarm`
- [ ] 工况变更当轮即按新频率判：`SYN-YYCG` 四个工况段（R9/R17/R25）都有对应结论
- [ ] 已核对条目转 `verified` 时：`selfcheck` 仍 OK、`rulesets` 的可判定条数上升、规则集 `version` +1、`06 §五` 记一行
- [ ] 拿不到官方原文的条目仍 `pending` 且不供货数值；`data/discipline` 扫描不因 M3 变红
- [ ] 标准回退档的判定行 `clause_ids` 非空（DDL CHECK 反证仍红）
- [ ] 未闭环标记被 M3 消费：`no_intensified_after_alarm` 只读 `alarm_state.unclosed`，不重算报警
- [ ] 新增测试全绿且 py3.8 / py3.12 收集数一致；`plan/00`/`05`/`06` 回写；`HANDOFF-M4.md` 落盘
- [ ] 本地提交；**不 push、不建仓、不打 tag**（对外动作待用户授权）

## 七、禁止事项

1. 不把任何阈值数值写成 `verified` —— 除非有用户提供的材料或官方可直连原文，且登记凭证（禁项一；`test_data_discipline.py` 会红，那是刻意的）。
2. 不改 `plan/01-题目与任务要求.md`（题目已定稿 v1）；范围扩张一律走 `06` 的决策表。
3. 不加 LLM、不做验算、不接设备、不做 BIM/大屏（题面 §二 明确不做、§六 边界）。
4. 不为了"测试通过"放宽纪律断言；纪律测试红了说明数据或代码越界了。
5. **不手改 `data/raw`、`data/truth` 里任何文件**；要改数值就改生成器再 `--force` 整目录重生成，让 `--check` 说话。
6. 不引入第三方 xlsx 解析库；也不把 M2 的 XLSX 读侧搬到 `src/` 之外去写（写侧属 M5）。
7. 不在工作树根目录撒临时产物；验证脚本与缓存只落 `.tmp_verify/`、`.tmp_parse/`。
8. 不 push、不建仓、不发 release、不改历史 —— 属 M6 且必须先取得用户确认。
9. 派子代理时限定可写目录并要求逐文件 `git diff` 审读；中间证据（抓取缓存/manifest）不许由它删除。
10. 违规结论只写"应核实"，不写"已确认违规/已认定超标"（题面 §六.4）。

# HANDOFF · M5（xlsx 报告导出 + PySide6 界面 + onedir exe）

> **✅ 已执行完毕（M5 于 2026-10-07 交付并本地提交），本文件降为历史资料。**
> 续接请读 `plan/HANDOFF-M6.md`；§六 的 DoD 已就地勾选（含偏差说明），§三 的 C1–C23 口径全部仍然有效，
> M5 新增的 C24–C29 记录在 `HANDOFF-M6 §三`。
> 用法：新对话直接说「读取 plan/HANDOFF-M6.md 并继续完成任务」。
> 上一棒（M4 内置基准评测）已完成并本地提交；本文件是唯一续接载体，零上下文也能开工。

## 一、当前进度（M4 交付了什么）

| 面 | 已落地 | 验证方式 |
|---|---|---|
| 基准口径文档 | `plan/10-基准与评测.md`：P08 档位来源结论、两个评测面、四态指标的分子/分母/边界、10 个 `also_expect` token 的对账语义、两类"导入器考题"怎么算 pass、`--json` 字段契约、`--markdown` 列序、golden 结构、门禁链口径 | `test_m4_discipline.py::test_bench_vocabulary_is_documented`（代码常量逐个必须在 `10` 出现，带阳性对照） |
| 评测内核 | `pmc/bench/runner.py`：`:memory:` 台账 → `freeze` 建档 → 逐轮 M1 导入 → 合成档位挂档 → M2 判定 → **从落库行回读**（只认链头）→ 逐起对账 → 七项四态指标 → golden 位级对账 | `test_m4_bench.py` 21 项（含逐起、四态、退出码、README 逐行对账） |
| 评测结果 | 合成自证档位面：11 起事件全部通过，6 起应报警事件**首超误差全 0**，召回 6/6、误报 0/11309、未闭环 49/49、待定值脱空 0/11364、回执 58/58；台账面：七项里四项 `不可用`、三项常驻断言 `达标` | `py -3.12 -X utf8 -m pmc bench` 退出码 0；`--plane ledger` 退出码 1 |
| golden | `data/golden/bench_synth.json`：`schema=pmc-golden-1` + `notice`、`grade_profile`、seed、sites、三站真值 sha256、逐起结论、七项指标；**无档位数值、无时间戳、非 CSV** | 默认运行与文件逐字节比对；漂移出 `BENCH_GOLDEN DRIFT` + 码 1；两条漂移测试（改结论 / 删事件）当场红 |
| 档位来源（P08） | 采用「`bench` 从 `pmc.synth.profile` + `sites` 取档位」，落 `06 D41`；交接文档声称"禁令不覆盖 bench"**实测不成立**，改为**具名装配点例外 + 三条补偿断言** | `test_synth_freeze.py` 的 `SYNTH_ASSEMBLY_POINTS`、`test_m4_discipline.py` 的例外名单/import 白名单/golden 数值扫描 |
| README | 指标节**七行 = `bench --plane ledger --markdown` 的逐字输出**；新增「基准评测」节承载 `--plane synth` 整表 + 门禁四连 + 数据面纪律 | `test_m4_bench.py` 两条"README 与实跑逐行对账"断言（不是手抄）；`test_readme_honesty.py` 全绿 |
| 门禁链 | `scripts/gate.py`：`selfcheck → synth --check → pytest → bench --all --json → bench --plane ledger`，**逐环断言期望码 0/0/0/0/1** | `test_m4_discipline.py::test_gate_chain_steps_and_expected_exit_codes` + `test_readme_and_ci_point_at_the_same_gate_chain` |
| CI | 新增 3 步：`synth --check`、`bench --all --json`、台账面反证（bash 里显式要求退出码 1） | `test_ci_workflow.py` 全绿（步骤名带冒号必须加引号、禁 `| tail`/`| head`/`PIPESTATUS`） |
| 全量测试 | **369 项，py3.8 与 py3.12 各一轮，全绿 0 跳过**（20 s / 25 s） | `py -3.12 -X utf8 -m pytest -rs` |

**M4 的既成事实（不是缺陷）**：生产数据面（`data/rulesets` 全 null + 档案无控制值）跑 `bench` 出**四项 `不可用`**，
退出码 1；合成面的全对**不等于**符合规范 —— `BENCH_NOTES` 第一行每次都写 `plane_is_synthetic`。
数值仍只有两个合法入口：① 用户在测点档案录入设计报警值；② 用户提供材料后按 `08 §三` 的分档结构填。
**M5 不许为了让报告里出现数字就把夹具或合成档位写进 `data/`**（禁项一，`test_data_discipline.py` 会红）。

**未验证项（如实留档）**：① `pip install -e ".[dev]"` 仍未在干净 venv 按 README 原文逐条跑过；② CI 四矩阵未实跑（M4 仍未 push）；
③ PyInstaller 与 PySide6 在本机完全未实测 —— M5 撞上要先定性再改代码。
M4 新增 2 个测试文件让本机收集数 331 → 369；CI 收集数必须与本机一致，M5 若再新增文件记得复核。

**M4 收尾基线**：`git status --porcelain` 只剩 `.qoder-credits/`（工具产物，不提交）；`synth --check` 过；双解释器 369 项同数全绿；`scripts/gate.py` 五环全过。

## 二、M5 待办（按顺序，每步都能单独演示）

1. **`pmc report --kind daily|weekly|stage`**：标准库 `zipfile` + 手写 OOXML 直写 xlsx（`06 D03`/D34：不装 openpyxl）；
   日报/周报/阶段报告三形态；每个数字挂追溯行（`contract/records.py::TraceRow`，M0 就定好的 DTO，别另立一套）。
2. **原生过程线图**：xlsx 原生 chart（`charts/chart1.xml` + drawing），不用外部图片；测点 × 轮次 × 累计值 + 阈值线。
3. **`bench` 的逐起表进报告**：报告的"判定结果"列与基准的"结论"列必须同源（C22 + `10 §十一.1`），
   M5 直接读 `ViolationRecord` / 判定 DTO，不重算。
4. **签字栏与免责**：空白签字栏不得渲染成"已审核"；每份报告带"不判基坑是否安全"的边界句（题面 §六）。
5. **PySide6 多页签界面**：台账 / 导入 / 判定 / 检核 / 报告五页签；offscreen 冒烟测试常驻（`pyproject` 的 `gui` extras）。
6. **PyInstaller onedir 打包 + 构建红线断言**：`data/` 内嵌、`find_data_dir` 的冻结通路（`sys.frozen`/`_MEIPASS`）实测；
   红线 = 产物里无外链/无绝对路径/无用户名/无创建时间；`dist/` 审计白名单对账。
7. **回归**：M2/M3/M4 的逐起、逐条、逐行对账仍成立；新增测试双解释器同数。
8. **收尾回写**：`plan/00` 状态、`plan/05` M5 行打勾（含偏差说明）、`plan/06` 决策与口径变更、`plan/11` 打包文档、写 `plan/HANDOFF-M6.md`，本地提交（不 push）。

## 三、既定口径清单（动了会打挂测试，改前先对照）

| # | 口径 | 位置 | 改它的代价 |
|---|---|---|---|
| C1 | 状态文本六值 + 未闭环集合 = {alarm, alarm_confirmed} | `contract/status.py` | DDL 的 CHECK 由枚举注入；迁移表两道检查全部重推 |
| C2 | 来源 `kind` 4 值、查证 `status` 3 值、**阈值轴**不启用原因码 9 个 | `contract/thresholds.py` | 核对队列与 README 统计口径全部重算 |
| C2b | **引擎轴**原因码 2 个：`no_applicable_rule`、`unit_inconsistent` | `alarm/engine.py`、`09 §2.4` | 改码同步 `test_m2_discipline.py` |
| C2c | **检核轴**原因码 5 个 + 违规类别 4 个 + 三条固定规则 id + 结论用词只准「应核实」 | `compliance/auditor.py`、`08 §四/§五/§七` | 改任一项同步 `plan/08` 与 `test_m3_discipline.py` |
| **C2d** | **评测轴**原因码 9 个（`plane_is_synthetic` / `clauses_unverified` / `no_shippable_conclusion` / `small_sample_p95` / `truth_shrunk` / `golden_missing` / `golden_written` / `golden_drift` / `subset_skips_aggregate_golden`）+ 四态词 + 结论词 7 个 + 对账项 9 个 | `bench/runner.py`、`10 §三/§四/§七` | 改任一项同步 `plan/10` 与 `test_m4_discipline.py`；`10` 是单一事实源，代码常量必须逐个能在文档里找到 |
| C3 | `SCHEMA_VERSION = 1`；版本不符直接 `ContractError`，不写迁移器（D21） | `pmc/__init__.py` | 改表必须 +1 并重建全部夹具库 |
| C4 | 退出码 0/1/2/3；`check` 降级 = 未闭环/待定值/原因码任一；`audit` 降级 = 应核实/规则被挡/检核缺口任一；**`bench` 降级 = 任一指标非达标 / 有漏报 / 有考题失败 / golden 缺失或漂移 / 台账面（码 1）** | `errors.py`、各层 `degraded_exit` | CI 步骤与 `scripts/gate.py` 的期望码依赖它 |
| C5 | 分层禁令 + 内核禁 `random/socket/urllib/http/requests` + **`pmc.synth` 只准 `synth/` 包与具名装配点（顶层模块 + `bench/`）引用** + 判定路径禁时钟（覆盖 `compliance` 与 **`bench`**） | `test_layering.py`、`test_synth_freeze.py`、`test_m2_discipline.py`、`test_m4_discipline.py` | 新增依赖或跨层复用会红；`bench` 只准 import `profile/sites/freeze`，碰 `generator/rng` 即红。**M5 的 `report/gui` 层若想要基准数值，读 `BenchReport` 而不是 import `pmc.synth`** |
| C6 | 规则集：无 `enabled` 字段、键白名单（含 `depth_max`）、`design_value` 不得进规则集、`window_days` 必配 `window_source`、`depth_max` 只配 `interval` 且至多一条空值兜底档 | `rules/loader.py` | 版本 +1 + `06` 一行 |
| C7 | 未核对的阈值 `value` 必须为 `null`；**合成档位与夹具档位都不得写进 `data/`**（含 `data/golden/`：数值+单位形态一并扫） | `test_data_discipline.py`、`test_m2/m3/m4_discipline.py` | 提前写数值 = 红，这是幻觉防线 |
| C8 | 字典条目字段白名单（不得出现阈值类字段）；14 项编码 | `catalog/items.py`、`data/dict/` | 加字段要同步测试与 `07` |
| C9 | pytest：`addopts` 不含 `-q`；跑测试一律 `-rs`；收集数变化逐项归因（当前 **369**） | `pyproject.toml`、`06 D14` | 双 `-q` 吞掉汇总行 |
| C10 | EOL：`* text=auto eol=lf`；scratch 只准落 `.tmp_verify/`、`.tmp_parse/` | `test_eol_guard.py` | **Windows 下 Write/脚本写文件必须显式 LF**（M3 实测四次踩到） |
| C11 | 指标四态；README 指标节由 `bench --plane ledger --markdown` 生成、基准评测节由 `--plane synth` 生成，**两节都由测试逐行对账** | `README.md`、`test_readme_honesty.py`、`test_m4_bench.py` | 基准类指标行**不得**写"达标"，指标节不得出现凭空百分比数值（`100.0%`、`95.x%`、`0.95x` 都算） |
| C12 | 真值列 7 项、`event_type` 7 值、`token` 白名单（`unclosed>=N` 是前缀形态） | `data/README §三`、`07 §6.1`、`bench/runner.TOKEN_TO_CHECK` | 改真值语义 = 已生成基准全部作废；未知 token 当场 `InputError` |
| C13 | 假数据白名单形式（SYN 前缀工程名、`SYN-JK-2026-000N` 图号、199 号段…） | `data/README §三` | 新增形式先登记再使用 |
| C14 | 观测 CSV 表头固定 8 列、真值表头固定 7 列、`source_row` 从 1 起（表头=1） | `synth/freeze.py`、`ingest/csvio.py` | 改列 = 冻结产物重生成 + `07 §八/§十` 同步 |
| C15 | 导入行级原因码 11 个；文件级硬失败清单（含 XLSX 专有 3 条） | `csvio.REASON_CODES`、`07 §八`、`09 §十` | 行级集合被测试逐字锁定 |
| C16 | 修订链语义：判定与**评测**都只取 `superseded_by IS NULL`；同值重复不产生空修订 | `store.py`、`alarm/engine.py`、`compliance/auditor.py`、`bench/runner.py` | M4 的 `persist_exact`/`import_revision=2` 两条对账直接读链，改读法当场红 |
| C17 | 数值/日期/编号一律 ASCII `[0-9]`，不用 `\d` | `csvio`、`_xlsx`、`bench/runner` 的 token 解析 | 全角数字会被静默洗成合法值（D28） |
| C18 | 站点档案参数：`SYN-LJ3` 0.18 / 其余 0.62、季节幅度 0.12×控制值、窗口 3/5/7 天、间隔 1/2/7 天、工况深度 3.5–14.5 m | `synth/sites.py`、`07 §4.1` | 改它必须整目录重生成并重新冻结，夹具窗口目录、`test_cli_m3.py` 的检核条数、**`data/golden/` 全部字段**同步 |
| C19 | `synth --db` 建档不写控制值；`data/golden/` **M4 已建**（`bench_synth.json`） | `freeze.seed_ledger`、`bench/runner.write_golden` | 写进数值 = 待定值纪律在数据层失效；重基线只允许「合成面 + 全三座」 |
| C20 | 判定整段重算 + upsert 保 id；`--round N` 打印第 N 轮但落库 1..N 全前缀 | `alarm/engine.py::persist`、`09 §5.4/§八` | 改增量会让修订链与延续状态分叉 |
| C20b | 检核整段替换落库；`--from/--to` 只筛清单不切断时间线 | `compliance/auditor.py::persist`、`08 §七` | 增量写会残留"历史违规" |
| C20c | **评测读落库行，不读内存 DTO**；规则**一站一套** | `bench/runner.read_*` / `run_bench` | 混规则会让周报站拿隔日站窗口 → 判据归错 + 晚报一轮（M4 首轮真踩过，见 `05 §三之五` #5） |
| C21 | 夹具档位登记 `kind=user_input`，窗口按工程分目录，`--rules-dir` 是判定档位唯一入口；频率夹具只准待在 `tests/fixtures/data_freq/` | `tests/fixtures/`、`09 §六`、`08 §八`、D31/D32/D40 | 改成 `design_value` 或把条款转 verified = 撞禁项一 |
| C22 | 模块 3/5 的输出对象复用契约层 DTO（`ViolationRecord` / `AlarmRecord` / `TraceRow`），各层不另立 | `contract/records.py`、`compliance/auditor.py`、`03 §5` | 两套 DTO 会让 M5 报告与追溯清单各读一套字段 |
| **C23** | **评测面只有两个词**：`synth`（缺省，出数值）/ `ledger`（反证，指标 `不可用`）；合成面的结论必须带 `plane_is_synthetic` 自述 | `bench/runner.PLANES`、`10 §二.1` | 加第三个面要先改 `10`；README 指标节只用台账面、基准评测节只用合成面，测试逐行钉住 |

## 四、本机环境事实（只写实测过的）

- **`python` 已不可用**：PATH 上第一个 `python` 解析到 Store 别名，任何命令**静默 rc=49、零输出**。一律 `py -3.8` / `py -3.12`。
- 未装 editable 包，`-m pmc` 需要 `PYTHONPATH=src`；pytest 靠 `pyproject` 的 `pythonpath=["src"]` 不需要。
- 已装：pytest 8.3.5（3.8）/ 9.1.1（3.12）、PyYAML 6.0.3（两端）。369 项 20–25 s，无需分批。
- **git `core.autocrlf=true`**（实测）→ 全新 clone 会把 LF 重写成 CRLF；`.gitattributes` + EOL 守门 + `synth --check` 是三重解药。
- 全程 `PYTHONDONTWRITEBYTECODE=1`：本机有 Python 帧损坏家族（unknown opcode / 139 / 0xC0000005），出怪错先清 `__pycache__`。
- Git Bash 里以 `/` 开头的参数会被 MSYS 静默转写；扫描字面值先跑阳性对照。
- **M4 新撞/新证实的坑，M5 别再踩**：
  ① **交接文档本身也要验**：`HANDOFF-M4` 说"bench 不在 `pmc.synth` 禁令名单里"是错的。改任何"文档声称的覆盖面"前先跑一次那条测试。
  ② **一站一套规则**：速率窗口是工程配置事实，多站共用一套规则集会让某一站拿错窗口，症状是"晚报一轮 + 判据归错"，而不是报错。
  ③ **README 的诚实门是切片读的**：`指标` 节按 `README.split("## 指标")[1].split("\n## ")` 取段，新增章节必须排在它后面，否则基准数值会被当成"凭空的指标数值"扫红。
  ④ **`--json` 里存 float 会写出 `1.0`**：`json.dumps(1.0)` = `1.0`，扫"数值是否泄漏"时整数轮次（`25`）与 float 阈值（`25.0`）用 `%g` 会撞车 —— 要扫就扫**数值+单位**的形态。
  ⑤ **golden 是 JSON 不是 CSV**：`test_data_discipline.py` 要求 `data/**/*.csv` 每行都有合规 `point_code`；同时要求每个 JSON 顶层是对象、`schema` 以 `pmc-` 开头、带 `notice`。
  ⑥ 既有的：Windows 写文件必须显式 LF；`--data-dir` 写在子命令之前；`synth --db` 不带 `--force` 在产物已存在时先抛 `SynthError`（建档走 `freeze.build` + `seed_ledger`）；`find_data_dir` 认 `dict/ rulesets/ clauses/` 三个 marker 目录。
- 未实测（M5 若撞上要先定性再改代码）：`pip install -e` 的 build isolation 在本机代理环境的表现；**PyInstaller**；**PySide6**。
- 未实测但已确定不做：openpyxl / xlrd / pandas 一律不装（`06 D03`/D34）。

## 五、关键命令速查

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src                              # `-m pmc` 需要它（本机没装 editable 包）
py -3.12 -X utf8 -m pytest -rs                     # 全量 369 项
py -3.8  -X utf8 -m pytest -rs                     # 老解释器同数全绿才算过
py -3.12 -X utf8 -m pmc selfcheck                  # SELF_CHECK_OK，rc=0（登记 5 条：located 1 / pending 4）
py -3.12 -X utf8 -m pmc rulesets                   # 规则 17 条，可参与判定 0 条
py -3.12 -X utf8 -m pmc synth --check              # 62 个产物逐字节一致，rc=0

# M4 基准一条龙
py -3.12 -X utf8 -m pmc bench                      # 合成面：逐起 11 起 + 七项指标全达标，rc=0
py -3.12 -X utf8 -m pmc bench --plane ledger       # 台账面：四项不可用 + 三项常驻达标，rc=1（这是设计，不是失败）
py -3.12 -X utf8 -m pmc bench --markdown           # README 基准评测节的整表
py -3.12 -X utf8 -m pmc bench --plane ledger --markdown   # README 指标节的七行
py -3.12 -X utf8 -m pmc bench --all --json         # CI/门禁用的机器可读形态
py -3.12 -X utf8 scripts/gate.py                   # 门禁五环（期望码 0/0/0/0/1）
# 重基线（改生成器/判定/真值之后必须做，且要在 05 偏差表说明）：
py -3.12 -X utf8 -m pmc bench --write-golden       # 仅「合成面 + 全三座」允许，否则 InputError
git status --porcelain                             # 必须只剩 .qoder-credits/
```

**退出码一律单独取**：`cmd; echo "rc=$?"`。不要 `cmd | tail`、不要用 `${PIPESTATUS[0]}`。
`-X utf8` 不能省：Windows 控制台 codepage 会让中文断言与 CSV/XLSX 读写出乱码。

## 六、M5 DoD（逐项打勾，未达成写偏差说明）

- [x] `pmc report --kind daily|weekly|stage` 产出 xlsx：标准库直写 OOXML，零第三方依赖（`06 D03`）
- [x] 报告里每个数字都能回溯到台账行（`TraceRow` 清单随报告出），无"来源不明"数字
      —— 构造期强制：含数字的单元格缺来源即 `ReportError`；逐格对账与阳性对照各有测试
- [x] 原生过程线图（xlsx chart，非贴图）：测点 × 轮次 × 累计值 + 阈值线
      —— 偏差：单份至多绘制 6 张（`CHART_SERIES_CAP`，`plan/11 §四` 登记）
- [x] 判定/检核/基准三者的结论用词与 DTO 同源（C22/C2d），报告不重算判定
      —— `report` 层不 import `alarm`/`compliance`/`rules`/`bench`/`synth`（AST 断言）
- [x] 签字栏空白不得声称"已审核"；每份报告带"不判基坑是否安全"边界句
- [x] PySide6 多页签界面：台账 / 导入 / 判定 / 检核 / 报告，offscreen 冒烟测试常驻
      —— 偏差：GUI 用例需要 `PySide6`，开发机两端都装了故同数 0 跳过；CI 三矩阵只装 `dev` 会声明式跳过 11 项（`05 §三之六` #6）
- [x] PyInstaller onedir 双 exe + 构建红线断言（无外链、无创建时间、无用户名）；`data/` 内嵌且 `find_data_dir` 冻结通路实测
      —— 偏差一：`co_filename` 里的构建机盘符属复核项（硬门是个人标记 0 命中），见 `plan/11 §十`
      —— 偏差二：审计器首版两处误报（`https://` 当盘符、OOXML 命名空间 URI 当外链）已修并补反证
- [x] `exe` 内跑通「导入一轮 → 判定 → 导出一份日报」，两次导出逐字节一致
      —— 判定出的是待定值形态（数据面无已核对阈值），是纪律不是缺陷；档案数值**录入入口**未做，登记为开放项
- [x] README 指标节与基准评测节仍由 `bench` 逐行对账；新增测试双解释器同数（基线 369 → **440**）
- [x] `plan/00`/`05`/`06` 回写 + `plan/11` 打包文档 + `HANDOFF-M6.md` 落盘
- [x] 本地提交；**不 push、不建仓、不打 tag**（对外动作待用户授权）

## 七、禁止事项

1. 不把任何阈值数值写成 `verified` —— 除非有用户提供的材料或官方可直连原文，且登记凭证（禁项一）。
2. 不改 `plan/01-题目与任务要求.md`（题目已定稿 v1）；范围扩张一律走 `06` 的决策表。
3. 不加 LLM、不做验算、不接设备、不做 BIM/大屏（题面 §二 明确不做、§六 边界）。
4. 不为了"测试通过/指标好看"放宽纪律断言；纪律测试红了说明数据或代码越界了。
5. **不手改 `data/raw`、`data/truth`、`data/golden` 里任何文件**；改生成器就整目录 `--force` 重生成 + `bench --write-golden` 重基线，让 `--check` 与 golden 对账说话。
6. 不引入第三方 xlsx 解析/生成库；XLSX 写侧走标准库（`zipfile` + 手写 OOXML），读侧复用 M2 的 `ingest/xlsx`。
7. 不在工作树根目录撒临时产物；验证脚本与缓存只落 `.tmp_verify/`、`.tmp_parse/`。
8. 不 push、不建仓、不发 release、不改历史 —— 属 M6 且必须先取得用户授权。
9. 派子代理时限定可写目录并要求逐文件 `git diff` 审读；中间证据（抓取缓存/manifest）不许由它删除。
10. 违规结论只写"应核实"，不写"已确认违规/已认定超标"；评测结论只写指标与四态，不写"基坑安全"；报告结论不得替代监测/设计/监理判断。
11. 不改 M3 的三条规则 id、四类 `kind`，也不改 M4 的两个平面词、九个评测轴原因码与七项指标码（`bench` 的 `data/golden` 与 README 都读它们）。
12. GUI/报告层不得 import `pmc.synth`（C5：装配点例外只给 `cli` 与 `bench`）；要基准数值就读 `BenchReport`。

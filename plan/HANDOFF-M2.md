# HANDOFF · M2（报警内核 + XLSX 输入）

> 用法：新对话直接说「读取 plan/HANDOFF-M2.md 并继续完成任务」。
> 上一棒（M1 数据先行）已完成并本地提交；本文件是唯一续接载体，零上下文也能开工。

## 一、当前进度（M1 交付了什么）

| 面 | 已落地 | 验证方式 |
|---|---|---|
| 数据契约文档 | `plan/07`：台账字段逐列字典、测点命名规则、三形态与量化、合成自证档位、7 类事件与真值 7 列语义、导入 reason_code 11 个定稿、产物冻结布局 | 与 `db/schema.py` 逐列对照；`06 §五` 记 6 行口径变更 |
| 合成数据 | 三座虚拟基坑：8/20/30 轮 × 每轮 196 行测点 = **11369 行观测 / 62 个产物文件 / ~538 KiB**，字节冻结入仓 | `pmc synth --check` + `tests/test_synth_freeze.py`（含篡改与增删文件反证） |
| 事件植入 | 7 类事件全覆盖：`SYN-ZHDQ` 7 起（每类一起）+ `SYN-YYCG` 4 起（含"跳增后回落、未闭环跨 3 轮"）；`SYN-LJ3` 零事件当误报率分母 | `tests/test_synth_generator.py` 22 项 |
| 期望轮次反算 | 按合成档位逐轮求首个触发轮次，**用量化后的落盘数值**；drift 植入第 8 轮 → 期望第 17 轮（不是复制植入轮次） | `first_alarm_round()` + `test_drift_expectation_is_back_computed_not_copied` |
| 生成器自检 | 基线处处不触发（否则事件不可归因）、行数与真值一一对应、互斥与窗口距离、量化文本一致、同值重复不成链 | 不过就 `SynthError` 拒绝落盘，共 7 类反证测试 |
| CSV 导入器 | `pmc/ingest/csvio.py`：11 个行级原因码 + 文件级硬失败分界、ASCII 数值纪律、文件内修订链编号 | `tests/test_ingest_csv.py` 36 项 |
| 修订链与回执 | `pmc/ingest/store.py`：后到只追加 + `superseded_by` 回填、同值不产生空修订、同哈希文件重放拒绝、`import_batch` 回执平衡 | `tests/test_ingest_revision.py` 16 项 |
| CLI | `pmc synth [--seed --sites --rounds --force --check --db]`、`pmc import [--dry-run]`、`pmc ledger [--project --point --from --to]` 全部可用；退出码 0/1/2 落地 | `tests/test_cli_m1.py` 13 项端到端 |
| 台账建档 | `synth --db` 写工程 3 / 工况 9 / 测点 588 / 轮次 58，**不写任何控制值数值**（`design_*` 全空、来源 `none`） | `test_synth_seeded_the_ledger_archive` + `test_ledger_seeding_creates_no_thresholds` |
| 确定性纪律 | 无 stdlib random、无时钟（新增 AST 断言）、Decimal 半进位量化、迭代全 sorted、产物 LF/UTF-8 无 BOM | `test_rng_determinism.py` + `test_generator_never_reads_the_clock` |
| 结构禁令（新增） | 判定/规则/导入/台账/契约层不得 `import pmc.synth`（合成档位冒充规范值的路径被切断） | `test_synthetic_grades_never_reach_the_judgment_path` |
| 脱敏扫描（扩展） | 扫描面从 `data/*.json` 扩到 data/ 全部 json+csv；sha256 数字串的假阳性加 hex 邻接豁免，并同时跑阳性对照 | `test_scanner_still_catches_real_forms` + `test_synthetic_forms_stay_on_the_whitelist` |
| 全量测试 | **199 项，py3.8.8 与 py3.12 各一轮，全绿 0 跳过**（3.3–3.9 s） | `python -X utf8 -m pytest -rs` |

**M1 的既成事实（不是缺陷）**：
`可参与判定的规则 = 0 条`、`point` 表里没有任何控制值 —— 所以 M2 一开工就会撞上"全是待定值"这条通路，
这是纪律要测的东西，不是 bug。带数值的判定必须在测点档案里由"设计值"录入或 M3 核对后回退供货。

**未验证项（如实留档）**：① `pip install -e ".[dev]"` 仍未在干净 venv 按 README 原文逐条跑过；② CI 四矩阵未实跑（本轮不 push）。
两者都属 M6 干净环境验证范围，但 M2 若新增测试文件要记得：CI 的收集数必须与本机一致。

**M1 收尾基线**：提交后 `git status --porcelain` 为空；`synth --check` 过；双解释器 199 项同数全绿。

## 二、M2 待办（按顺序，每步都能单独演示）

1. **先写 `plan/09-报警状态机设计.md`**：双控判据合成规则（`03 §4` 的落地细则）、有效阈值的选取次序（设计值优先 → 标准回退必须带条款号 → 皆缺出待定值）、
   滑动窗口速率与"窗口内无可用读数"的处置、未闭环跨轮次延续的确切定义（`unclosed` 何时置位/何时清除）、非法迁移报错点、降级退出码 1 的触发集合。
2. **夹具档位落地**：M2 的判定要带数值才能跑通"出货通路"。在 `tests/fixtures/` 建已核对档位的假阈值组合（数值可与 `pmc/synth/profile.py` 同，但**来源登记必须写成夹具**，不得伪装成设计文件号），
   并加断言：夹具 id 不得出现在 `data/`；`data/rulesets/*` 仍全 `null`。
3. **判定内核** `pmc/alarm/engine.py`：读 `observation`（只取 `superseded_by IS NULL`）+ `point`（设计值三态）+ 门控后规则，产出 `AlarmRecord` 并落 `alarm_state`；
   `undetermined` 行的阈值列必须为空（DTO + DDL 双重）；`unclosed`、`first_alarm_round_id` 跨轮次持久化。
4. **`pmc check [--round]`**：每测点状态 + 触发依据 + 条款号 + 未闭环清单；退出码 0/1/2 按 `errors.py` 语义。
5. **状态机迁移校验**：`ALLOWED_TRANSITIONS` 落地成行为断言（非法迁移报错，不静默纠正）；"已处置不得回退成已确认"。
6. **XLSX 输入通路（P07 已拍板落在 M2 初）**：标准库 `zipfile` + `ElementTree` 读 `xl/worksheets/*.xml` 与 `sharedStrings.xml`、
   日期序列号换算；**必须复用 `csvio` 的同一套校验与 11 个 reason_code**，不许长出第二套判读口径；坏结构走文件级硬失败。
7. **回归**：植入的每起应报警事件都要被其 `expected_first_alarm_round` 命中（先手工对 11 起，M4 做成 `bench`）；
   `SYN-LJ3` 的 1568 行零事件必须全 `normal`/`prewarning`，不得出现 alarm。
8. **收尾回写**：`plan/00` 状态、`plan/05` M2 行打勾（含偏差说明）、`plan/06` 新增决策与口径变更、写 `plan/HANDOFF-M3.md`，本地提交（不 push）。

## 三、既定口径清单（动了会打挂测试/基准，改前先对照）

| # | 口径 | 位置 | 改它的代价 |
|---|---|---|---|
| C1 | 状态文本六值 + 未闭环集合 = {alarm, alarm_confirmed} | `contract/status.py` | DDL 的 CHECK 由枚举注入；测试 4 处 |
| C2 | 来源 `kind` 4 值、查证 `status` 3 值、阈值不启用原因码 9 个 | `contract/thresholds.py` | 核对队列与 README 统计口径全部重算 |
| C3 | `SCHEMA_VERSION = 1`；版本不符直接 `ContractError`，不写迁移器（D21） | `pmc/__init__.py` | 改表必须 +1 并重建全部夹具库 |
| C4 | 退出码 0/1/2/3 语义（import 有拒收行 = 1） | `pmc/errors.py`、`test_cli_surface.py`、`test_cli_m1.py` | CI 步骤判定依赖它 |
| C5 | 分层禁令 + 内核禁 `random/socket/urllib/http/requests` + **非生成层禁 import `pmc.synth`** | `test_layering.py`、`test_rng_determinism.py`、`test_synth_freeze.py` | 新增依赖或跨层复用会红；先改测试口径再动代码，别悄悄放宽断言 |
| C6 | 规则集：无 `enabled` 字段、键白名单、`design_value` 不得进规则集、`window_days` 必配 `window_source` | `rules/loader.py` | 版本 +1 + `06` 一行 |
| C7 | 未核对的阈值 `value` 必须为 `null`（含合成档位不得写进 `data/`） | `test_data_discipline.py`、`manifest` 只记 `profile_id` | 提前写数值 = 红，且是幻觉防线 |
| C8 | 字典条目字段白名单（不得出现阈值类字段）；14 项编码 | `catalog/items.py`、`data/dict/` | 加字段要同步测试与 `07` |
| C9 | pytest：`addopts` 不含 `-q`；跑测试一律 `-rs`；收集数变化逐项归因 | `pyproject.toml`、`06 D14` | 双 `-q` 吞掉汇总行 |
| C10 | EOL：`* text=auto eol=lf`；scratch 只准落 `.tmp_verify/`、`.tmp_parse/` | `test_eol_guard.py` | 工作树出现 CR 的临时文件会挂四矩阵 |
| C11 | 指标四态 + README 指标表 M4 起由 `bench --markdown` 生成 | `README.md`、`test_readme_honesty.py` | M1 的"不可用/达标"改动是手写的，M4 必须能被 `--markdown` 复现 |
| C12 | 数据纪律：**真值列 7 项**（M1 由 6 扩到 7，新增 `also_expect`）、`event_type` 7 值、`token` 白名单、行数与数据一致 | `data/README §三`、`07 §6.1` | 改真值语义 = 已生成基准全部作废，必须整目录重生成 |
| C13 | 假数据白名单形式（SYN 前缀工程名、`SYN-JK-2026-000N` 图号、199 号段…） | `data/README §三` | 新增形式先登记再使用 |
| C14 | 观测 CSV 表头固定 8 列、真值表头固定 7 列、`source_row` 从 1 起（表头=1） | `synth/freeze.py`、`ingest/csvio.py` | 改列 = 冻结产物重生成 + `07 §八/§十` 同步 |
| C15 | 导入行级原因码 11 个 + 文件级硬失败清单（含同哈希文件重放） | `csvio.REASON_CODES`、`07 §八` | 集合被测试逐字锁定；加码要在 `06` 记一行 |
| C16 | 修订链语义：判定只取 `superseded_by IS NULL`；同值重复不产生空修订 | `store.py`、`03 §2.1` | M2 引擎读链的方式由此决定 |
| C17 | 数值/日期/编号一律 ASCII `[0-9]`，不用 `\d` | `csvio` 三个正则 | 全角数字会被静默洗成合法值（D28） |
| C18 | 站点档案参数：`SYN-LJ3` 0.18 / 其余 0.62、季节幅度 0.12×控制值、窗口 3/5/7 天 | `synth/sites.py`、`07 §4.1` | 改它必须整目录重生成并重新冻结，且基线自检要仍然过 |
| C19 | `synth --db` 建档不写控制值；`data/golden/` 属 M4 | `freeze.seed_ledger` | 写进数值 = 待定值纪律在数据层失效 |

## 四、本机环境事实（只写实测过的）

- **`python` 已不可用**：PATH 上第一个 `python` 解析到 `WindowsApps\python.exe`（Store 别名占位），
  任何命令（含 `python --version`）都**静默退出 rc=49、零输出**。一律改用启动器：`py -3.8` 与 `py -3.12`（两者实测健康）。
  README 里给读者的命令仍写 `python -m pip ...`（读者环境不同），但**本机验证与 CI 之外的所有复跑都用 `py -3.x`**。
- 未装 editable 包，`-m pmc` 需要 `PYTHONPATH=src`；pytest 靠 `pyproject` 的 `pythonpath=["src"]` 不需要。
- 已装：pytest 8.3.5（3.8）/ 9.1.1（3.12）、PyYAML 6.0.3（两端）。199 项 < 4 s，无需分批。
- **git `core.autocrlf=true`**（已实测）→ 全新 clone 会把 LF 重写成 CRLF；`.gitattributes` + EOL 守门测试 + `synth --check` 是这一条的三重解药，别删。
- 全程 `PYTHONDONTWRITEBYTECODE=1`：本机有 Python 帧损坏家族（unknown opcode / 139 / 0xC0000005），出怪错先清 `__pycache__` 再判。
- Git Bash 里以 `/` 开头的参数会被 MSYS 静默转写成 Windows 路径；扫描字面值先跑阳性对照。
- M1 实测到的两个"环境无关"陷阱，M2 别再踩：
  ① Python 的 `\d` 认全角数字（D28）；② 反算必须用**量化后**的落盘数值，用量化前的浮点会把首超轮次算错一轮。
- 未实测（M2 若撞上要先定性再改代码）：`pip install -e` 的 build isolation 在本机代理环境的表现；PyInstaller；PySide6。

## 五、关键命令速查

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src                              # `-m pmc` 需要它（本机没装 editable 包）
py -3.12 -X utf8 -m pytest -rs                     # 全量，跳过项可见（pytest 自己走 pyproject 的 pythonpath）
py -3.8  -X utf8 -m pytest -rs                     # 老解释器同数全绿才算过
py -3.12 -X utf8 -m pmc selfcheck                  # 期望 SELF_CHECK_OK，rc=0
py -3.12 -X utf8 -m pmc synth --check              # 62 个产物逐字节一致，rc=0
py -3.12 -X utf8 -m pmc synth --seed 20260107 --sites 3 --force --db .tmp_verify/l.sqlite
py -3.12 -X utf8 -m pmc import data/raw/SYN-ZHDQ/round-12.csv --project SYN-ZHDQ --round 12 --db .tmp_verify/l.sqlite   # rc=1
py -3.12 -X utf8 -m pmc ledger --db .tmp_verify/l.sqlite --point SYN-TH-18 --from 9 --to 9
git status --porcelain                             # 生成器跑完必须为空
```

**退出码一律单独取**：`cmd; echo "rc=$?"`。不要 `cmd | tail`、不要把 `${PIPESTATUS[0]}` 写进同一条管道的参数里。
`-X utf8` 不能省：Windows 控制台默认 codepage 会让中文断言与 CSV 读写出乱码。

## 六、M2 DoD（逐项打勾，未达成写偏差说明）

- [ ] `plan/09` 状态机设计完成，含"有效阈值选取次序"与"未闭环延续的确切定义"
- [ ] `pmc check` 对 `SYN-ZHDQ` 第 9/11/14/22 轮与 `SYN-YYCG` 第 18/22 轮逐个命中真值的 `expected_first_alarm_round`
- [ ] `SYN-LJ3` 的 1568 行零事件序列无一条 `alarm`（误报 = 0，夹具档位下）
- [ ] 待定值行的阈值列全空且带原因码；DDL 反证仍然红
- [ ] 未闭环跨轮次延续：`SYN-YYCG` 第 18 轮报警后，19/20/21 轮数值已回落仍带 `unclosed`，且 `first_alarm_round_id` 指向 18
- [ ] 非法状态迁移报错（不静默纠正）；已处置不回退成已确认
- [ ] 判定只读链上最新行：`SYN-TH-18` 第 9 轮按修正值 26.0 判超，按首报 8.3 判正常 —— 取错行就会漏报
- [ ] XLSX 输入通路复用同一套 reason_code，且 `data/raw` 的 CSV 换成同内容 xlsx 后导入结果逐行一致
- [ ] 生成路径与判定路径都无 stdlib random、无时钟、无 set 迭代序依赖（AST 断言仍绿）
- [ ] 新增测试全绿且 py3.8 / py3.12 收集数一致；`plan/00`/`05`/`06` 回写；`HANDOFF-M3.md` 落盘
- [ ] 本地提交；**不 push、不建仓、不打 tag**（对外动作待用户授权）

## 七、禁止事项

1. 不把任何阈值数值写成 `verified` —— 除非用户提供了设计文件材料并登记凭证（`test_data_discipline.py` 会红，那是刻意的）。
2. 不改 `plan/01-题目与任务要求.md`（题目已定稿 v1）；范围扩张一律走 `06` 的决策表。
3. 不加 LLM、不做验算、不接设备、不做 BIM/大屏（题面 §二 明确不做、§六 边界）。
4. 不为了"测试通过"放宽纪律断言；纪律测试红了说明数据或代码越界了。
5. **不手改 `data/raw`、`data/truth` 里任何文件**；要改数值就改生成器再 `--force` 整目录重生成，让 `--check` 说话。
6. 不引入第三方 xlsx 解析库（openpyxl/xlrd/pandas 一律不装）：`06 D03` 的内核零依赖对读侧同样成立。
7. 不在工作树根目录撒临时产物；验证脚本与缓存只落 `.tmp_verify/`、`.tmp_parse/`。
8. 不 push、不建仓、不发 release、不改历史 —— 属 M6 且必须先取得用户确认。
9. 派子代理时限定可写目录并要求逐文件 `git diff` 审读；中间证据（抓取缓存/manifest）不许由它删除。

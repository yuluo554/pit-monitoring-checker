# HANDOFF · M1（数据先行）

> 用法：新对话直接说「读取 plan/HANDOFF-M1.md 并继续完成任务」。
> 上一棒（M0 计划与骨架）已完成并本地提交；本文件是唯一续接载体，零上下文也能开工。

## 一、当前进度（M0 交付了什么）

| 面 | 已落地 | 验证方式 |
|---|---|---|
| 计划文档 | `plan/00`（索引重写）、`02` 架构、`03` 接口契约、`04` 数据计划、`05` 里程碑、`06` 决策 D01–D23 | 索引与实际文件一一对应；`01` 未改动 |
| 契约层 | `pmc.contract`：状态六值 + 迁移表 + 未闭环集合；阈值来源三态 × 查证三档 + 9 个原因码；条款登记装载；跨模块 DTO（含 `AlarmRecord.validate` 的待定值断言） | `tests/test_contracts.py` 16 项 |
| 台账 DDL | 11 张表 + 修订链 UNIQUE + 5 条纪律 CHECK（含"无来源不得出结论""待定值阈值列必须为空""标准回退必须带条款号"） | `tests/test_db_schema.py` 13 项，反证为主 |
| 字典与规则集 | `data/dict/monitoring_items.json`（14 项，无数值）；`data/clauses/register.json`（5 条，全 pending）；`data/rulesets/*`（15 条，阈值一律 null） | `tests/{test_rules_loader,test_data_discipline}.py` |
| 门控 | `rules/gate.py`：启用与否单点决定，数据自称启用即装载失败；9 个原因码分类统计 | 含"窗口未配置""条款未核对"两类分档测试 |
| CLI | `pmc selfcheck/init/dict/rulesets` 可用；其余 8 个命令返回退出码 3 并指回里程碑；退出码 0/1/2/3 定稿 | `tests/test_cli_surface.py` |
| 确定性 | `pmc/synth/rng.py` splitmix64（stream 派生），禁 stdlib random 与网络模块的 AST 扫描 | `tests/test_rng_determinism.py` |
| 发布面基建 | `.gitattributes`(eol=lf)、MIT LICENSE（contributors 署名，无个人名）、CI 四矩阵 workflow、根 README（指标四态、无凭空数字）、`.gitignore` 补 scratch 名单 | `tests/{test_eol_guard,test_ci_workflow,test_packaging,test_readme_honesty}.py` |
| 全量测试 | **94 项，py3.8.8 与 py3.12 各一轮，全绿 0 跳过**（0.3–0.5 s） | `python -X utf8 -m pytest -rs` |

**M0 的既成事实（不是缺陷）**：`可参与判定的规则 = 0 条`。所有阈值未挂原文核对 → 只能输出待定值。

**未验证项（如实留档）**：① `pip install -e ".[dev]"` 未在干净 venv 里按 README 原文逐条跑过；② CI 四矩阵未实跑（本轮不 push）。两者都属 M6 干净环境验证范围，M1 不必处理，但别在文档里声称已通过。

## 二、M1 待办（按顺序，每步都能单独演示）

1. **先写字典细目** `plan/07-数据字典与合成数据.md`：`point`/`obs_round`/`observation` 的字段字典（名称/类型/单位/空值规则/来源）、导入 reason_code 集合定稿、真值列语义（`expected_first_alarm_round` 怎么反算、`also_expect` 记什么、互斥坑）。
2. **形态学与事件植入表**：在 `pmc/synth/generator.py` 落三形态（开挖影响段 / 回弹段 / 流变段）+ 分辨率量化 + 7 类事件植入；每类事件的"应触发轮次"由生成器反算。
3. **生成三座虚拟基坑**：`pmc/synth/sites.py`（或模板表）+ `pmc synth --seed 20260107 --sites 3 --rounds …`；产物落 `data/raw/`、`data/truth/`，**字节冻结入仓**；配 `--check` 位级一致与"改生成器必须整目录重生成"守门测试。
4. **CSV 导入器** `pmc/ingest/csvio.py` → `Observation`：测点编号唯一性与命名规则、监测项目字典匹配、单位一致性、缺测标记（不许插值）、轮次时间单调、缺列/坏数字/编码拒绝。
5. **修订链与导入回执**：同测点同轮次重复上报 → `revision_seq+1` + `superseded_by`，前一条保留；`import_batch` 写回执（`reason_code` + `source_row`），不平即被 CHECK 拒绝。
6. **CLI 实装**：`pmc import`（含 `--dry-run` 只出回执不落库）、`pmc ledger`（看修订链与缺测）、`pmc synth`。命令面与退出码不得漂移。
7. **XLSX 输入**：标准库 `zipfile` + `ElementTree` 读 sheet XML（含 sharedStrings、日期序列号）。**是否留在 M1 尾部由用户拍板（P07）**，先做完 1–6 再问。
8. **收尾回写**：`plan/00` 状态、`plan/05` M1 行打勾（含偏差说明）、`plan/06` 新增决策与口径变更、写 `plan/HANDOFF-M2.md`，本地提交（不 push）。

## 三、既定口径清单（动了会打挂测试/基准，改前先对照）

| # | 口径 | 位置 | 改它的代价 |
|---|---|---|---|
| C1 | 状态文本六值 `normal/prewarning/alarm/alarm_confirmed/alarm_handled/undetermined`；未闭环集合 = {alarm, alarm_confirmed} | `contract/status.py` | DDL 的 CHECK 由枚举注入，改名会让旧库静默收脏值；测试 4 处 |
| C2 | 来源 `kind` 4 值、查证 `status` 3 值、原因码 9 个（含 `window_unconfigured`、`clause_not_verified`） | `contract/thresholds.py` | 核对队列与 README 统计口径全部重算 |
| C3 | `SCHEMA_VERSION = 1`；版本不符直接 `ContractError`，**不写迁移器**（D21） | `pmc/__init__.py` | 改表必须 +1 并重建全部夹具库 |
| C4 | 退出码 0/1/2/3 语义 | `pmc/errors.py`、`test_cli_surface.py` | 与同族仓库不一致；CI 步骤判定依赖它 |
| C5 | 分层禁令：`contract` 不依赖任何业务层；只有 `gui` 可 import PySide6；内核禁 `random/socket/urllib/http/requests` | `test_layering.py`、`test_rng_determinism.py` | 新增依赖或跨层复用工具函数会红；**先改测试口径再动代码，别悄悄放宽断言** |
| C6 | 规则集：无 `enabled` 字段；顶层与规则级键白名单；`design_value` 不得进规则集；`window_days` 必须配 `window_source`；`schema` 标签 `pmc-ruleset/1` | `rules/loader.py`、`test_rules_loader.py` | 版本 +1 + `06` 口径变更一行 |
| C7 | 未核对的阈值 `value` 必须为 `null` | `test_data_discipline.py` | 提前写数值 = 红，且是幻觉防线 |
| C8 | 字典条目字段白名单（不得出现阈值类字段）；14 项编码 | `catalog/items.py`、`data/dict/` | 加字段要同步测试与 `plan/07` |
| C9 | pytest：`addopts` 不含 `-q`；跑测试一律 `-rs`；收集数变化要逐项归因 | `pyproject.toml`、`06 D14` | 双 `-q` 会吞掉汇总行 |
| C10 | EOL：`.gitattributes` `* text=auto eol=lf`；scratch 只准落 `.tmp_verify/`、`.tmp_parse/`（已在 `.gitignore`） | `test_eol_guard.py` | 工作树里出现 CR 的临时文件会挂四矩阵 |
| C11 | 指标四态（达标/未达标/不可判/不可用）+ README 指标表由 `bench --markdown` 生成 | `README.md`、`test_readme_honesty.py` | 基准没跑就写"达标"= 红 |
| C12 | 数据纪律：真值列 6 项、`event_type` 7 值、`.truth.csv` 同目录同名、行数与数据一致 | `data/README.md` §三 | 改真值语义 = 已生成基准全部作废 |
| C13 | 假数据白名单形式（SYN 前缀工程名、199 号段、`SYN-JK-2026-0001` 图号…） | `data/README.md` §三 | 新增形式要先登记再使用 |

## 四、本机环境事实（只写实测过的）

- 解释器：`py -3.8`（3.8.8）与 `py -3.12` 都在；`py` 启动器会无视 venv，README 里一律写 `python -m pip ...`。
- 已装：pytest 8.3.5（3.8）/ 9.1.1（3.12）、PyYAML 6.0.3（两端）。测试无需分批（94 项 < 1 s）。
- **git `core.autocrlf=true`**（已实测）→ 全新 clone 会把 LF 重写成 CRLF；`.gitattributes` + EOL 守门测试是这一条的解药，别删。
- 全程 `PYTHONDONTWRITEBYTECODE=1`：本机有 Python 帧损坏家族（unknown opcode / 139 / 0xC0000005），出怪错先清 `__pycache__` 再判，同命令重跑即绿（详见 `crash-loop-rescue`）。
- Git Bash 里以 `/` 开头的参数会被 MSYS 静默转写成 Windows 路径；扫描字面值先跑阳性对照。
- 未实测（M1 若撞上要先定性再改代码）：`pip install -e` 的 build isolation 在本机代理环境的表现；PyInstaller；PySide6。

## 五、关键命令速查

```bash
export PYTHONDONTWRITEBYTECODE=1                 # 本机帧损坏家族对策
python -X utf8 -m pmc selfcheck                  # 契约自检（期望 SELF_CHECK_OK，rc=0）
python -X utf8 -m pmc selfcheck --json           # 门禁可消费的结构化输出
python -X utf8 -m pmc init --db .tmp_verify/ledger.sqlite   # scratch 目录已 gitignore
python -X utf8 -m pmc dict                       # 14 项监测项目 + 来源状态
python -X utf8 -m pmc rulesets                   # 规则集版本/sha256/不启用原因码
python -X utf8 -m pytest -rs                     # 全量，跳过项可见
py -3.8 -X utf8 -m pytest -rs                    # 老解释器同数全绿才算过
git status --porcelain                           # 生成器跑完必须为空（位级一致）
```

**退出码一律单独取**：`cmd; echo "rc=$?"`。不要 `cmd | tail`、不要把 `${PIPESTATUS[0]}` 写进同一条管道的参数里。

## 六、M1 DoD（逐项打勾，未达成写偏差说明）

- [ ] `plan/07` 数据字典与真值格式细目完成，字段与 DDL 一一对应
- [ ] 三座虚拟基坑生成：正常序列形态 + 7 类事件全部植入过
- [ ] 真值文件与数据文件行数一一对应；`expected_first_alarm_round` 由反算而非复制得来
- [ ] `pmc synth --check` 位级一致；连跑两次 `git status --porcelain` 为空
- [ ] 导入回执：`rows_total = accepted + rejected` 且每条拒绝带 reason_code 与 source_row
- [ ] 重复上报进修订链（不覆盖），缺测不被插值，单位不一致被拒收
- [ ] 生成路径无 stdlib random、无时钟、无 set 迭代序依赖（AST 断言仍绿）
- [ ] 新增测试全绿且 py3.8 / py3.12 收集数一致；`plan/00`/`05`/`06` 回写；`HANDOFF-M2.md` 落盘
- [ ] 本地提交；**不 push、不建仓、不打 tag**（对外动作待用户授权）

## 七、禁止事项

1. 不把任何阈值数值写成 `verified` —— 除非用户提供了设计文件材料并登记凭证（`test_data_discipline.py` 会红，那是刻意的）。
2. 不改 `plan/01-题目与任务要求.md`（题目已定稿 v1）；范围扩张一律走 `06` 的决策表。
3. 不加 LLM、不做验算、不接设备、不做 BIM/大屏（题面 §二 明确不做、§六 边界）。
4. 不为了"测试通过"放宽纪律断言；纪律测试红了说明数据或代码越界了。
5. 不 push、不建仓、不发 release、不改历史 —— 这些属 M6 且必须先取得用户确认。
6. 不在工作树根目录撒临时产物；验证脚本与缓存只落 `.tmp_verify/`、`.tmp_parse/`。
7. 派子代理时限定可写目录并要求逐文件 `git diff` 审读；中间证据（抓取缓存/manifest）不许由它删除。

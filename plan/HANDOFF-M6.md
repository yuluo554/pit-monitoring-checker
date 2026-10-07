# HANDOFF · M6（脱敏发布 + 干净环境验证 + 建仓 push）

> 用法：新对话直接说「读取 plan/HANDOFF-M6.md 并继续完成任务」。
> 上一棒（M5 交付形态）已完成并**本地提交**；本文件是唯一续接载体，零上下文也能开工。
> **M6 的每一个对外动作（建仓、push、tag、release、可见性）都必须先取得用户授权。**

## 一、当前进度（M5 交付了什么）

| 面 | 已落地 | 验证方式 |
|---|---|---|
| 报告口径文档 | `plan/11-报告与打包.md`：三形态与缺省范围、九张工作表构成、状态/依据/处置/类别用词映射、追溯口径（含派生来源白名单）、xlsx 写侧纪律、签字栏与免责、`report` 退出码与 `REPORT_*` 标记行、界面五页签纪律、onedir 双 exe 与数据定位优先级、构建红线与**误报校准四项**、构建与 exe 实测记录 | `test_m5_discipline.py::test_report_vocabulary_is_documented`（形态词/表名/角色/状态码/派生表逐个必须在 `11` 出现） |
| xlsx 报告内核 | `pmc/report/ooxml.py`（标准库 `zipfile` 直写 OOXML + 原生 chart）、`builder.py`（读落库行 → 契约 DTO → 工作表 + 追溯）、`fingerprint.py`（台账内容 sha256，剔除三个时间列）；三形态 × 九表 × 追溯清单 | `test_m5_report.py` 22 项：三形态、逐字节复现、无创建时间与个人标记、**逐格追溯对账**、追溯查法可执行、阳性对照（无数值的格不挂来源即红）、图表部件在界内、边界句与空白签字栏、基准列取 DTO 原文、降级退出码、`data/` 只读 |
| 原生过程线图 | `xl/charts/chartN.xml` + `xl/drawings` + `<drawing>`；每测点一张折线图，阈值虚线；开发机第三方 OpenXML 解析器复验识别 4 张图、0 issue | `test_chart_parts_are_native_and_in_bounds`（引用范围不越界、`numCache.ptCount` 与点数一致、至少一条虚线系列） |
| 桌面界面 | `pmc/gui/app.py` + `pages.py`：五页签（台账/导入/判定/检核/报告），argv → `pmc.cli.main()` 进程内执行，notify 通道无模态框，`--smoke` 存活探针，缺 PySide6 降级返回 2 | `test_m5_gui.py` 11 项（offscreen）：页签顺序、旗标是 CLI 子集、`--db/--project` 透传、文本与终端逐字同源、无模态框、`main([])` 返回 2、`--smoke` 返回 0 |
| 打包 | `packaging/pmc.spec`（onedir 双 exe，`datas` 白名单 6 个 data 子目录）+ 两个入口薄壳；`find_data_dir` 冻结优先级经实测确认（exe 定位到 `_internal/data`） | `test_m5_packaging.py` 15 项：spec AST 白名单、SPECPATH 解析、入口薄壳零行为、`.gitignore` 不吞 `*.spec`、冻结定位五分支、审计器反证、README 覆盖 M5 面 |
| 构建红线 | `scripts/dist_audit.py`：内嵌数据整目录逐份 sha256 对账 + 禁区成分 + 个人标记诱饵（本机用户名/home）+ 报告 0 外链；`--selftest` 9 项伪造产物反证 | 实跑 `DIST_AUDIT_OK`（67 份一致）；`--selftest` 打印 `DIST_AUDIT_SELFTEST_OK`，两条都随全量测试常驻 |
| exe 通路 | 中立目录（`%LOCALAPPDATA%\Temp`）跑通 selfcheck → init → synth 建档 → import 一轮 → check → **report 两次逐字节一致** → bench 台账面反证 → gui 探针 → 越界输入拒绝 | 见 `plan/11 §十一` 的退出码与输出记录 |
| README | 状态行翻到「M5 交付形态已完成」、命令表新增 report/gui/dist_audit/pyinstaller 四行、退出码语义补全、快速开始加 M5 段（含 exe 中立目录注意）、路线图 M5 打 ✅、仓库结构补 `report/`、`gui/`、`packaging/`、`scripts/dist_audit.py`，并新增**收集数与跳过项对账口径**段 | `test_readme_honesty.py` + `test_m4_bench.py` 两条逐行对账未动；`test_m5_packaging::test_readme_documents_report_gui_and_exe` 新增 |
| 全量测试 | **440 项，py3.8 与 py3.12 各一轮，全绿 0 跳过**（26 s / 27 s） | `py -3.12 -X utf8 -m pytest -rs` |

**M5 的既成事实（不是缺陷，M6 不许"顺手修好"）**：exe 现场跑出的报告是**待定值形态**（阈值列全空 + 原因码 + 退出码 1），
因为生产数据面一条阈值都没核对。数值仍只有两条合法入口：① 用户在测点档案录入设计报警值；② 材料到位后按 `08 §三` 的分档结构填。
**M6 不许为了让报告/exe 演示出现数字而把夹具或合成档位写进 `data/`**（禁项一，`test_data_discipline.py` 会红）。

**未验证项（如实留档）**：
① `pip install -e ".[dev,gui,pkg]"` 仍未在干净 venv 按 README 原文逐条跑过（M6 干净环境验证的核心）；
② CI 四矩阵未实跑（M6 首推就会暴露：`windows-3.8` 装 `dev,gui`、其余三矩阵只装 `dev` → GUI 的 11 项声明式跳过，收集数必须逐项归因）；
③ 报告在真实 Excel / WPS 里的人工开检未做（只用第三方 OpenXML 解析器复验过结构与 0 issue）—— M6 若拿不到 Excel，按偏差留档；
④ 报告产物的**docx/pdf 形态**未做（题面允许"至少一种可编辑格式"，xlsx 已满足，不扩张范围）。

**开放项（要用户拍板，不自行开工）**：测点档案数值的**录入入口**（GUI 表格编辑或 `pmc point-set` 命令）本轮未实现，
因此 exe 现场无法演示"报警形态"报告。加它属于范围扩张，须走 `06` 决策表并由用户确认。

**M5 收尾基线**：`git status --porcelain` 只剩 `.qoder-credits/`（工具产物，不提交）；`synth --check` 过；
双解释器 440 项同数全绿；`scripts/gate.py` 五环（0/0/0/0/1）全过；`bench` 与 README 两节逐行对账仍成立。

## 二、M6 待办（按顺序）

1. **脱敏四步 + 构建产物本体扫描**，逐条把命令与结论写进 `plan/RELEASE-M6.md`：
   ① `git ls-files` 扫 `.env/.key/secret/token`；② 内容级扫全部跟踪文本（密钥、手机号、身份证、
   个人路径、内网 IP、内部域名、邮箱）—— **个人路径最爱藏在 HANDOFF 的叙述与命令示例里**；
   ③ 二进制单独扫（xlsx 要扫**全部 zip 条目**，含 `docProps/core.xml` 与 `.rels`）；
   ④ 提交元数据邮箱 `git log --format="%ae %ce" --all`；⑤ **`dist/pmc/` 产物本体扫描**（已有 `scripts/dist_audit.py`，
   发布前再跑一次并把结论留档；注意 co_filename 的盘符属复核项而非硬门，见 `11 §十`）。
2. **入库 HANDOFF 的用法行一律相对路径**（`plan/HANDOFF-Mx.md`，本文件已按此写）。
   扫描器固化成 `scripts/desensitize_audit.py`（selftest 阳性对照 + tracked/history/messages 三模式），
   模式用片段拼接构造，**扫描器源码自己也要过自己的扫描**，输出一律「文件:行号 [类别] x次数」不回显原文。
3. **干净环境验证**：新目录 `git clone` + 全新 venv，**逐条照 README 快速开始执行**（含 M5 的 report/gui/dist_audit 段），
   `pip install -U pip setuptools wheel` 前置行按 README 原文；暴露的每个问题修复后加回归测试。
4. **收集数对账**：dev（两端 440，0 跳过）vs CI 四矩阵 vs 干净 clone，差异逐项指认到声明式跳过并写进 `RELEASE-M6`。
5. **EOL 门复核**：开发机全新 clone 后 `synth --check` + `bench` 必须仍绿（`core.autocrlf=true` 的老坑）。
6. **用户授权后才做对外动作**：建仓（SSH 通道优先，`gh repo create` 不带 `--push`）→ push → CI 四矩阵 →
   annotated tag → `gh release create`（notes 用 `--notes-file`）→ topics → 发布后 GitHub 全新 clone 复核。
7. **收尾回写**：`plan/00` 状态、`plan/05` M6 行 + 发布台账、`plan/06` 决策与缓议项清零、
   README 状态行与 CI 徽章转正、`HANDOFF-FINAL`（或本文件就地收束）。

## 三、既定口径清单（动了会打挂测试，改前先对照）

M0–M4 的 C1–C23 全部未变（见 `HANDOFF-M5 §三`，那份文件已降为历史资料）。M5 新增：

| # | 口径 | 位置 | 改它的代价 |
|---|---|---|---|
| C24 | **报告轴**：形态词 `daily/weekly/stage`、九张工作表名、`STATE_LABELS`/`BASIS_LABELS`/`DISPOSITION_LABELS`/`VIOLATION_KIND_LABELS` 四张用词映射、`STATIC_TEXTS` 固定文案（一律不含数字）、两个派生追溯表 `bench` / `ledger_fingerprint` | `report/builder.py`、`plan/11 §一/二/三` | 改任一项同步 `plan/11` 与 `test_m5_discipline.py`；`11` 是报告面的单一事实源，代码常量必须逐个能在文档里找到 |
| C25 | **追溯口径**：单元格只要含 ASCII 数字或本身是数值，必须带 `(表, 行号, 列)`，缺来源构造期即 `ReportError`；汇总格按贡献行逐条展开；`ruleset_applied` 无整型主键 → 行号 = 按 `code,version` 排序的行序 | `report/builder._Builder._register`、`locator_sql`、`plan/11 §三` | 放宽就是把"来源不明数字"放回交付物；`test_positive_control_numeric_cell_without_source_is_rejected` 是反证，不许删 |
| C26 | **xlsx 写侧字节纪律**：`ZipInfo` 固定 1980-01-01、`ZIP_DEFLATED` + 显式 `compresslevel=6`、条目按名排序、`inlineStr` 不建 sharedStrings、**`.rels` 不得有 Content-Type Override**、docProps 只写 `pmc` 占位 | `report/ooxml.py`、`plan/11 §四` | 任一项变动 → 两次导出不再逐字节一致（`test_two_runs_are_byte_identical` 红）；给 `.rels` 写 Override 会让整包打不开（`content_types_xml` 直接抛错 + 反证测试） |
| C27 | **`report` 退出码并入 C4**：未闭环 / 待定值 / 应核实 / 随附基准未达标 任一 → `1`；工程不存在 / 无轮次档案 / 无判定行 / 参数组合非法 → `2`；`3` 只作兜底（无占位命令） | `report/builder.report_exit_code`、`plan/11 §六` | README 命令表与退出码段依赖它；CI 若加 report 步骤要按此断言期望码 |
| C28 | **界面与分层**：GUI 一律经 `pmc.cli.main()`（参数面是 CLI 子集）；`report` 层不 import `alarm/compliance/rules/bench/synth/gui`；基准对象由 CLI 传入；反馈走 notify，禁模态框；`pmc gui` 缺 PySide6 → 返回 `2` 带安装提示 | `gui/app.py`、`gui/pages.py`、`test_m5_discipline.py`、`test_layering.py` | 界面自己算一套结论就废了 C22/C2d 的"用词同源"；`test_argv_flags_exist_on_the_cli` 会挡住凭空旗标 |
| C29 | **打包轴**：`datas` 白名单 = `dict/rulesets/clauses/raw/truth/golden` 六目录；`find_data_dir` 优先级 显式 > `PMC_DATA_DIR` > `_MEIPASS/data` > `exe/_internal/data` > `exe/data` > 仓库根 > CWD 上溯；红线分**硬门**（个人标记、禁区成分、内嵌逐份一致、报告 0 外链）与**复核项**（`co_filename` 盘符） | `packaging/pmc.spec`、`paths.py`、`scripts/dist_audit.py`、`plan/11 §九/十` | 改优先级 = exe 内嵌数据验证退化成假绿（`test_find_data_dir_priority` 五分支锁死）；改白名单要同步 `DATA_FOLDERS` 两处与审计 |

## 四、本机环境事实（只写实测过的）

- `python` 仍是 Store 别名（静默 rc=49），一律 `py -3.8` / `py -3.12`；`-X utf8` 不能省。
- **py3.8 与 py3.12 都装了 `PySide6==6.6.3.1` + `pyinstaller 6.22.3` + `pillow`**（本轮 `pip install -i 清华源` 装上，
  3.12 也能装 6.6.3.1，与 `pyproject` 的 `PySide6>=6.4,<6.7` 一致）。所以 GUI 用例两端同数、0 跳过。
- 构建通道实测：`py -3.12 -X utf8 -m PyInstaller --noconfirm --clean packaging/pmc.spec` 一次成功（约 2 分钟，退出码 0）。
  **没有连崩**，所以 `crash-loop-rescue` 里"先清全局 `__pycache__` 再判"的前置本轮没触发；若下轮撞上，先清 pyc 再定性。
- `officecli`（本机 CLI）可用于第三方 OpenXML 校验：`officecli view <file> outline|issues --json`。
  坑：`view <file> sheets` 不是合法 mode（合法值 text/annotated/outline/stats/issues/html/svg/screenshot/forms）；
  不带 `--json` 时错误路径自身会抛 .NET `FileNotFoundException`，**看错误一律加 `--json`**。
- Git Bash 里把 exe 的 stdout 重定向到文件是 **cp936** 字节，按 UTF-8 读会乱码（不是缺陷，与源码态不带 `-X utf8` 一致）。
- `.gitignore` 已有 `dist/`、`build/`、`*.exe`、`reports/out/`，且**没有** `*.spec` 通配（`*.spec.lock` 才忽略）——
  这条由 `test_gitignore_does_not_swallow_spec` 把守，别再手加 `*.spec`。
- 全程 `PYTHONDONTWRITEBYTECODE=1`；工作树里的 `Write`/脚本产物本轮实测写出 LF（EOL 门 0 命中）。
- 未实测（M6 若撞上要先定性再改代码）：干净 venv 的 `pip install -e ".[dev,gui,pkg]"` build isolation 表现；
  CI 四矩阵；真实 Excel/WPS 打开报告的人工核验。

## 五、关键命令速查

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src
py -3.12 -X utf8 -m pytest -rs                     # 全量 440 项
py -3.8  -X utf8 -m pytest -rs                     # 老解释器同数全绿才算过
py -3.12 -X utf8 -m pmc selfcheck                  # SELF_CHECK_OK，rc=0
py -3.12 -X utf8 -m pmc synth --check             # 62 个产物逐字节一致，rc=0
py -3.12 -X utf8 scripts/gate.py                   # 门禁五环（期望码 0/0/0/0/1）

# M5 报告与界面
py -3.12 -X utf8 -m pmc report --kind daily --db .tmp_verify/m5.sqlite --project SYN-ZHDQ --round 9 --out .tmp_verify/report
py -3.12 -X utf8 -m pmc report --kind stage --db … --project SYN-ZHDQ --bench-plane synth   # 阶段报告带基准逐起表
py -3.12 -X utf8 -m pmc gui --db … --smoke         # GUI_SMOKE_OK 页签 5 个，rc=0

# 打包与红线
py -3.12 -X utf8 -m PyInstaller --noconfirm --clean packaging/pmc.spec
py -3.12 -X utf8 scripts/dist_audit.py --selftest  # 9 项伪造产物反证
py -3.12 -X utf8 scripts/dist_audit.py             # DIST_AUDIT_OK
git status --porcelain                             # 必须只剩 .qoder-credits/（dist/build/reports 已忽略）
```

**退出码一律单独取**：`cmd; echo "rc=$?"`。不要 `cmd | tail`，不要用 `${PIPESTATUS[0]}`。

## 六、M6 DoD（逐项打勾，未达成写偏差说明）

- [ ] 脱敏四步 + 构建产物本体扫描 + 提交邮箱检查，命令与结论逐条留档 `plan/RELEASE-M6.md`
- [ ] 脱敏扫描器固化为 `scripts/desensitize_audit.py`（selftest 阳性对照 + 三模式），并加守门测试随全量跑
- [ ] 入库文档与脚本自身 0 个人字面值（含扫描器源码、命令示例、HANDOFF 叙述）
- [ ] 干净环境（新 clone + 新 venv）按 README **原文逐字**跑通，含 M5 的 report / gui --smoke / dist_audit 三段
- [ ] dev / CI 四矩阵 / 干净 clone 的 pytest 收集数差异**逐项归因到声明式跳过**（GUI 11 项）并留档
- [ ] `synth --check`、`bench` golden、README 两节逐行对账在全新 clone 里仍绿（EOL 门）
- [ ] 用户授权后才：建仓 → push → CI 四矩阵全绿（`gh run list` 对账 push 数 = run 数）→ tag → release → topics
- [ ] 发布后 GitHub 全新 clone 复核 + README 状态行与 CI 徽章转正 + 缓议项清零
- [ ] `plan/00`/`05`/`06` 终态回写 + 收尾快照落盘

## 七、禁止事项（M5 清单全部继承）

1. 不把任何阈值数值写成 `verified`（禁项一）；不为让报告/exe 演示出现数字而把夹具或合成档位写进 `data/`。
2. 不改 `plan/01`（题目 v1 定稿）；范围扩张（含"测点档案录入入口"）一律先走 `06` 决策表并**取得用户确认**。
3. 不加 LLM、不做验算、不接设备、不做 BIM/大屏；不引入第三方 xlsx 库（读写都是标准库）。
4. 不为了"测试通过/指标好看"放宽纪律断言；纪律测试红了说明数据或代码越界了。
5. 不手改 `data/raw`、`data/truth`、`data/golden`；改生成器就整目录 `--force` 重生成 + `bench --write-golden` 重基线。
6. 不在工作树根撒临时产物；验证脚本与缓存只落 `.tmp_verify/`、`.tmp_parse/`。
7. **不 push、不建仓、不发 release、不打 tag、不改历史** —— 每一项都必须先取得用户授权（M5 全程未执行对外动作）。
8. 不删 `dist_audit.py --selftest` 的反证项；审计器的含金量取决于它自己被测过。
9. 不改 M5 新增的 C24–C29 口径而不写进 `plan/11`；报告面与打包面的文档同样是单一事实源。
10. 违规结论只写"应核实"；评测结论只写指标与四态；报告结论不得替代监测/设计/监理判断，且每份都带"不判基坑是否安全"边界句。
11. 派子代理时限定可写目录并要求逐文件 `git diff` 审读；中间证据（抓取缓存/manifest/`dist` 审计日志）不许由它删除。


## 八、M6 完成态（就地收束 · 2026-10-07）

> 本节是给"发布棒"新对话的唯一续接载体。M6 的内部工作在 `a58db36` → `558670d` 三个本地提交里做完，
> **全程未执行任何对外动作**（未建仓、未 push、未 tag、未 release）。

### 交付了什么

| 面 | 落地 | 验证 |
|---|---|---|
| 脱敏审计器 | `scripts/desensitize_audit.py`：`tracked` / `history` / `messages` 三模式 + `--include`（生成物逐 zip 条目）+ `--root`（可审别的仓库/干净 clone）+ `--selftest` | 25 项反证全命中；`--mode tracked` 硬门 0 复核 0；扫描器源码过自家扫描 |
| 脱敏四步 + 产物本体 | `plan/RELEASE-M6.md §一` 逐条命令与结论；`dist_audit` 复审构建产物 | `DIST_AUDIT_OK`（内嵌 67 份逐字节一致）；提交邮箱只有 GitHub noreply 别名 |
| 入库面清零 | `tests/test_data_discipline.py` 的两处真实形态号段样本改片段拼接；旧历史 blob 登记放行 | `--mode history` 复核 2 条均在 `KNOWN_HISTORY_REVIEW`，且守门测试要求"命中 ⊆ 登记表" |
| 干净环境逐字验证 | 全新 clone + 全新 venv 按 README 25 步逐条跑，退出码全对 | `RELEASE-M6 §三`；顺带打出并修掉四处 `only-in-clean-clone` 偏差 |
| 收集数对账 | dev 两端 452 / 0 跳过；只装 dev 的干净 clone 441 + 1 模块级跳过；缺口 11 = GUI 用例数 | `RELEASE-M6 §四` |
| EOL 门 | 全新 clone 里 `synth --check` rc=0、`--force` 重生成后已跟踪文件 0 改动、`bench` golden 位级对账过 | `RELEASE-M6 §五` |
| 交付形态实构建 | 干净 clone 里装 `.[dev,gui,pkg]` → pytest 452 全绿 → PyInstaller onedir 双 exe → `dist_audit` OK → 中立目录跑 exe 链路，日报两次 **sha256 一致** | `RELEASE-M6 §六` |
| 终态回写 | `plan/00` 状态与路线图、`plan/05` M6 行、`plan/06 §八` D46–D51 + P01/P02/P04 现状、README 状态行/命令表/快速开始/收集数口径 | 全量 452 项两端逐文件绿 |

### M6 期间形成的新口径（动了要先对照）

| # | 口径 | 位置 | 代价 |
|---|---|---|---|
| C30 | **脱敏门**：三模式扫描 + 片段拼接构造 + 只按路径形状判个人标记 + 号段类必须登记才放行 + 输出不回显原文 | `scripts/desensitize_audit.py`、`tests/test_m6_desensitize.py`、`plan/RELEASE-M6 §二`、`06 D46–D48` | 裸名匹配会在 CI 变成噪音墙；放宽号段判据 = 把真号一起放行；回显原文会让审计报告成为第二个泄露源 |
| C31 | **"入库面"一律走 git 口径**（跟踪 + 未跟踪且未忽略），不再手维护 SKIP 名单 | `tests/test_eol_guard.py`、`RELEASE-M6 §三` 校准 3、`06 D51` | 名单与 `.gitignore` 漂移会误伤，而误伤的门很快被当噪音绕过 |
| C32 | **README 快速开始必须自洽**：后半段引用的轮次/台账状态要在前面有对应的导入步骤 | `README.md` 快速开始、`tests/test_readme_honesty.py::test_quickstart_only_reads_rounds_it_imported` | 不自洽时文档写 rc=1、用户拿到 rc=2，只有逐字跑才打得出来 |

### 发布棒实况（2026-10-07 用户授权后**已执行完毕**，逐条结果见 `RELEASE-M6.md §七之二` 与 `§十`）

1. **先要 P01 / P02 拍板**（见 `plan/06 §八`）：仓库归属与可见性（个人公开仓 / org 仓 / 是否用现有账号）、
   onedir 产物是否挂 Release（体积大）还是只给构建说明 + sha256。
2. 建仓：`gh repo create` —— **可见性与归属由 P01 决定**（个人公开仓 / org 仓 / 是否用现有账号），
   本文件不替用户预设 `--public`/`--private`；优先 SSH 通道，且**不带 `--push`**，先建空仓再核对远端。
3. push：`git remote add origin <P01 定下的 SSH 远端> && git push -u origin main`
   （远端地址按 P01 的归属拍板结果填，本文件不预写 `<账号>/<仓库>` 形态的 URL —— 那种写法既像邮箱又是凭据面，
   自家脱敏门会把它当成邮箱命中）
   —— 本地 8 个提交（M0–M6）会全部上公开历史，`--mode history` 的登记表已覆盖其中唯一的号段类样本。
4. CI 四矩阵：`gh run list --limit 8` 对账 **push 数 = run 数**；三个非 gui 矩阵应 441 passed / 1 skipped，
   `windows-3.8`（dev,gui）应 452 / 0；脱敏四步门（selftest / tracked / messages / history）应各自 rc=0。
5. annotated tag + release：`git tag -a v0.1.0 -m "…"` → `gh release create v0.1.0 --notes-file <文件>`
   （notes 里不写本机绝对路径，产物按 P02 的决定挂或不挂）。
6. topics + README 徽章转正 + 发布后从 GitHub 全新 clone 再复核一遍（§三/§五/§六 的同一套检）。
7. 收尾：`plan/00` 状态行与 `plan/05` M6 行翻成 ✅、`plan/06` 的 P01/P02 记决定、本文件 §八 补发布结果。

### 发布前必须如实说清的三件事（别在 README/Release notes 里含糊）

- **可参与判定的规则 0 条**：GB 50497-2019 无官方可直连条文原文（逐渠道实测在 `plan/08 §二`），
  所以日报/阶段报告的阈值列是待定值形态，报警通路从未用真数验证过 —— 数值只有两条合法入口。
- **真实 Excel/WPS 人工开检未做**（只有第三方 OpenXML 解析器与部件级测试的证据），按偏差留档。
- 违规结论只写"应核实"，报告不判基坑是否安全；这是产品边界，不是免责声明的装饰。

### 本机环境事实增补（M6 实测）

- `py -m venv`（不带版本号）在本机解析到 **3.8.8**；干净 venv 里 `pip install -U pip setuptools wheel` →
  `pip install -e ".[dev]"` 与 `.[dev,gui,pkg]` 都能装成（后者拿到 PySide6 6.6.3.1 + pyinstaller 6.22.3 + pytest 9.1.1）。
- 本机开发树单进程跑全量 pytest **随机 0xC0000005/段错误**，崩点固定在 `test_alarm_regression.py` 之前
  （早于 M6 新增文件的字母序位置，与改动无关）；取证改用逐文件跑（`for f in tests/test_*.py; do pytest -q "$f"; done`，
  崩一片重跑一片），3.8 与 3.12 各 33 个文件全 rc=0。全新 venv 里未复现，CI 不需要这个绕道。
- 干净环境的 clone 与 venv 只落在 `.clean-store/`（已在 `.gitignore`，且 EOL/脱敏门的扫描面走 git 口径后天然排除）；
  本轮另把工具产物 `.qoder-credits/` 补进 `.gitignore`（它可能带本机会话与路径信息）。

# RELEASE-M6 · 脱敏发布与干净环境验证台账

> 本文件是 M6 的过程台账：每条命令、每个结论、每次放行都要能在这里被复核。
> **留档纪律**：本文件内一律相对路径；工具 stdout 里带本机绝对路径的（例如 `dist_audit.py` 会打印产物全路径），
> 转写进本文件时必须改写成 `dist/pmc` 这类相对形态 —— 「个人路径最爱藏在叙述与命令示例里」说的就是这种事。
> 执行环境：Windows，仓库根为工作目录，`PYTHONDONTWRITEBYTECODE=1`，命令一律 `py -3.12 -X utf8`；退出码单独取（`cmd; echo rc=$?`）。

## 一、脱敏四步 + 产物本体扫描（`HANDOFF-M6 §二.1`）

| 步 | 命令 | 结论 |
|---|---|---|
| ① 机密文件名 | `git ls-files \| grep -Ei '\.env\|\.key$\|secret\|token\|credential\|\.pem$\|id_rsa'` | **0 命中**（181 个跟踪文件：py 81 / csv 61 / md 20 / json 12 / yml、toml、spec、gitkeep、gitignore、gitattributes、LICENSE 各 1） |
| ② 内容级（跟踪文本） | `py -3.12 -X utf8 scripts/desensitize_audit.py --mode tracked` | `DESENSITIZE_OK mode=tracked 无泄露命中`，**硬门 0 / 复核 0**，rc=0（扫描器源码与守门测试自身也在这 181 个文件里，一并过了） |
| ③ 二进制与容器条目 | `py -3.12 -X utf8 scripts/desensitize_audit.py --mode tracked --include reports/out` | 跟踪面**没有任何二进制文件**（扩展名分布见①），所以"二进制单独扫"落在**生成物**上：日报 114 KB、阶段报 1.9 MB，两份 xlsx **逐 zip 条目**扫描（含 `docProps/core.xml`、`xl/_rels/workbook.xml.rels`、九张工作表与 chart 部件）→ 硬门 0，rc=0 |
| ④ 提交元数据 | `git log --format="%ae %ce" --all \| sort -u` | 唯一邮箱 = `…+yuluo554@users.noreply.github.com`，是 GitHub 的 **noreply 别名**而非真实信箱；作者名 `yuluo554` 与发布账号一致（发布即署名，不构成泄露）。`--mode messages` 扫全部提交/标签的姓名、邮箱、标题、正文 + `.git/config` 里带凭据的远端地址 → **0 命中**，rc=0 |
| ⑤ 构建产物本体 | `py -3.12 -X utf8 scripts/dist_audit.py --report reports/out/pmc-daily-SYN-ZHDQ-R09.xlsx` | `DIST_AUDIT_OK`（原文该行末尾带本机绝对路径，按上文纪律转写掉了）：内嵌数据 **67 份与仓库逐字节一致**、无禁区成分、无个人标记诱饵、报告 0 外链；`--selftest` 9 项伪造产物反证照旧打印 `DIST_AUDIT_SELFTEST_OK` |

**分工（为什么不重复扫）**：`dist/pmc/` 冻结载荷里必然带 PyInstaller 记录的 `co_filename` 绝对盘符，
`plan/11 §十` 已把它定成**复核项**而非硬门；所以产物本体由 `dist_audit.py` 管（有硬门/复核两套判据），
`desensitize_audit.py` 管**入库面 + 历史 + 提交信息 + 生成物内容**。两者各跑各的，结论互不替代。

### 校准与修复记录（都是扫描器自己暴露的，不是事后补的）

1. **`id_number` 初版判据过松**：按"连续 18 位数字"判，两份报告产物一次性命中 **518 处**（`xl/worksheets/sheet3.xml`
   报警台账里的长数字串）。收紧为**结构判据** —— 6 位区划 + `(19|20)` 年 + 月 01–12 + 日 01–31 + 3 位顺序 + 校验位 ——
   后 518 处归零，同时把"18 位纯时间戳形状"加进 `--selftest` 阴性对照钉住。
   **为什么必须收紧**：误伤一旦被登记放行，真号就跟着一起被放行了；放宽判据不是"降噪"，是把门拆了。
2. **入库面确有 2 处真实形态字面值**：`tests/test_data_discipline.py` 的阳性对照用了非白名单号段的手机号与
   带真实区划前缀的 18 位证件号形状 —— 它们不是任何真人信息，但按 `data/README.md §三` 的白名单纪律就是越界。
   改为**片段拼接**（运行时拼成真实形态，检测力不变；入库文本里不留完整字面值）。
   两条**阴性**样本（`ab…` 与 `dead…beef`）**不许拆**：它们不被命中靠的是正则的非 hex 相邻守卫，
   一拆就变成被引号包围的纯数字串，守卫失效，反而是自我打挂。
3. **`--include` 的号段类命中原先只打印不拦**（等于生成物可以"看到了但照样绿"）。改为**所有模式一视同仁**：
   号段类命中必须出现在登记表里才放行，不在就按新泄露失败。
4. 扫描器**自身两处自命中**（邮箱白名单里的保留域条目、注释里写的示例盘符）被 `--selftest`
   的"扫描器源码过自家扫描"抓出，同样以片段拼接解决。本文件初稿也栽过同一类：把保留域条目原名写进了留档，
   被自家门判成内网域 —— 留档只准写"哪个类别、哪一行"，不复述可疑字面值。
5. **裸用户名匹配会在 CI 上变成噪音墙**（发布前推演出来的，不是 CI 报错才发现）：GitHub runner 的账号名就叫
   `runner`/`runneradmin`，而本仓库正文与代码里 `bench/runner.py` 出现几十次 —— 按裸名子串匹配会把它们全判成
   个人标记，CI 的脱敏步骤首推即红，且噪音一大真泄露就再也看不见。
   改为**只按路径形状判**：标记一律取 home 路径族（含两种分隔符变体），裸账号名不参与硬门；
   裸名本来要防的事（本机绝对路径入库）由 `drive_path` 与 `home_path` 两条通用规则接住。
   `--selftest` 里加了钉住这条教训的阴性对照：`src/pmc/<账号名>.py` 不许被命中。

## 二、扫描器 `scripts/desensitize_audit.py`（`HANDOFF-M6 §二.2`）

| 面 | 口径 |
|---|---|
| 模式 | `tracked`（全部跟踪文件当前内容）/ `history`（`git rev-list --objects --all` + `git cat-file --batch` 全历史 blob）/ `messages`（提交与标签的姓名邮箱标题正文 + `.git/config` 凭据）/ `all`（缺省） |
| 类别 | `phone_number`、`id_number`（复核类，需登记）；`email`、`credential`、`private_key_block`、`drive_path`、`home_path`、`internal_ip`、`internal_domain`、`personal_identity`（硬门） |
| 个人标记 | 运行时取自 `expanduser("~")`、`USERPROFILE`、`HOMEDRIVE+HOMEPATH`（含两种分隔符变体），**不写死模式表** —— 换台机器照样有效；**只按路径形状判**，裸用户名不进硬门（理由见下方校准 5） |
| 构造纪律 | **模式一律片段拼接**，扫描器源码必须过自己的扫描（`--selftest` 末项 + `test_scanner_and_guard_test_pass_their_own_scan` 双保险） |
| 输出纪律 | 一律「位置 [类别] x次数」，**不回显命中的原文**（否则审计报告本身成了第二个泄露源）；`--include` 指到仓库外时位置标识退化成 `external:名字`，**不把本机绝对路径印进输出**；`--selftest` 有专门一项断言不回显 |
| 容器 | zip 逐条目展开成 `路径!条目名`；`docProps/core.xml` 与 `.rels` 是创建者姓名与外部目标的标准藏身处 |
| 标记行 | `DESENSITIZE_OK` / `DESENSITIZE_FAIL` / `DESENSITIZE_REVIEW` / `SCAN_SUMMARY mode=… 硬门=… 复核=… 类别=…` / `DESENSITIZE_SELFTEST_OK` |
| 退出码 | `0` 无泄露命中；`1` 有硬门命中或未登记的复核命中；反证模式 `1` 表示审计器在空转 |
| 反证 | `--selftest` **25 项**：12 类阳性必抓 + 9 类白名单/形状阴性不误报 + zip 条目展开 + 输出不回显 + 源码自扫，全部 PASS |
| 守门测试 | `tests/test_m6_desensitize.py` 9 项，随全量跑；含"往临时目录撒一个真手机号必须被打挂"与"xlsx 的 docProps 里塞创建者必须被点名"两条反证 |

### 复现命令（§一②③④ 与 §二 的结论全部来自这几条）

```bash
py -3.12 -X utf8 scripts/desensitize_audit.py --selftest                                  # 25 项反证
py -3.12 -X utf8 scripts/desensitize_audit.py --mode tracked                              # 硬门 0 复核 0
py -3.12 -X utf8 scripts/desensitize_audit.py --mode history                              # 硬门 0 复核 2（均已登记）
py -3.12 -X utf8 scripts/desensitize_audit.py --mode messages                             # 硬门 0 复核 0
py -3.12 -X utf8 scripts/desensitize_audit.py --mode tracked --include reports/out        # 报告本体逐 zip 条目
py -3.12 -X utf8 scripts/dist_audit.py --selftest                                         # 9 项伪造产物反证
py -3.12 -X utf8 scripts/dist_audit.py --report reports/out/pmc-daily-SYN-ZHDQ-R09.xlsx   # 产物红线 + 报告 0 外链
```

### 历史放行登记表（唯一一条，动了它必须同步这里）

```
KNOWN_HISTORY_REVIEW = (
    ("tests/test_data_discipline.py@2be0d9694a", "phone_number"),
    ("tests/test_data_discipline.py@2be0d9694a", "id_number"),
)
```

- **blob**：`tests/test_data_discipline.py` 的旧版本 `2be0d9694a`，第 90 行 `phone_number`、第 92 行 `id_number`；
- **性质**：检测器阳性对照的真实形态样本，非任何真人信息；
- **为什么不重写历史**：改历史要抹掉 M0–M5 的验收轨迹与既有提交号，代价远大于一条"格式合法但不存在"的假号；
  且现入库版本已改为片段拼接，**新入库面 0 命中**（`--mode tracked` 硬门 0 复核 0 为证）；
- **CI 浅克隆的形态差**：`actions/checkout@v4` 默认 `fetch-depth: 1`，历史模式在那边命中数为 0 属正常，
  守门测试按"命中 ⊆ 登记表"判，不按"必须命中这两条"判。

## 三、干净环境验证（`§二.3`）

通道：`.clean-store/pit-monitoring-checker` = 本地路径全新 clone（全历史，非浅克隆），
`py -m venv .venv` 全新虚拟环境（本机 `py` 缺省解析到 **Python 3.8.8**），只装 `.[dev]`。

| 前置/步骤 | 结果 |
|---|---|
| `python -m pip install -U pip setuptools wheel` | rc=0（pip 25.0.1 / setuptools 75.3.4 / wheel 0.45.1） |
| `python -m pip install -e ".[dev]"` | **首跑 rc=1**：pip 的 build-isolation 子进程 `0xC0000005`（本机解释器通道故障，见 §四 末注）；同一条命令连重两次 rc=0，装齐 pytest/pyyaml。判定：环境故障，不是工程缺陷，按 M6 纪律如实留档 |
| README 快速开始逐条 25 步 | **每一步退出码都与 README 注释声称的一致**；`IMPORT_RECEIPT … 总行 196 入库 195 拒收 1` 与 `CHECK_SUMMARY … undetermined=196` 逐字复现 |
| `pmc gui --smoke`（无 PySide6） | rc=2 + 安装提示 —— C28 的降级通路在干净环境里成立 |
| `desensitize_audit` 三模式 + `--include reports/out` | clone 里全 rc=0；history 模式在 clone 里报出与开发机同一条已登记复核 |
| `dist_audit --selftest` | rc=0（9 项伪造产物反证不依赖构建产物，干净 clone 里独立可跑） |

### 只有逐字跑才打得出来的四处偏差（已修 + 已加回归）

1. **README 快速开始不自洽**（缺陷，不是风格问题）：文档后半段的 `ledger --from 9`、`check --round 11`、
   `report --round 9` 引用的轮次从来没被导入。照 README 敲的人拿到 **rc=2（输入不可用）**，
   而注释写的是 rc=1 的降级输出。修：M1 段补 R09/R11 两轮导入，日报与阶段报告的 `REPORT_SCOPE`
   数字改成实测值（daily 判定 196 待定值 196 追溯 3206 过程线图 6；stage 判定 392 检核 18 追溯 6977 过程线图 6）。
   回归：`test_readme_honesty.py::test_quickstart_only_reads_rounds_it_imported` 静态对照"引用的轮次必须先导入"。
2. **M3 夹具检核的示例结论无法复现**：文档列的两条具体应核实事项出自装满 20 轮的台账，
   而快速开始只导入三轮。改成如实写三轮台账的实测（`AUDIT_SUMMARY missed=18 …`，
   并说明"档案在、读数不在"正是检核器该报的东西），同时点明逐条时序结论需要全轮导入
   （M4 的 `bench --plane ledger` 与 `scripts/gate.py` 就是这么建台）。
3. **EOL 门与 `.gitignore` 漂移**：门自己维护一份 SKIP 名单，`.clean-store/` 不在名单里，
   于是被 pip 生成的 `*.egg-info/PKG-INFO`（带 CRLF）打挂。修：扫描面改走 **git 自己的口径**
   （已跟踪 + 未跟踪且未忽略），并补一正一反两条对照，钉住"豁免了 scratch 但照样抓得住能提交的 CRLF"。
4. **`only-in-clean-clone` 三处断言**（详见 §四）：开发机两端都装了 PySide6，这三条在干净环境
   （等价 CI 的三个非 gui 矩阵）里全红。已在 `558670d` 修掉，判据没有放宽。

> **通道注意（不是缺陷）**：README 的激活写法面向 PowerShell/cmd；Git Bash 里用相对路径建 venv 后
> `source .venv/Scripts/activate` 会拼出混合分隔符的坏 PATH（`which python` 指向不存在的文件）。
> 本台账的干净环境步骤统一用 `.venv/Scripts/python.exe` 直调解释器，等价于激活后的 `python`。

## 四、收集数对账（`§二.4`）

| 通道 | extras | 通过 | 跳过 | 说明 |
|---|---|---|---|---|
| 开发机 py3.8（逐文件 33 个文件） | dev+gui | **452** | 0 | PySide6 6.6.3.1 在场，GUI 11 项全跑 |
| 开发机 py3.12（逐文件 33 个文件） | dev+gui | **452** | 0 | 与 3.8 同数 |
| 全新 clone py3.8.8 | 仅 dev | **441** | 1（`test_m5_gui.py:17` 模块级声明式跳过） | 452 − 441 = **11**，恰好等于 GUI 用例数，无第二处缺口 |
| CI `windows-3.8`（dev,gui） | dev+gui | 预期 452 | 0 | 与开发机同形；push 后用 `gh run view --log` 逐项对账 |
| CI `ubuntu-3.8`/`ubuntu-3.12`/`windows-3.12` | 仅 dev | 预期各 441 | 各 1 | 与上面的干净 clone 同形（同 extras） |

**归因口径**：差异只有一处来源 —— `tests/test_m5_gui.py` 缺 PySide6 时**整模块声明式跳过**，
`-rs` 打出一条 `SKIPPED [1] tests\test_m5_gui.py:17: 界面页签需要 PySide6（extras=gui）`。
干净 clone 实测出的三条 only-in-clean-clone 失败（gui 降级断言写漏前置条件；"工作树不留库文件"把范围写成
"根目录不许存在任何 sqlite"，而 README 就叫用户 `init --db ledger.sqlite`；EOL 豁免对照没建 `.tmp_verify/`）
已全部修复，修复后 clone 为 441 passed / 1 skipped / **0 failed**。

> **本机崩溃家族（M6 期间的实况）**：开发机单进程跑全量 pytest 随机 `0xC0000005`/段错误，
> 崩点固定在 `tests/test_alarm_regression.py` 之前 —— **早于 M6 新增文件的字母序位置**，与本轮改动无关；
> `py -3.8` 与 `py -3.12` 都会中，批次内文件越多越容易中。取证方式改成逐文件跑（崩一片重跑一片），
> 两端各 33 个文件全部 rc=0。CI/干净 venv 未见此现象，属本机解释器通道问题。
>
> **两条不是"全绿"两字能盖住的实况**（写清楚，免得下轮把 452 当成无条件成立）：
> ① 逐文件跑到 `tests/test_rng_determinism.py`（py3.8）时中过一次 `INTERNALERROR: AttributeError:
> 'builtin_function_or_method' object has no attribute 'ModuleType'` —— 解释器内部状态被破坏的形态，
> 同一条命令连重三次各自 9 passed；这一条**不在**我的重试触发条件里（只认 139/0xC0000005），
> 所以它记在账上而不是被"重跑通过"抹平。
> ② 分片批次里还出现过一次 `1 failed`（第三次分片，116 项里 1 红），把同一批文件与更大范围一起重跑都是全绿，
> 没能稳定复现。**结论**：452 这个数字在两端逐文件与干净 clone 两条通道上都取到了全绿，
> 但本机跑必须"崩一片重跑一片"，不得把单次红色当成产品缺陷、也不得把重试通过当成没发生过。

## 五、EOL 门复核（`§二.5`）

| 检 | 全新 clone 结果 |
|---|---|
| `git status --porcelain`（刚 clone 完） | 0 行 |
| `python -X utf8 -m pmc synth --check` | **rc=0** —— 62 个仓内产物与本机重生成逐字节一致 |
| `synth --seed 20260107 --sites 3 --force` 重生成后的 clone | 已跟踪文件 **0 改动** → `.gitattributes` 的 `* text=auto eol=lf` 挡住了 `core.autocrlf=true` 的重写 |
| `python -X utf8 -m pmc bench` | **rc=0** —— 与 `data/golden/bench_synth.json` 位级对账通过 |
| README 两节逐行对账（`test_m4_bench.py` 两条 + `test_readme_honesty.py`） | 在 clone 里随全量一起绿 |
| `tests/test_eol_guard.py` | 在 clone 里绿（扫描面改走 git 口径后不再被 pip 产物误伤） |

## 六、干净环境实构建（交付形态在 clone 里重跑一遍）

第二个通道：`.clean-store/verify2` = 又一个全新 clone（head `558670d`），venv 用 `py -3.12`（**3.12.10**），
按 README 装 `.[dev,gui,pkg]` 全 extras。这一路补的是 M5 留下的"未验证项 ①"。

| 检 | 结果 |
|---|---|
| `pip install -U pip setuptools wheel` → `pip install -e ".[dev,gui,pkg]"` | **两条都 rc=0**（干净 venv + build isolation 正常）；装上 PySide6 6.6.3.1 / shiboken6 / pyinstaller 6.22.3 / pytest 9.1.1 / pyyaml 6.0.3 |
| 全量 pytest（extras 齐，与 README 的 452 对齐） | **452 passed，0 skipped，rc=0** —— 与开发机两端同数，也对上了 §四 的推算 |
| `pmc gui --db … --smoke`（正通路，offscreen） | rc=0 + `GUI_SMOKE_OK 页签 5 个：台账、导入、判定、检核、报告` |
| `pyinstaller packaging/pmc.spec` | rc=0，约 31 s，产出 `dist/pmc/{pmc.exe, pmc-gui.exe, _internal}` |
| `scripts/dist_audit.py`（全新构建产物） | **DIST_AUDIT_OK**：内嵌数据 67 份与仓库逐字节一致、无禁区成分、无个人标记诱饵、报告 0 外链 |
| `scripts/dist_audit.py --selftest` | rc=0，9 项反证全命中 |
| 冻结 exe 链路（中立目录 `%TEMP%/pmc-m6-exe2`，仓库树之外） | `selfcheck`→`init`→`synth --db`→`import R09`→`check`→`report ×2` 全通：`CHECK_SUMMARY … undetermined=196`（rc=1 待定值降级）、`REPORT_FILE … sha256=2a2182ba5803`，**两次导出 sha256 逐字节一致**，产物 115,295 B；`pmc-gui.exe --smoke` rc=0 |
| 链路跑完后再审 bundle | `dist_audit` 仍 rc=0 —— 因为 exe 链路的写入用 `--data-dir` 指到中立副本，没把已审计的内嵌 `data/` 就地改写 |

两处脚本自身的坑（不是产品缺陷，记下来免得下轮再踩）：
① `--data-dir` 是全局参数，写在子命令**之后**会 rc=2（`selfcheck --data-dir …` 就是这条），README §175 已经写明；
② Git Bash 里 exe 的 stdout 是 cp936 字节，`grep` 会当二进制看，取标记要用 `-a` 或只 grep ASCII 标记。

## 七、对外动作（`§二.6`）

用户已授权（2026-10-07）：个人公开仓 `yuluo554/pit-monitoring-checker`，建仓 → push → CI → tag → release 一路做完；**onedir 产物不挂 Release**（P02 改判：118 MB / zip 49.6 MB 只压重仓库，改为给构建说明 + 本地验证清单）。实际执行与结果见 §七之二。


## 七之二、发布实况台账

| 步 | 动作 | 结果 |
|---|---|---|
| 前置全历史体检 | `git log --all --oneline -- .qoder-credits dist build .tmp_verify .tmp_parse reports` | 六个生成/工具目录**全部 0 个提交**，从未入库；`--mode history` 早已扫过全历史 blob（硬门 0） |
| 建仓 | `gh repo create pit-monitoring-checker --public --source .`（**不带 `--push`**） | 建成 `https://github.com/yuluo554/pit-monitoring-checker`，只加远端不推送 |
| push | `git push -u origin main` | 13 个提交上公开历史，rc=0 |
| CI 首跑 | run `37644586895`（1 次 push = 1 个 run ✓） | **四矩阵 2 绿 2 红**：两个 ubuntu 挂在 `tests/test_synth_freeze.py::test_check_detects_hand_edited_artifact` |
| 定性 | 读断言 + 实测 needle | 不是 Linux 怪癖，是**假通过**：`,3.4,` 在 `round-03.csv` 里出现 **0 次**，replace 是 no-op；Windows 之所以绿，是 `read_text`/`write_text` 把 `\n` 翻成 `\r\n`，"换行全变"蒙出了字节不一致。用 `newline=''` 复现旧写法：`check_blobs` 返回 `ok=True` ✓ 坐实 |
| 修正 | 改字节级单点扰动 + 两条"编辑真的生效"断言 | `b3a7073`；本地 py3.8/py3.12 各 15 passed |
| CI 二跑 | run `37645380842` | **4/4 success**（ubuntu-3.8 / windows-3.8+gui / ubuntu-3.12 / windows-3.12），含脱敏四步门 |
| 产物处置 | onedir 重新构建 + `dist_audit` + 可复现 zip | `DIST_AUDIT_OK`（内嵌 67 份逐字节一致）；zip 49,640,643 B，`sha256=e8baf52ae77f3d6b7d71fe21d413335c9275c4ccb96f1e975abf4df2d8b35540`（条目排序 + 固定 1980-01-01 + `compresslevel=6`，同规则重打包得同一个数）。**按 P02 决定不上传**，只把数留档备查 |

> **首跑才暴露的价值**：M5 收尾时我在 `HANDOFF-M6 §一` 写明"CI 四矩阵未实跑，M6 首推就会暴露"。
> 首推确实暴露了一条本地永远绿的反证测试 —— 这类"只有别的机器才打得出来"的问题，
> 靠在本机重跑多少遍都不会出现。

## 八、DoD 对账

| DoD 项 | 状态 |
|---|---|
| 脱敏四步 + 产物本体扫描 + 提交邮箱检查，逐条留档 | ✅ §一 |
| 扫描器固化为 `scripts/desensitize_audit.py`（三模式 + selftest + 守门测试） | ✅ §二：`--selftest` 25 项反证 + `test_m6_desensitize.py` 9 项，随全量常驻 |
| 入库文档与脚本自身 0 个人字面值 | ✅ `--mode tracked` 硬门 0 复核 0（含扫描器源码、守门测试、本台账自身） |
| 干净环境按 README 原文逐字跑通 | ✅ §三：25 步退出码全对，并打出四处偏差全部修复 + 加回归 |
| 收集数差异逐项归因 | ✅ §四：452 / 441 + 1 模块级跳过，缺口恰好等于 GUI 的 11 项 |
| EOL 门在全新 clone 仍绿 | ✅ §五：`synth --check` rc=0、重生成后已跟踪文件 0 改动、bench 位级对账过 |
| 交付形态在干净环境实构建（PyInstaller + exe 链路 + dist_audit） | ✅ §六：452 passed 0 跳过、DIST_AUDIT_OK、exe 报告两次 sha256 一致 |
| CI 四矩阵全绿（`gh run list` 对账 push 数 = run 数） | ⬜ 必须 push 才有 run，与 §七 一并等授权 |
| 发布后 GitHub 全新 clone 复核 + README 状态行与 CI 徽章转正 | ⬜ 依赖 §七；本地 clone 复核已先行做完（§三/§五/§六） |
| `plan/00`/`05`/`06` 终态回写 + 收尾快照 | ✅ §九 |

### 仍开着的口子（发布前要如实说，不藏）

1. **真实 Excel/WPS 人工开检仍未做** —— M5 的未验证项 ③ 本轮没解决（本机无 Excel/WPS 可用），
   报告的结构与 0 issue 只有第三方 OpenXML 解析器与 `test_m5_report.py` 的部件级证据。按偏差留档。
2. **报告的 docx/pdf 形态未做** —— 题面允许"至少一种可编辑格式"，xlsx 已满足，不扩张范围。
3. **测点档案数值的录入入口仍未实现**（M5 的开放项）：所以 exe 演示出来的报告是待定值形态，
   这是生产数据面的真实状态，不是缺陷。要加录入入口属范围扩张，须走 `plan/06` 决策表 + 用户确认。
4. **exe 报告里的待定值形态意味着报警通路从未用真数验证过** —— 数值仍只有两条合法入口
   （用户录入设计报警值 / 按 `08 §三` 分档结构填），M6 没有为了让报告出现数字而动 `data/`。


## 九、收尾回写台账（`§二.7`）

| 文件 | 回写内容 |
|---|---|
| `plan/00-README总览.md` | 状态行翻成「M6 脱敏门与干净环境验证完成，只剩对外发布（待授权）」；快速自检的项数 440→452；新增 M6 交付两件段；路线图 M6 行 ⬜→🟡；文档索引加 `RELEASE-M6.md`，`HANDOFF-M6` 标为已就地收束；「继续开发」段改成发布棒的接续说法 |
| `plan/05-里程碑与验收门.md` | M6 出口判据行 ⬜→🟡，逐项标注实测位置（①②③ 已实测，④ 待授权） |
| `plan/06-交付对标与决策记录.md` | 新增 §八「M6 决策增量」D46–D51（个人标记只按路径形状判、号段类片段拼接 + 历史 blob 显式登记不重写、审计输出不回显、`id_number` 收紧为结构判据、CI 加脱敏门与 `fetch-depth: 0`、入库面统一走 git 口径）；P01/P02/P04 明记「仍握在用户手上」 |
| `README.md` | 状态段与命令表加 `desensitize_audit.py`；快速开始加 M6 段（含浅克隆下 history 命中 0 属正常的说明）；仓库结构补 `scripts/desensitize_audit.py` 与 `RELEASE-M6`；收集数与跳过项口径段换成实测数字（452 / 441 + 1）；M3 与 M5 段的输出注释改为干净环境实测值 |
| `plan/HANDOFF-M6.md` | 就地收束：新增 §八「M6 完成态」（交付清单、新口径 C30–C32、发布棒逐步命令与 CI 对账口径、发布前必须说清的三件事、本机环境事实增补） |

**提交链**：`a58db36`（脱敏门 + 扫描器 + 台账 §一/§二）→ `a81b5f5`（README 自洽 + EOL 门走 git 口径 + 历史模式 blob 分批修正）→
`558670d`（only-in-clean-clone 三处断言）→ 本提交（终态回写）。全部为**本地提交，未 push**。

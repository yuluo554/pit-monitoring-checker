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

（待补：全新 clone + 全新 venv 按 README 原文逐字执行，含 M5 的 report / gui --smoke / dist_audit 三段）

## 四、收集数对账（`§二.4`）

（待补：dev 两端 / CI 四矩阵 / 干净 clone 的收集数差异逐项归因到声明式跳过）

## 五、EOL 门复核（`§二.5`）

（待补：全新 clone 里 `synth --check` 与 `bench` golden 仍绿）

## 六、对外动作（`§二.6`）

**未执行，等用户授权**：建仓（SSH 通道优先，`gh repo create` 不带 `--push`）→ push → CI 四矩阵 → annotated tag →
`gh release create --notes-file` → topics → 发布后 GitHub 全新 clone 复核。M6 全程只做到本地提交。

## 七、DoD 对账

| DoD 项 | 状态 |
|---|---|
| 脱敏四步 + 产物本体扫描 + 提交邮箱检查，逐条留档 | ✅ 本文件 §一 |
| 扫描器固化为 `scripts/desensitize_audit.py`（三模式 + selftest + 守门测试） | ✅ §二，23 项反证 + 10 项守门 |
| 入库文档与脚本自身 0 个人字面值 | ✅ `--mode tracked` 硬门 0 复核 0 |
| 干净环境按 README 原文逐字跑通 | ⬜ §三待补 |
| 收集数差异逐项归因 | ⬜ §四待补 |
| EOL 门在全新 clone 仍绿 | ⬜ §五待补 |
| 对外动作 | ⬜ 待授权 §六 |

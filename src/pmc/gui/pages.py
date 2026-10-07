"""五页签的控件与参数装配（PySide6）。

一页签 = 一条 pmc 命令的旗标面。`PmPage.argv()` 把界面输入拼成 argv，
`PmPage.run()` 交给 `pmc.gui.app.run_cli` 执行；本页不做任何业务判断，
输出的文本就是终端里那份文本（"界面不是第二套行为"的落地点）。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pmc.gui.app import TAB_TITLES, run_cli

#: 页签名 → (命令名, 字段说明)。字段说明 = (标签, 旗标, 形态)
#: 形态：text / int / flag / combo:值表 / path（文件选择，走按钮槽）
PAGE_SPECS: Dict[str, Tuple[str, Tuple[Tuple[str, str, str], ...]]] = {
    "台账": (
        "ledger",
        (
            ("测点编号", "--point", "text"),
            ("起始轮次", "--from", "int"),
            ("结束轮次", "--to", "int"),
        ),
    ),
    "导入": (
        "import",
        (
            ("观测文件（CSV 或 XLSX）", "@", "path"),
            ("轮次序号", "--round", "int"),
            ("只出回执，不写台账", "--dry-run", "flag"),
        ),
    ),
    "判定": (
        "check",
        (
            ("只打印该轮（判据仍从第 1 轮算起）", "--round", "int"),
            ("规则集目录（夹具档位唯一入口）", "--rules-dir", "dir"),
            ("只判不落库", "--dry-run", "flag"),
        ),
    ),
    "检核": (
        "audit",
        (
            ("起始轮次", "--from", "int"),
            ("结束轮次", "--to", "int"),
            ("频率规则集目录", "--rules-dir", "dir"),
        ),
    ),
    "报告": (
        "report",
        (
            ("报告形态", "--kind", "combo:daily,weekly,stage"),
            ("日报目标轮次", "--round", "int"),
            ("起始轮次", "--from", "int"),
            ("结束轮次", "--to", "int"),
            ("输出目录", "--out", "dir"),
            ("附带基准逐起表（平面）", "--bench-plane", "combo:_,synth,ledger"),
        ),
    ),
}


class PmPage(QWidget):
    def __init__(self, title: str, refs: Dict[str, object], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.title = title
        self.refs = refs
        command, fields = PAGE_SPECS[title]
        self.command = command
        self.fields: List[Tuple[str, object]] = []
        self.messages: List[str] = []
        self.last_code: Optional[int] = None
        self.last_text: str = ""

        layout = QVBoxLayout(self)
        for label, flag, kind in fields:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            control = self._make_control(kind)
            row.addWidget(control)
            layout.addLayout(row)
            self.fields.append((flag, control))

        buttons = QHBoxLayout()
        self.run_button = QPushButton("执行 pmc " + command)
        self.run_button.setObjectName("run")
        self.run_button.clicked.connect(self.run)
        buttons.addWidget(self.run_button)
        self.copy_button = QPushButton("复制输出")
        self.copy_button.setObjectName("copy")
        self.copy_button.clicked.connect(self._copy_output)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.output = QTextEdit()
        self.output.setObjectName("output")
        self.output.setReadOnly(True)
        layout.addWidget(self.output, 3)

        self.log = QTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

    # ---- 控件工厂 --------------------------------------------------------
    def _make_control(self, kind: str):
        if kind == "text":
            return QLineEdit()
        if kind == "int":
            box = QSpinBox()
            box.setRange(0, 100000)
            box.setSpecialValueText("不限")
            return box
        if kind == "flag":
            return QCheckBox()
        if kind == "path":
            row = QLineEdit()
            row.setObjectName("path")
            return row
        if kind == "dir":
            return QLineEdit()
        if kind.startswith("combo:"):
            values = kind.split(":", 1)[1].split(",")
            box = QComboBox()
            box.addItems(values)
            return box
        raise ValueError("未知字段形态：{0}".format(kind))

    # ---- argv 组装 -------------------------------------------------------
    def _shared(self, key: str) -> str:
        """顶部共用输入框取值：不这么写会把 QLineEdit 本身当字符串拼进 argv。"""
        control = self.refs.get(key)
        return str(control.text()).strip() if hasattr(control, "text") else ""

    def argv(self) -> List[str]:
        argv: List[str] = []
        data_dir = self._shared("data_dir")
        if data_dir:
            argv.extend(["--data-dir", data_dir])
        argv.append(self.command)
        db = self._shared("db")
        project = self._shared("project")
        if db:
            argv.extend(["--db", db])
        if project:
            argv.extend(["--project", project])
        for flag, control in self.fields:
            value = self._value_of(control)
            if value is None:
                continue
            if flag == "@":
                argv.append(str(value))
            elif value is True:
                argv.append(flag)
            else:
                argv.extend([flag, str(value)])
        return argv

    def _value_of(self, control) -> Optional[object]:
        if isinstance(control, QCheckBox):
            return True if control.isChecked() else None
        if isinstance(control, QSpinBox):
            return control.value() if control.value() > 0 else None
        if isinstance(control, QComboBox):
            text = control.currentText()
            return None if text in ("", "_") else text
        text = str(control.text()).strip()
        return text or None

    # ---- 执行 ------------------------------------------------------------
    def run(self) -> int:
        argv = self.argv()
        code, text = run_cli(argv)
        self.last_code = code
        self.last_text = text
        self.output.setPlainText(text)
        self.notify("pmc {0} → 退出码 {1}".format(" ".join(part.replace("\\", "/") for part in argv), code))
        return code

    def notify(self, text: str) -> None:
        """界面反馈通道：写日志区而不是弹模态框（offscreen 测试会挂死在模态框上）。"""
        self.messages.append(text)
        self.log.append(text)

    def _copy_output(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.last_text)
        self.notify("输出已复制到剪贴板")

    def browse(self) -> Optional[str]:  # pragma: no cover - 按钮槽，测试不触模态对话框
        path, _filter = QFileDialog.getOpenFileName(self, "选择观测文件")
        if path:
            for flag, control in self.fields:
                if flag == "@":
                    control.setText(path)
        return path


def make_window(db: str = "", data_dir: str = "", project: str = ""):
    """构造主窗口：顶部共用台账/工程/数据目录，下面五个页签。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.setWindowTitle("基坑监测数据判读与预警 · pmc")

    root = QWidget()
    outer = QVBoxLayout(root)

    bar = QHBoxLayout()
    bar.addWidget(QLabel("台账"))
    db_edit = QLineEdit(db)
    db_edit.setObjectName("db")
    db_edit.setPlaceholderText("台账 .sqlite 路径")
    bar.addWidget(db_edit)
    bar.addWidget(QLabel("工程"))
    project_edit = QLineEdit(project)
    project_edit.setObjectName("project")
    project_edit.setPlaceholderText("工程编码")
    bar.addWidget(project_edit)
    bar.addWidget(QLabel("数据目录"))
    data_edit = QLineEdit(data_dir)
    data_edit.setObjectName("data_dir")
    data_edit.setPlaceholderText("缺省自动定位")
    bar.addWidget(data_edit)
    outer.addLayout(bar)

    refs: Dict[str, object] = {"db": db_edit, "project": project_edit, "data_dir": data_edit}
    tabs = QTabWidget()
    tabs.setObjectName("tabs")
    pages: List[PmPage] = []
    for title in TAB_TITLES:
        page = PmPage(title, refs)
        page.setObjectName("page-" + title)
        tabs.addTab(page, title)
        pages.append(page)
    if db:
        for page in pages:
            for flag, control in page.fields:
                if flag == "@":
                    control.setText("")
    outer.addWidget(tabs)

    holder = QScrollArea()
    holder.setWidget(root)
    holder.setWidgetResizable(True)
    window.setCentralWidget(holder)
    refs["pages"] = pages
    refs["tabs"] = tabs
    return app, window, pages


def page_titles() -> Sequence[str]:
    return TAB_TITLES


def default_out_dir() -> str:
    return os.path.join("reports", "out")

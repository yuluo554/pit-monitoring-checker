"""共享契约层：状态枚举、阈值来源三态、条款登记、跨模块数据流记录。

这一层只描述"数据长什么样、什么状态下才算数"，不含任何判读算法。
下游各层（db / rules / alarm / compliance / report / bench）都只认这里的定义，
GUI 亦不例外：界面不得自行发明状态或阈值语义（plan/03 §1）。
"""

from __future__ import annotations

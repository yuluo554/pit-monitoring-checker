"""`pmc.exe` 的冻结入口薄壳：只做 `sys.exit(main())`，不含任何行为。

行为一律在 `pmc.cli`，这里多写一行就是第二套事实源（plan/11 §九）。
"""

import sys

from pmc.cli import main

if __name__ == "__main__":
    sys.exit(main())

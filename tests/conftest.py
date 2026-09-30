"""試験全体の約束。取り込み時に本物の設定・記録から逃がす。"""
from __future__ import annotations

from tests import _isolation

_isolation.ensure_isolated()

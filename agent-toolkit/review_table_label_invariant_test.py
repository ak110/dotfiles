"""レビュー指摘管理表の規範が定める書き出しを、振り返りの集計が受理することを確かめる。

規範（`share/review-table-operations.md`）と集計（`skills/review-session/scripts/cost_metrics.py`）は別々に更新されるため、
片方だけを変えた不一致をここで検出する。規範の現物を読むため、スクリプトの試験と分けてプラグイン直下へ置く。
"""

from __future__ import annotations

import importlib
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent
# スキルのスクリプトはパッケージ外にあり、同じディレクトリのモジュールを裸の名前で読み込む。
# 静的解析はこの検索パスを解決できないため、実行時に名前で読み込む。
sys.path.insert(0, str(_ROOT / "skills/review-session/scripts"))
cost_metrics = importlib.import_module("cost_metrics")


def test_documented_self_record_label_is_recognized_by_review_metrics() -> None:
    """規範が定める書き出しは1つで、その書き出しで始まる指摘箇所を集計が担当の記録として判定する。"""
    norm = (_ROOT / "share/review-table-operations.md").read_text(encoding="utf-8")
    labels = [span for span in norm.split("`")[1::2] if span.endswith("の記録:")]
    assert labels == ["担当の記録:"]
    assert cost_metrics._SELF_RECORD_LABEL.match(f"{labels[0]} 統合rebaseの競合解消")  # noqa: SLF001  # pylint: disable=protected-access
    assert not cost_metrics._SELF_RECORD_LABEL.match("file.py")  # noqa: SLF001  # pylint: disable=protected-access

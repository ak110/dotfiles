"""Codexの記録の写し（`aggregated_output`）を、hookの判定が省略を見落とさずに扱う不変条件を検証する。

Codexは64KiBを超える出力について、記録へ保存する`aggregated_output`だけを中間省略する。モデルへ返した本文と
写しを直接比べる判定を別の箇所へ書くと、写しが省略された全文の応答を不一致として扱い、取得済みの資料を
未取得のまま遮断する。写しの比較は`codex_read_observation`の判定関数へ集約し、hookの実装でこのキーを
参照するのは同モジュールだけとする。
"""

import pathlib

_HOOKS = pathlib.Path(__file__).resolve().parent
_OWNER = _HOOKS / "codex_read_observation.py"


def test_record_copy_is_compared_only_through_codex_read_observation():
    offenders = [
        str(path.relative_to(_HOOKS))
        for path in sorted(_HOOKS.rglob("*.py"))
        if not path.name.endswith("_test.py") and path != _OWNER and "aggregated_output" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"記録の写しを直接参照している: {offenders}。codex_read_observationの判定関数を使え"

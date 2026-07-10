"""`check_self_ref.py`の単体テスト。

`## 変更内容`H3配下の`text`コードブロックに対する自己参照曖昧候補・禁止形式候補・H1改称差分の
検出について、それぞれpositive/negativeケースを網羅する。加えて`## 変更内容`H2外・text
コードブロック外の記述で誤検出しないことを確認する。
"""

from __future__ import annotations

import pathlib
import subprocess

_SCRIPT = pathlib.Path(__file__).with_name("check_self_ref.py")


def _run(tmp_path: pathlib.Path, content: str) -> subprocess.CompletedProcess[str]:
    """スクリプトを別プロセスで起動し結果を返す。

    PEP 723スクリプト形式の実行環境と本テスト環境を分離するため、`uv run`ではなく
    Pythonインタプリタ直接起動でモジュール読み込みして実行する。
    """
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(content, encoding="utf-8")
    return subprocess.run(
        ["python3", str(_SCRIPT), str(plan_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_self_ref_positive_hon_setsu(tmp_path: pathlib.Path) -> None:
    """パターン1: `本節のバレット項目`は検出される。"""
    content = """## 変更内容

### `foo.md`

```text
本節のバレット項目に追記する
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "self-ref" in result.stderr


def test_self_ref_positive_hon_setsu_no_zen(tmp_path: pathlib.Path) -> None:
    """パターン1: `本節の全`は検出される。"""
    content = """## 変更内容

### `foo.md`

```text
本節の全項目を更新する
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "self-ref" in result.stderr


def test_self_ref_positive_do_setsu(tmp_path: pathlib.Path) -> None:
    """パターン1: `同節のバレット項目`は検出される。"""
    content = """## 変更内容

### `foo.md`

```text
同節のバレット項目を修正
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1


def test_self_ref_negative_no_match(tmp_path: pathlib.Path) -> None:
    """パターン1: 該当語なしは検出されない。"""
    content = """## 変更内容

### `foo.md`

```text
この節は通常の説明である
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0
    assert result.stderr == ""


def test_forbidden_form_positive_shinai(tmp_path: pathlib.Path) -> None:
    """パターン2: `を根拠にしない`は検出される。"""
    content = """## 変更内容

### `foo.md`

```text
Xを根拠にしない
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "forbidden-form" in result.stderr


def test_forbidden_form_positive_mochiinai(tmp_path: pathlib.Path) -> None:
    """パターン2: `を根拠に用いない`は検出される。"""
    content = """## 変更内容

### `foo.md`

```text
自己推定を根拠に用いない
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "forbidden-form" in result.stderr


def test_forbidden_form_positive_hanteishinai(tmp_path: pathlib.Path) -> None:
    """パターン2: `を根拠に判定しない`は検出される。"""
    content = """## 変更内容

### `foo.md`

```text
規模を根拠に判定しない
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1


def test_forbidden_form_negative_positive_form(tmp_path: pathlib.Path) -> None:
    """パターン2: `いかなる〜があってもしない`型の全称否定形は検出されない。"""
    content = """## 変更内容

### `foo.md`

```text
いかなる理由があっても中断しない
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0


def test_h1_change_positive_declared_but_missing(tmp_path: pathlib.Path) -> None:
    """パターン3: H3見出しでH1改称を宣言するがブロックにH1変更が無い場合は検出される。"""
    content = """## 変更内容

### `foo.md`のH1改称

```text
本文は変わらない
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "h1-change-missing" in result.stderr


def test_h1_change_positive_declared_extension(tmp_path: pathlib.Path) -> None:
    """パターン3: `拡張`キーワードでも同様に検出される。"""
    content = """## 変更履歴

### タイトル拡張

```text
説明のみで実差分なし
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1


def test_h1_change_negative_declared_and_present(tmp_path: pathlib.Path) -> None:
    """パターン3: H1変更宣言かつブロック内にH1行差分が存在する場合は検出されない。"""
    content = """## 変更内容

### `foo.md`のH1タイトル改称

```text
# 新タイトル

説明文
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0


def test_h1_change_negative_no_declaration(tmp_path: pathlib.Path) -> None:
    """パターン3: H1改称宣言がないH3では未検出。"""
    content = """## 変更内容

### `foo.md`

```text
本文の追記のみ
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0


def test_out_of_target_h2_not_detected(tmp_path: pathlib.Path) -> None:
    """`## 変更内容`H2外の記述で誤検出しない。"""
    content = """## 調査結果

### `foo.md`

```text
本節のバレット項目に追記する
Xを根拠にしない
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0
    assert result.stderr == ""


def test_out_of_text_block_not_detected(tmp_path: pathlib.Path) -> None:
    """`text`コードブロック外（地の文・他言語コードブロック）で誤検出しない。"""
    content = """## 変更内容

### `foo.md`

地の文で本節のバレット項目と書いても検出しない。

```python
# Xを根拠にしない
```

```yaml
本節の全: 何か
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0
    assert result.stderr == ""


def test_multiple_violations_reported(tmp_path: pathlib.Path) -> None:
    """複数種類の違反が同時に検出される。"""
    content = """## 変更内容

### `foo.md`

```text
本節のバレット項目を更新する
Xを根拠にしない
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "self-ref" in result.stderr
    assert "forbidden-form" in result.stderr


def test_boundary_h3_switch_resets_state(tmp_path: pathlib.Path) -> None:
    """H3切り替え時に前H3のH1宣言状態がリセットされる（次H3へ持ち越さない）。"""
    content = """## 変更内容

### `foo.md`のタイトル改称

```text
# 新タイトル
```

### `bar.md`

```text
通常追記
```
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0

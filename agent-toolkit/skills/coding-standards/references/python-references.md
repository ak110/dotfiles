# Python参照情報

## Python新構文と導入バージョン

レビュー対象コードのPythonバージョンが該当PEPの導入バージョン以上の場合、当該構文は正規構文であり指摘の対象にしない。

| PEP | 構文 | 導入バージョン | 例 |
| --- | --- | --- | --- |
| PEP 758 | `except`・`except*`の括弧省略 | 3.14 | `except ValueError, TypeError:` |
| PEP 654 | Exception Groupsと`except*` | 3.11 | `except* ValueError:` |
| PEP 604 | Union型の`\|`記法 | 3.10 | `def f(x: int \| str) -> None: ...` |
| PEP 695 | type parameter構文 | 3.12 | `type Alias = int`、`class C[T]: ...` |
| PEP 634 | 構造的パターンマッチ | 3.10 | `match x: case _: ...` |
| PEP 701 | f-string拡張 | 3.12 | `f"{'inner'}"`（同一引用符の入れ子） |

PEPバージョン情報は`peps.python.org`公式メタデータの`Python-Version`値を典拠とする。
PEP 758の`as`節使用時は従来通り括弧必須とする（`except (ValueError, TypeError) as e:`）。

## 新しいPythonバージョンの機能

- 対象プロジェクトの`requires-python`で利用できる機能は公式のWhat's Newで確認する
  <https://docs.python.org/3/whatsnew/index.html>
- PEP 750テンプレート文字列（`t"..."`、Python 3.14+）自体は注入対策にならない。
  安全性は後段のレンダラやAPI側に依存するため、SQL／HTML生成では対応レンダラと組み合わせて使う

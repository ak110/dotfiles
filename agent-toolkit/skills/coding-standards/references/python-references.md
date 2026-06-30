# Python参照情報

## LLMが誤指摘しがちなPython新構文

LLMの古い学習知識で誤指摘されやすいPython新構文を正規構文として明示する。
レビュー対象コードのPythonバージョンが該当PEPの導入バージョン以上の場合、
当該構文を誤指摘の対象としない。

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

- 新しいPythonバージョンの機能を積極的に使う
  - Python 3.12+: PEP 695型パラメーター構文（`def f[T](x: T) -> T:`／`type Alias[T] = list[T]`）を使う
    - `TypeVar`宣言が不要になり、ジェネリック定義が簡潔になるため
  - Python 3.12+: PEP 701のf-string拡張を活用する
    - 複数行・ネストクォート・バックスラッシュが利用できるようになり可読性が上がる
  - Python 3.13+: `typing.TypedDict`の`ReadOnly[...]`で不変フィールドを型レベルで表現する
  - Python 3.13+: `copy.replace(obj, field=value)`で変更コピーを生成する
    - 対応対象は`dataclass`／`namedtuple`／`__replace__()`定義クラスのみに限定される
  - Python 3.14+: PEP 750テンプレート文字列（`t"..."`）は構造を保持した`Template`を返す
    - f-stringと異なり生成済み文字列ではないため、対応レンダラと組み合わせたSQL／HTML生成で使う
    - `t"..."`自体は注入対策にならない。安全性は後段のレンダラやAPI側に依存する

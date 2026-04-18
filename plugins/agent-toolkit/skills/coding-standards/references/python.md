# Python記述スタイル

## 言語スタイル

- importについて
  - 可能な限り`import xxx`形式で書く（`from xxx import yyy` ではない。定義元を特定しやすく、名前衝突も避けられるため）
  - `import xxx as yyy` の別名は`np`などの一般的なものを除き極力使用しない（可読性を損なうため）
  - 可能な限りトップレベルでimportする（循環参照や初期化順による問題を避ける場合に限りブロック内も可）
- タイプヒントは可能な限り書く（静的解析・IDE補完・リファクタリング耐性を確保するため）
  - `typing.List`ではなく`list`を使用する。`dict`やその他も同様
  - `typing.Optional`ではなく`| None`を使用する
  - 関数をオーバーライドする場合は`typing.override`デコレーターを必ず使用する
- docstringはGoogle Style
  - 自明なArgs, Returns, Raisesは省略する
- ログは`logging`を使う
  - `logger = logging.getLogger(__name__)`でモジュールごとに取得
  - `exc_info=True`指定時は例外をメッセージへ含めず簡潔に（例: `logger.error("〇〇処理エラー", exc_info=True)`）
    - 頻繁に発生する例外に限り `logger.warning(f"〇〇失敗: {e}")` のように文字列化して出力する
  - 一度のエラーで複数回ログが出力されたり、逆に一度もログが出なかったりすることが無いよう注意する
- 日付関連の処理は`datetime`を使う
- ファイル関連の処理は`pathlib`を使う（型安全でOS間の差異を吸収できるため）。`open`関数や`os`モジュールは使わない
- テーブルデータの処理には`polars`を使う（高速・省メモリー・型安全・遅延評価対応）。`pandas`は使わない
- 例外の再送出は `raise`（引数なし）を使い、`raise e` は使わない（スタックトレースが書き換わるため）
- インターフェースの都合上未使用の引数がある場合は、関数先頭で`del xxx # noqa`のように書く（lint対策）
- `typing.Literal`の分岐は`typing.assert_never`で網羅性を担保（`else: typing.assert_never(x)`）
- 単なる長い名前の別名でしかないローカル変数は作らない（参照元を二度追う手間が増え、リネーム時の追従漏れも起きやすいため）。
  例えば `x = cls.foo` と書いて `x` を使うより `cls.foo` を直接使う。
- SQLAlchemyのNULLチェックは`.is_(None)`を使用
- Lintエラーの対策は、可能な限り`assert`や`del`などの通常の構文を使用する
  - Linter側のバグなどで回避が難しい、あるいは必要以上の複雑さを招く場合のみ`# type: ignore[xxx]`などを使用する。
    `mypy`・`pyright`・`pylint`などが重複検出するケースも多く、無視コメントが入り乱れるためあくまで最終手段とする
- Python 3.14以降: PEP 758により`except ValueError, TypeError:`のようにかっこなしで複数例外を書ける（フォーマッターが自動整形する場合あり）
- 入力バリデーション: API境界や外部入力は `pydantic` v2で型駆動バリデーションする（型・範囲・形式を一括で保証し、不正データを早期に拒否するため）
- セキュリティ上の危険パターン
  - `eval()` / `exec()` / `compile()` はユーザー入力に対して使わない（`ast.literal_eval()` や専用パーサーで代替）
  - `pickle` / `shelve` は信頼できないデータに使わない（`json` や `msgpack` で代替）
  - `subprocess` は `shell=True` を避ける（引数はリスト形式で渡す。やむを得ない場合は `shlex.quote()` で引数をエスケープ）
  - YAML読み込みは `yaml.safe_load()` を使う（`yaml.load()` は任意コード実行の危険あり）
  - SQLは必ずパラメーター化クエリを使う（f-stringやformat等で組み立てない）
  - 一時ファイルは `tempfile` モジュールを使う（予測可能なパスへの手動作成は競合・権限昇格のリスクあり）
  - セキュリティ用途（トークン生成・パスワードリセット等）の乱数は `secrets` モジュールを使う
- 他で指定が無い場合のツール推奨:
  - パッケージマネージャー: `uv`（Rust製で高速、pip互換、Pythonバージョン管理も統合）
  - pre-commitフック: `pre-commit`（コミット時の自動チェック）
  - リンター/フォーマッター: `pyfltr`（Ruff + mypy等を統合実行するラッパー）
    - 詳細: <https://ak110.github.io/pyfltr/llms.txt>
  - ユーティリティ集: `pytilpack`（便利ライブラリ）
    - 詳細: <https://ak110.github.io/pytilpack/llms.txt>
- 新しいPythonバージョンの機能を積極的に使う
  - Python 3.12+: PEP 695型パラメーター構文（`def f[T](x: T) -> T:` / `type Alias[T] = list[T]`）を使う
    - `TypeVar` 宣言が不要になり、ジェネリック定義が簡潔になるため
  - Python 3.12+: PEP 701のf-string拡張を活用する
    - 複数行・ネストクォート・バックスラッシュが使えるようになり可読性が上がる
  - Python 3.13+: `typing.TypedDict` の `ReadOnly[...]` で不変フィールドを型レベルで表現する
  - Python 3.13+: `copy.replace(obj, field=value)` で変更コピーを生成する
    - 対応対象は `dataclass` / `namedtuple` / `__replace__()` 定義クラスのみに限定される
  - Python 3.14+: PEP 750テンプレート文字列（`t"..."`）は構造を保持した `Template` を返す
    - f-stringと異なり生成済み文字列ではないため、対応レンダラと組み合わせたSQL / HTML生成で使う
    - `t"..."` 自体は注入対策にならない。安全性は後段のレンダラやAPI側に依存する

## テストコード（pytest）

- テストコードは`pytest`で書く
- 網羅性のため、必要に応じて`@pytest.mark.parametrize`を使用する
- テスト関数内で使用しないfixture（副作用のみが必要な場合）は
  `@pytest.mark.usefixtures("fixture_name")` を使用する
  - `@pytest.mark.parametrize(..., indirect=True)` との併用も可
  - デコレーター順序（外側から内側）:
    `parametrize` → `asyncio` → `usefixtures`

### Fixture のコーディングルール

- 関数名: `_`で始める、テストから参照する場合は`name`で別名指定
- scope: 可能な限り広いスコープ（session → package → module → function）
- autouse: モジュール単位は積極的に使用、package/session単位は副作用に注意
- 型ヒント: 必須、複数値返す場合は型エイリアスを定義

### 非同期テスト

- `pytest-asyncio` を使用する
  - `asyncio_mode = "strict"` を推奨（マーカーの付け忘れを検出できる）
  - テスト関数には `@pytest.mark.asyncio` を明示する
  - 非同期fixtureには `@pytest_asyncio.fixture` を使用する（`@pytest.fixture` + `async def` では動作しない）

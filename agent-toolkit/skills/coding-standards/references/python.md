# Python記述スタイル

## 言語スタイル

- importについて
  - 可能な限り`import xxx`形式で書く（`from xxx import yyy`ではない。定義元を特定しやすく、名前衝突も避けられるため）
  - `import xxx as yyy`の別名は`np`などの一般的なものを除き極力使わない（可読性を損なうため）
  - 可能な限りトップレベルでimportする（循環参照や初期化順による問題を避ける場合に限りブロック内も可）
    - 循環参照はTYPE_CHECKINGガード等の回避策に依存せず、共通依存を別モジュールへ切り出す設計上の解消を優先する（片方を関数内importにするのも局所対処であり、恒常化は避ける）
- タイプヒントは可能な限り書く（静的解析・IDE補完・リファクタリング耐性を確保するため）
  - `typing.List`ではなく`list`を使う。`dict`やその他も同様
  - `typing.Optional`ではなく`| None`を使う
  - 関数をオーバーライドする場合は`typing.override`デコレーターを必ず使う
  - `@typing.final`でオーバーライドを禁止されたメソッドは、サブクラス実装の都合で除去しない
    サブクラスで挙動を変える必要がある場合は、抽象メソッド側での委譲など実装側で吸収する設計を検討する
- docstringはGoogle Style
  - 自明なArgs, Returns, Raisesは省略する
  - `ruff`のD規則（pydocstyle由来のD100〜D107等）が有効なプロジェクトでは、自明な内容でもpublic関数（D103）・モジュール（D100）・クラス（D101）等のdocstring省略はlintエラーになる
    - 最小限の1行サマリーに留め、設計を歪めない範囲で関数を`_`接頭辞のprivate化に切り替える選択肢も検討する
- ログは`logging`を使う
  - `logger = logging.getLogger(__name__)`でモジュールごとに取得
  - `exc_info=True`指定時は例外をメッセージへ含めず簡潔に（例: `logger.error("〇〇処理エラー", exc_info=True)`）
    - 頻繁に発生する例外に限り`logger.warning(f"〇〇失敗: {e}")`のように文字列化して出力する
  - 一度のエラーで複数回ログが出力されたり、逆に一度もログが出なかったりすることが無いよう注意する
- 日付関連の処理は`datetime`を使う
- ファイル関連の処理は`pathlib`を基本とする（型安全でOS間の差異を吸収できるため）
  - `os`モジュールのパス操作は使わず、ファイルの開閉は`Path.open`等の`pathlib`経由を優先する
- テーブルデータの処理には`polars`を使う（高速・省メモリー・型安全・遅延評価対応）。`pandas`は使わない
- 例外の再送出は`raise`（引数なし）を使い、`raise e`は使わない（スタックトレースが書き換わるため）
- インターフェースの都合上未使用の引数がある場合は、関数先頭で`del xxx # noqa`のように書く（lint対策）
- `typing.Literal`の分岐は`typing.assert_never`で網羅性を担保（`else: typing.assert_never(x)`）
- 単なる長い名前の別名でしかないローカル変数は定義しない
  - 参照元を二度参照する手間が増え、リネーム時の追従漏れも起きやすい
  - 例: `x = cls.foo`と書いて`x`を使うより`cls.foo`を直接使う
- SQLAlchemyのNULLチェックは`.is_(None)`を使う
- `isinstance(x, int)`は`bool`値も真と判定する（`bool`は`int`のサブクラス）
  - 数値型を厳格に限定するときは`type(x) is int`または`isinstance(x, int) and not isinstance(x, bool)`で除外する
  - `isinstance(value, type(reference))`形式の型一致チェックでも、`reference`が`int`値のときに`bool`が素通りする
- Python 3.14以降: PEP 758により`except ValueError, TypeError:`のようにかっこなしで複数例外を記述できる
 （フォーマッターが自動整形する場合あり）
- 入力バリデーション: API境界や外部入力は型駆動でバリデーションする（`pydantic` v2等を活用する）
- セキュリティ上の危険パターン
  - `eval()`／`exec()`／`compile()`はユーザー入力に対して使わない（`ast.literal_eval()`や専用パーサーで代替）
  - `pickle`／`shelve`は信頼できないデータに使わない（`json`や`msgpack`で代替）
  - `subprocess`は`shell=True`を避ける（引数はリスト形式で渡す。やむを得ない場合は`shlex.quote()`で引数をエスケープ）
  - `subprocess.run(..., capture_output=True)`の戻り値`proc.stdout`は静的解析（ty/mypy）で
    `bytes | None`寄りに推論されるため、`.decode("utf-8")`で警告が出る
    - 使う前に`assert isinstance(proc.stdout, bytes)`で型を限定すると以降の解析が通る
    - `text=True`を指定する場合は`str`に推論されるが、`None`の可能性が残るため同様に限定する
  - YAML読み込みは`yaml.safe_load()`を使う（`yaml.load()`は任意コード実行の危険あり）
  - SQLは必ずパラメーター化クエリを使う（f-stringやformat等で組み立てない）
  - 一時ファイルは`tempfile`モジュールを使う（予測可能なパスへの手動作成は競合・権限昇格のリスクあり）
  - セキュリティ用途（トークン生成・パスワードリセット等）の乱数は`secrets`モジュールを使う
- 他で指定が無い場合のツール推奨:
  - パッケージマネージャー: `uv`（Rust製で高速、pip互換、Pythonバージョン管理も統合）
  - pre-commitフック: `pre-commit`（コミット時の自動チェック）
  - リンター／フォーマッター: `pyfltr`（Ruff + mypy等を統合実行するラッパー）
    - 詳細: <https://ak110.github.io/pyfltr/llms.txt>
  - ユーティリティ集: `pytilpack`（便利ライブラリ）
    - 詳細: <https://ak110.github.io/pytilpack/llms.txt>
- `argparse`で`action="append"`を使う場合の既定値は`default=None`にする
  - 非list（文字列等）を渡すとCLI引数指定時に`str + list`の`append`で型が破綻する
  - list（例: `[]`）を渡すと毎回初期要素として混入する
  - 環境変数フォールバックを実装するときは`parse_args`後に手動で解決し、`None`なら環境変数から初期化、それ以外はそのまま使う
- `argparse`のオプションへ後から解決経路（環境変数・設定ファイル等）を追加する場合も、`add_argument`の`type`引数（`type=int`・`type=float`等）は維持する
  - `type`を外して全経路を文字列で受け取り後段で変換する設計に変更すると、CLI直接指定時の早期型エラーが失われ呼び出し側の検証コストが増える
  - 既定値解決ロジックは別関数（例: `_resolve_default(args.value, env_key, config_key)`）へ吸収し、parse段階の型変換と既定値解決を分離する
- CLIエントリポイント関数（コマンドラインから直接呼ばれる関数）は`_main`等のprivate命名にせず`main`として公開する
  - pytestで`pytest.raises(SystemExit)`を用いて終了コードを検証する場合、private関数を直接呼ぶテストはpylintの`W0212: protected-access`を招く
    - 一括disableで抑止すると本来の保護検知が失われる
  - 関数本体は成功パスも含めて`sys.exit(exit_code)`を常時明示する
    - 成功パスで早期`return`して終了する設計では`pytest.raises(SystemExit)`が`Failed: DID NOT RAISE <class 'SystemExit'>`で失敗する
  - 推奨形:

    ```python
    def main() -> None:
        has_error = run_process()
        exit_code = 1 if has_error else 0
        sys.exit(exit_code)
    ```

    `-> int`を返して呼び出し側で`sys.exit(main())`に渡す形も同等の推奨形とする。
    成功パスを含めて`sys.exit`が常時呼ばれる構造であれば、いずれの形式も許容する。

    ```python
    def main() -> int:
        has_error = run_process()
        return 1 if has_error else 0

    if __name__ == "__main__":
        sys.exit(main())
    ```

  - `[project.scripts]`の`module:main`参照を変更・追加したら、登録したコマンド名で起動して確認する
    - `python -m <package>.<module>`はモジュール実行で`[project.scripts]`を経由せず、参照更新の確認にならない

- `platformdirs`で設定・キャッシュ・データ等のディレクトリを取得するときは、`user_config_dir`・`user_cache_dir`・`user_data_dir`等の呼び出しで`appauthor=False`を明示する
  - `appname`単独指定は不可
  - Windowsの既定では`appauthor`が省略されると`appname`と同じ値が補完され、配置先が`%LOCALAPPDATA%\<appname>\<appname>\...`の二重構造になる
  - Linux・macOSでは`appauthor`が無視されるため挙動差異を生まない
  - 全プラットフォームで`%LOCALAPPDATA%\<appname>\...`形式を維持するため必須指針とする
- 実行中のイベントループを取得する場合は`asyncio.get_running_loop()`を使う
  - `asyncio.get_event_loop()`はPythonバージョンによって挙動が異なり非推奨
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

## 静的解析の誤検出と抑制

- Lintエラーの対策は、可能な限り`assert`や`del`などの通常の構文を使う
  - Linter側のバグなどで回避が難しい、あるいは必要以上の複雑さを招く場合のみ`# type: ignore[xxx]`などを使う
  - `mypy`・`pyright`・`pylint`などが重複検出するケースも多く、無視コメントが入り乱れるため最終手段とする
- 動的に`sys.path.insert()`してから内部モジュールをimportする箇所では、
  pylintは`wrong-import-position`に加えて`import-error`も誤発火する。
  抑制コメントは`# pylint: disable=wrong-import-position,import-error`の両方併記とする
- 関数内importを意図的に行う箇所では、ruffの`PLC0415`とpylintの`import-outside-toplevel`が
  別ルールで重複指摘する。
  `# noqa: PLC0415  # pylint: disable=import-outside-toplevel`の形式で両方併記する
- `ty`と`mypy`は型不一致抑制コメントの構文が別系統
  `ty`は`# ty: ignore[<rule>]`、`mypy`は`# type: ignore[<code>]`を要求する。
  プロジェクトで両方有効な場合は`# type: ignore[<mypy-code>]  # ty: ignore[<ty-rule>]`の形で同一行に併記する
- `sorted(iterable, key=fn)`で型検査器tyが要素型を`fn`の引数protocol型へ誤って収束させる
  - 具体例は`key=str`での文字列ソートと`key=len`でのコレクション長ソート
  - 検出例は`invalid-assignment`・`unresolved-attribute`
  - `key=lambda x: fn(x)`形式の回避はpylintの`unnecessary-lambda`（W0108）を招き両立しない
  - 内包表記でリスト化してから`list.sort(key=fn)`を使い、`sorted`の戻り値型推論を回避する
- `object`型の引数を`isinstance(x, dict)`で判定すると、型検査器tyは型引数を`Never`と推論する
  - 後続の`x.get("key")`で`invalid-argument-type`（`Expected Never, found Literal["key"]`）を報告する
  - 引数型を`typing.Any`にして回避する（`mypy`・`pyright`・`ty`・`pylint`・`ruff-check`の全通過を確認済み）
  - JSON応答やセッション記録など外部由来の任意値を`dict`判定して処理するヘルパーで再発しやすい

## テストコード（pytest）

- テストファイルの配置方式はプロジェクト方針に従う
  - 方針が無い場合は規模・配布形態を踏まえて以下のいずれかを選ぶ
    - 同居方式: ソース`<name>.py`に対して同一ディレクトリ内に`<name>_test.py`を置く（対応関係を辿りやすい一方、配布パッケージにテストを含めないための除外設定が必要になる）
    - 集約方式: `tests/`配下に`<module>_test.py`をまとめる（配布除外を設定不要で実現できる一方、ソースとテストの対応関係を辿りにくい）
- テストコードは`pytest`で書く
- 網羅性のため、必要に応じて`@pytest.mark.parametrize`を使う
  - 用途は同一ロジックを異なる入力データで反復実行する場合に限定する
    - テスト本体で`if param == "...":`のようなシナリオごとの分岐は書かない
  - シナリオごとに処理が分岐する場合は独立したテスト関数に分割する
   （parametrizeで表現するのはデータ、関数本体が担うのはロジックであり、両者を混在させるとシナリオ追加・削除時にparametrizeリストと分岐節の双方を更新することになりSSOTを失う）
- テスト関数内で使用しないfixture（副作用のみが必要な場合）は
  `@pytest.mark.usefixtures("fixture_name")`を使う
  - `@pytest.mark.parametrize(..., indirect=True)`との併用も可
  - デコレーター順序（外側から内側）:
    `parametrize` → `asyncio` → `usefixtures`
- 空コレクションとの等価比較（`assert x == []`・`assert d == {}`など）はpylintの
  `use-implicit-booleaness-not-comparison`で警告されるため、`assert not x`と書く
  - 中身まで含めた比較が必要な場合は`assert x == [expected]`のように具体的な期待値を書く

### Fixtureのコーディングルール

- 関数名: `_`で始める、テストから参照する場合は`name`で別名指定
- scope: 可能な限り広いスコープ（session → package → module → function）
- autouse: モジュール単位は積極的に使い、package／session単位は副作用に注意する
- 型ヒント: 必須、複数値返す場合は型エイリアスを定義

### 非同期テスト

- `pytest-asyncio`を使う
  - `asyncio_mode = "strict"`を推奨（マーカーの付け忘れを検出できる）
  - テスト関数には`@pytest.mark.asyncio`を明示する
  - 非同期fixtureには`@pytest_asyncio.fixture`を使う（`@pytest.fixture` + `async def`では動作しない）

### 環境変数・設定ディレクトリのテスト隔離

環境変数フォールバックや設定ディレクトリを読み込むCLIをテストする場合、
テスト環境のホームディレクトリや設定ディレクトリ変数が漏れ込むと結果が不安定になる。

- `monkeypatch.setenv`／`monkeypatch.delenv`で関連する全環境変数を`tmp_path`配下へ向ける
  対象は対応する独自環境変数に加え、次の両系統を網羅する。
  POSIX系設定ディレクトリ変数（`HOME`・`XDG_CONFIG_HOME`・`XDG_CACHE_HOME`・`XDG_DATA_HOME`等）。
  Windows系設定ディレクトリ変数（`LOCALAPPDATA`・`APPDATA`・`USERPROFILE`・`PROGRAMDATA`等）。
  `platformdirs`が参照し得る全変数を含める
- 設定経路を1本でも漏らすと開発者ホームの実設定を読み込んでしまうため、
  当該CLIの設定解決経路をプラットフォーム横断で洗い出してから一括で隔離するfixtureに集約する。
  当該CLIが特定OS専用でも、テスト実行環境のOSと参照変数のOSが一致しない場合に隔離漏れが発生する
- 隔離fixtureは`autouse=True`で当該テストモジュールに適用するか、
  `@pytest.mark.usefixtures(...)`で明示適用する

### monkeypatchによる関数差し替え

- `monkeypatch.setattr`で差し替える代用関数は副作用と戻り値をlambda式に同居させず、
  通常の`def`関数として定義して`return`文で戻り値を明示する
  - lambda式に副作用と戻り値を同居させる書き方は、型検査と式評価の警告が同一行で重複する
- 代用関数のシグネチャは対象APIへのキーワード引数追加に追従できるよう、末尾に`**kwargs`を含める
  - 追加される引数名が事前に分かる場合は`<name>: object = None`形式の引数を併用してもよい
  - 対象APIに新規キーワード引数を追加した際に複数の代用関数が一斉に破綻する事態を防ぐ
- 対象APIへ引数を委譲する代用関数の`*args`・`**kwargs`の型注釈は`typing.Any`を使う
  - 委譲先の呼び出し（`original_save(self, *args, **kwargs)`等）で型検査器が型不一致を報告するため
  - 該当検査器: pyright（`reportArgumentType`）・ty（`invalid-argument-type`）・mypy（`arg-type`）
  - 引数を委譲せず即`raise`等の単純なモックは`object`型でもよい

### ロギング出力の検証

- `caplog` fixtureはroot loggerへ伝搬した記録のみ捕捉するため、`propagate=False`のloggerでは捕捉できない
- `capsys`で捕捉する場合も、`StreamHandler(sys.stdout)`をfixtureセットアップ時に追加すると
  capsysのstdout差し替えタイミングとstream参照が一致せず失敗しやすい
- 対処: 検証対象loggerへ記録蓄積用の`logging.Handler`サブクラスを直接追加し、
  fixture終了時に`removeHandler`で取り除くパターンが安定する

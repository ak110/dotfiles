# Python記述スタイル

本書はPythonのコードとテストコードの記述スタイル基準と、静的解析の扱いを定める。

## 言語スタイル

### import・型・docstring

- importは可能な限り`import xxx`形式で、トップレベルに書く。`import xxx as yyy`の別名は読みやすさか実行上の必要性（定着した略称`np`、名前衝突の回避、互換APIの差し替えなど）がある場合に使う
- 循環参照は関数内importやTYPE_CHECKINGガードの恒常化ではなく、共通依存を別モジュールへ切り出して解消する
- 公開境界と非自明な型を型ヒントで明確にする。`@typing.final`のメソッドはサブクラスの都合で外さず、サブクラスごとの差は抽象メソッドへの委譲などで扱う
- docstringはGoogle Styleとし、自明なArgs, Returns, Raisesは省略する。`ruff`のD規則が有効なプロジェクトでは自明でも公開関数・モジュール・クラスのdocstring省略がlintエラーになるため、1行サマリーに留めるか`_`接頭辞でprivate化する

### 標準ライブラリとログ

- ログは`logging`、日時は`datetime`、パスとファイル操作は`pathlib`を基本とする
- 新規にテーブルデータの処理ライブラリを選ぶ場合は`pandas`に代えて`polars`を選ぶ。既存プロジェクトでは採用済みのものにそろえる
- インターフェースの都合で未使用の引数は、関数先頭で`del xxx # noqa`と書く
- `Literal`の分岐は`assert_never`などで網羅性を確かめる
- `isinstance(x, int)`と`isinstance(value, type(reference))`は`bool`値も真と判定する。数値型を厳格に限定するときは`bool`を除外する

### 入力検証とセキュリティ

- API境界や外部入力は型駆動でバリデーションする（`pydantic` v2など）
- セキュリティの一般作法は`implementation-time.md`の「セキュリティ・ロギング・エラー処理」が定める。Pythonでは評価系の`eval`・`exec`、安全でない復元の`pickle`・`yaml.load`、shellによる実行、文字列で組み立てたSQL、予測可能な一時ファイルパスとセキュリティ用途の`random`の使用を避ける。外部入力は引数、SQLはパラメーターで渡し、一時ファイルは安全な生成API、セキュリティ用途の乱数はCSPRNGを使う
- PEP 750テンプレート文字列（`t"..."`、Python 3.14+）の安全性は後段のレンダラに依存するため、SQL・HTML生成では対応するレンダラと組み合わせる

### 実行環境

- PEP 723 uv script（`#!/usr/bin/env -S uv run --script`と`# /// script`ブロック）の注意点
  - `pyproject.toml`があるディレクトリ配下で`uv run`を呼ぶとプロジェクトをインストール対象として扱う。プロジェクトへ依存しないスクリプトは`uv run --no-project --script <path>`で実行する
  - `.python-version`はスクリプトの`requires-python`より優先され、該当Pythonが無いと`error: No interpreter found for Python <ver>`で失敗する。`--no-project`では回避できないため`uv run --python <ver> --script <path>`で指定する
  - `uv run --script`のvenvキャッシュはスクリプトパスに依存し得る（キャッシュキーは公式資料に規定が無い）。制限時間内の実行が必要なスクリプトを事前ウォームアップする場合は、実行時に参照される実パスを対象にする。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/python.md：実行環境：2026年8月17日」にある
- project lockfileを使う`uv run`では、lockfileを更新しない指定（`--frozen`または`--locked`）を必須とする。prekは親環境の`UV_FROZEN`を引き継がない
- `uv run --script`では、script lockfileがある場合だけlockfileを更新しない指定を付ける。script lockfileが無い対象へ`--frozen`を指定すると`Unable to find lockfile for Python script`で終了コード2になる。依存解決を固定するなら`uv lock --script <スクリプトの絶対パス>`で作成してから付ける
- `platformdirs`の`user_config_dir`・`user_cache_dir`・`user_data_dir`などでは`appauthor=False`を明示する。省略するとWindowsで`appauthor`に`appname`が補完され、`%LOCALAPPDATA%\<appname>\<appname>\...`の二重構造になる

### argparseとCLIエントリポイント

- 既存のパーサーまたは共通の`add_argument`登録へオプションを加える前に、対象の名前空間で同じ綴りを検索する。重複登録は起動時のオプション衝突を招く
- `action="append"`の引数が指定されないときの値は`default=None`にする（非listでは型エラー、listでは初期要素が毎回混入する）。環境変数フォールバックは`parse_args`後に`None`の場合だけ解決する
- オプションへ値の解決元（環境変数・設定ファイルなど）を追加しても`type=int`などの`type`引数は維持し、指定なしの場合の解決は別関数へ分ける（CLI直接指定時の早期型エラーを保つため）
- CLIエントリポイント関数は`main`として公開し、成功パスも含めて終了コードを`sys.exit(exit_code)`で明示するか、`-> int`を返して`sys.exit(main())`へ渡す。テストが公開関数で全パスの終了コードを検証できる状態を保つ
- `[project.scripts]`の`module:main`参照を変更・追加したら、登録したコマンド名で起動して確認する（`python -m`は`[project.scripts]`を経由しない）

## 静的解析の誤検出と抑制

- lint指摘への対応は`implementation-time.md`「lintと機械チェック」に従う。Pythonでは複数のチェッカーが同じ箇所を重複検出しやすいため、`assert`や`del`などの通常の構文で解消できる場合はそちらを選ぶ
- `subprocess.run(..., capture_output=True)`の`proc.stdout`は`None`を含む型（`bytes | None`など）に推論される（`text=True`でも`None`が残る）。使う前に`assert isinstance(proc.stdout, bytes)`などで限定する
- 動的な`sys.path.insert()`の後のimportでは、pylintが`wrong-import-position`と`import-error`の両方を誤発火するため`# pylint: disable=wrong-import-position,import-error`と併記する
- 意図的な関数内importでは`# noqa: PLC0415  # pylint: disable=import-outside-toplevel`と併記する
- `ty`と`mypy`の両方が有効な場合、型不一致の抑制は`# type: ignore[<mypy-code>]  # ty: ignore[<ty-rule>]`と同一行に併記する

## テストコード（pytest）

- テストファイルの配置はプロジェクト方針に従う。方針が無い場合は、同居方式（`<name>.py`と同じディレクトリの`<name>_test.py`。配布除外の設定が要る）と集約方式（`tests/<module>_test.py`）から規模と配布形態で選ぶ
- テストフレームワークは対象プロジェクトの採用へそろえ、新規に選ぶ場合は`pytest`を使う
- 同じ操作の入力差には`parametrize`、副作用だけを使うfixtureには`usefixtures`を選ぶ。デコレーター順序は外側から`parametrize` → `asyncio` → `usefixtures`とする
- 空コレクションの比較は`assert not x`と書く。pylintの`use-implicit-booleaness-not-comparison`は空のリストとの等値比較より真偽値による判定を求める
- 大規模テストスイートでは`pyfltr`の`command-timeout`を延伸する。具体値は本ファイルではなく、計測結果に基づいて各プロジェクトの`pyproject.toml`の`[tool.pyfltr]`節へ記録する。省略時の値と挙動は<https://ak110.github.io/pyfltr/llms.txt>から現行版を確認する

### Fixtureのコーディングルール

- 関数名は`_`で始め、テストから参照する場合は`name`で別名を指定する
- scopeとautouseは、入力の隔離と後始末を保てる範囲で共有する。複数の戻り値は型エイリアスやdataclassで意味を明確にする

### 非同期テスト

- `pytest-asyncio`は`asyncio_mode = "strict"`を推奨し、テストには`@pytest.mark.asyncio`、非同期fixtureには`@pytest_asyncio.fixture`を明示する。strictモードでは`@pytest.fixture`と`async def`の組合せは動作しない

### 環境変数・設定ディレクトリのテスト隔離

- 設定ディレクトリや環境変数を読み込むCLIのテストでは、そのCLIが設定を読む場所をプラットフォーム横断で洗い出し、`monkeypatch`で全てを`tmp_path`配下へ向けるfixtureへ集約する。独自の環境変数に加え、POSIX系（`HOME`・`XDG_CONFIG_HOME`・`XDG_CACHE_HOME`・`XDG_DATA_HOME`など）とWindows系（`LOCALAPPDATA`・`APPDATA`・`USERPROFILE`・`PROGRAMDATA`など）の両方を含める。特定OS専用のCLIでも、実行環境のOSと参照変数のOSが異なると隔離されない変数が残る

### monkeypatchによる関数差し替え

- 代用関数は副作用と戻り値をlambda式に同居させず、通常の`def`関数として定義する
- 代用関数のシグネチャは末尾に`**kwargs`を含め、対象APIの引数追加に追従させる。引数を委譲する代用関数の`*args`・`**kwargs`には`typing.Any`を付ける（`object`では委譲先の呼び出しでpyright・ty・mypyが型不一致を報告する）

### ロギング出力の検証

- `caplog`はroot loggerへ伝搬した記録のみ捕捉し、`propagate=False`のloggerは捕捉できない。`capsys`が差し替えた`sys.stdout`と、fixtureで追加した`StreamHandler(sys.stdout)`が保持するストリームは一致しないことがある。検証対象loggerへ記録蓄積用の`logging.Handler`を直接追加し、fixture終了時に取り除くと、捕捉の有無がこれらの条件に左右されない

## Python新構文と導入バージョン

構文の受理は対象コードの対応版と、`peps.python.org`公式メタデータの`Python-Version`を基準にする。
Python 3.14のPEP 758は`except`・`except*`の括弧省略を認めるが、`as`節を使う場合は括弧が必要である。

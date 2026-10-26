# Python記述スタイル

本書は、Pythonコードおよびテストコードの記述スタイル基準と、静的解析ツールの扱いについてまとめたものである。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「セキュリティ・ロギング・エラー処理」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「セキュリティ・ロギング・エラー処理」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「lintと機械チェック」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「lintと機械チェック」` |

## 言語スタイル

### import・型・docstring

- `import`は可能な限り`import xxx`の形式とし、ファイルの最上部（トップレベル）に記述する。`import xxx as yyy`による別名指定は、広く定着した略称（`np`など）を用いる場合、名前の衝突を避ける場合、または互換APIへ差し替える場合など、可読性や実行上の必要性がある場合に限定して使用する。
- 循環参照が発生した場合は、共通して依存する処理を別のモジュールへ切り出して解決する。
- 外部へ公開するインターフェースの境界や、推論が自明でない型は、型ヒントで明確に示す。`@typing.final`が付いたメソッドはサブクラス側の都合で外さず、サブクラスごとの差異は抽象メソッドへの委譲などで吸収する。
- docstringはGoogleスタイルで記述し、自明なArgs、Returns、Raisesは省略する。`ruff`のD規則（pydocstyle）が有効なプロジェクトでは、自明であっても公開関数・モジュール・クラスのdocstringを省略するとlintエラーとなるため、1行の要約を記載するか、非公開を示すアンダースコア（`_`）接頭辞を付与する。

### 標準ライブラリとログ

- ログ出力には`logging`、日時の処理には`datetime`、パスやファイルの操作には`pathlib`を標準として使用する。
- 表形式データ（テーブルデータ）の処理ライブラリを新規に選定する場合は、`pandas`の代わりに`polars`を採用する。既存プロジェクトでは、すでに採用されているライブラリに合わせる。
- 外部インターフェースの仕様上、シグネチャとしては必要だが関数内で使用しない引数は、関数の先頭で`del xxx # noqa`と明記する。
- `Literal`による条件分岐では、すべてのケースを網羅していることを確認するために`assert_never`などを利用する。
- `subprocess`をテキストモード（`text=True`や`encoding`の明示）で起動すると、Windows環境では`input`や標準入力への書き込みに含まれる`\n`が`os.linesep`（`\r\n`）へ変換される。詳細はPython公式ドキュメント`subprocess`の「Frequently Used Arguments」を参照する。厳密な改行コードが求められる子プロセス（`git update-ref --stdin`など）に対しては、文字列ではなくバイト列へエンコードして渡す。
- `subprocess`のテキスト入出力の境界では、文字コードとしてUTF-8を明示する。呼び出し先の契約が別の文字コードを定めている場合は、その文字コードを指定する。厳密な改行を保つ子プロセスへバイト列を入力する際は、前述の規定に従う。
- `isinstance(x, int)`や`isinstance(value, type(reference))`は、`bool`型の値に対しても真（True）を返す。数値型のみに厳密に限定したい場合は、`bool`型を除外する条件を加える。

### 入力検証とセキュリティ

- APIの境界や外部からの入力値は、`pydantic` v2などの型駆動型ライブラリを用いてバリデーションを行う。
- セキュリティに関する全般的な作法は、`implementation-time.md`「セキュリティ・ロギング・エラー処理」に従う。Pythonコードでは、コードを動的に評価する`eval`や`exec`、安全でない直列化復元をする`pickle`や`yaml.load`、シェルを介したコマンド実行、文字列結合で組み立てたSQL、推測可能なパスを用いた一時ファイルの作成、およびセキュリティ用途での`random`モジュールの使用を避ける。外部入力は関数の引数として渡し、SQLはプレースホルダー（パラメーター）を用いて組み立てる。一時ファイルは安全な生成APIを用い、セキュリティ用途の乱数生成には暗号学的に安全な疑似乱数生成器（CSPRNG）を使用する。
- PEP 750のテンプレート文字列（`t"..."`、Python 3.14以降）の安全性は後段で処理するレンダラーの実装に依存するため、SQLやHTMLの生成時には適切な安全対策を施したレンダラーと組み合わせて使用する。

### 資源の寿命

ファイルやネットワーク接続などのリソースは、取得した直後に`with`や`async with`のブロックで囲み、例外や中断が発生した場合でも確実に解放されるようにする。
リソースの有効期間が関数、呼び出し元、テストのfixtureなどにまたがる場合は、`contextlib.contextmanager`、`ExitStack`、fixtureの`yield`などを用いて所有権と解放の責任を受け渡す。
リソースを保持する自作クラスには、`__enter__`と`__exit__`（非同期の場合は`__aenter__`と`__aexit__`）を実装し、利用側がコンテキストマネージャーとして扱える設計にする。
複数の後始末処理を順に行う場合は、前段の処理で例外、`KeyboardInterrupt`、または`CancelledError`が発生しても、後段の解放処理が確実に実行される構造にする。

`consider-using-with`や`SIM115`などのlint警告を行単位で無効化（抑制）するのは、デーモンや常駐プロセスなどリソースをプロセスの寿命を超えて保持し続ける場合や、`__exit__`の仕様が要件に合致しない場合に限定し、その理由をコメントとして明記する。
関数をまたいでリソースを管理する必要がある場合は、前述のコンテキストマネージャーなどを利用して表現する。

### 実行環境

Windows環境でも実行や静的解析が行われるコードやテストを作成する際は、Windowsで動作結果が異なる可能性のある前提条件を事前に確認する。手元のLinux環境で検証しても、OS間の差異を検出できないためである。

<例>
他OSに存在しない標準モジュール: `fcntl`、`pwd`、`pty`、`msvcrt`、`winreg`など。
仮想環境の`python.exe`による中継処理と、親プロセス・子プロセス間でのPIDの差異。
ドライブレターを含むパス表記とパス区切り文字。
拡張子付きの実行ファイル名と`.cmd`形式のスクリプト起動。
テキストモードの`subprocess`における標準入力の改行コード自動変換（「標準ライブラリとログ」参照）。
</例>

- PEP 723のuvスクリプト（`#!/usr/bin/env -S uv run --script`および`# /// script`メタデータブロック）に関する注意点：
  - `pyproject.toml`が存在するディレクトリ配下で`uv run`を実行すると、そのプロジェクト自身がインストール対象として認識される。プロジェクトに依存しない独立したスクリプトを実行する場合は、`uv run --no-project --script <path>`を指定する。
  - `.python-version`ファイルの設定はスクリプト内の`requires-python`より優先されるため、該当バージョンのPythonが環境に存在しないと`error: No interpreter found for Python <ver>`というエラーで失敗する。これは`--no-project`では回避できないため、`uv run --python <ver> --script <path>`でPythonバージョンを明示的に指定する。
  - `uv run --script`が作成する仮想環境のキャッシュは、スクリプトのファイルパスごとに管理される場合がある（キャッシュキーの生成規則は公式ドキュメントに規定がない）。実行時間制限のあるスクリプトを事前にウォームアップする場合は、実行時に実際に参照される絶対パスを対象にする。監査記録は`docs/development/audit-records.md`「agent-basics/skills/writing-standards/references/python.md：実行環境：2026年8月17日」を参照。
- プロジェクトのlockfileを利用する`uv run`では、lockfileを更新しないオプション（`--frozen`または`--locked`）の指定を必須とする。prekなどのツールは親環境の`UV_FROZEN`環境変数を引き継がないためである。
- `uv run --script`においては、スクリプト用のlockfileが存在する場合に限り、lockfileを更新しないオプションを付与する。スクリプト用lockfileが存在しない対象に対して`--frozen`を指定すると、`Unable to find lockfile for Python script`（終了コード2）のエラーとなる。依存解決の結果を固定したい場合は、事前に`uv lock --script <スクリプトの絶対パス>`を実行してlockfileを生成した上で指定する。
- `platformdirs`の`user_config_dir`、`user_cache_dir`、`user_data_dir`などを呼び出す際は、`appauthor=False`を明示する。この引数を省略すると、Windows環境において`appauthor`に`appname`が自動補完され、`%LOCALAPPDATA%\<appname>\<appname>\...`のようにディレクトリ名が重複する構造になる。

### argparseとCLIエントリポイント

- 既存の引数パーサーや共通の`add_argument`処理に新しいオプションを追加する前に、対象の名前空間で同じオプション文字列がすでに使われていないか検索する。同一オプションの重複登録は、実行時のオプション衝突エラーを引き起こす。
- `action="append"`を指定した引数において、コマンドラインで指定されなかった場合の初期値は`default=None`に設定する（空リストなどの非None値を設定すると、型チェックでエラーになったり、意図しない初期要素が毎回混入したりする原因になる）。環境変数によるフォールバック値の解決は、`parse_args()`を呼び出した後に値が`None`であった場合のみ行う。
- コマンドラインオプションに対して環境変数や設定ファイルなどの値解決手段を追加する場合でも、`type=int`などの型変換引数はそのまま残す。未指定時の値解決処理は別の関数に切り出すことで、CLIで直接不正な値が指定された際に早期に型エラーを検出できるようにする。
- CLIのエントリポイント関数は`main`という名前で公開する。正常終了時・異常終了時のいずれにおいても、終了コードを`sys.exit(exit_code)`で明示的に指定するか、戻り値として`-> int`を返して呼び出し側で`sys.exit(main())`に渡す。終了コードの値は処理結果に基づいて決定する。個々の処理対象のエラーを捕捉して警告を表示しつつ残りの処理を継続する場合、失敗した処理が1件以上存在すれば、全体の終了コードは0以外（非ゼロ）とする。終了コードを常に0（成功）として固定できるのは、エラーを捕捉して処理を続行する仕組みを持たない単純な関数に限られる。テストコードではこの公開関数を直接呼び出し、失敗終了のパターンも含めたすべての分岐で正しい終了コードが返されることを検証する。
- `pyproject.toml`の`[project.scripts]`に定義された`module:main`の参照を追加または変更した場合は、実際に登録されたコマンド名を実行して動作を確認する（`python -m`によるモジュール実行では`[project.scripts]`の登録内容を経由しないため）。

## 静的解析の誤検出と抑制

- lintの指摘への対応は`implementation-time.md`「lintと機械チェック」に従う。Pythonでは複数のチェッカーが同じ箇所に対して重複して警告を出すことが多いため、`assert`や`del`などの標準的な構文を用いてコード側で解決できる場合は、抑制コメントよりも構文による解決を優先する。
- `subprocess.run(..., capture_output=True)`の戻り値`proc.stdout`は、型チェッカーによって`None`を取り得る型（`bytes | None`など）として推論される（`text=True`を指定した場合でも同様に`None`の可能性が残る）。これを使用する前に、`assert isinstance(proc.stdout, bytes)`などで型を絞り込む。
- `sys.path.insert()`などを用いて動的にパスを変更した直後に`import`文を記述すると、pylintが`wrong-import-position`と`import-error`の両方の警告を誤って出すことがある。この場合は`# pylint: disable=wrong-import-position,import-error`を併記して抑制する。
- 意図的に関数内で`import`を行う場合は、`# noqa: PLC0415  # pylint: disable=import-outside-toplevel`を併記して警告を抑制する。
- `ty`と`mypy`の両方が有効になっている環境で型の不整合に関する警告を抑制する場合は、`# type: ignore[<mypy-code>]  # ty: ignore[<ty-rule>]`のように同一行に両方の抑制コメントを併記する。

実行環境のOSに存在しない標準モジュールのインポートでは、OS判定による分岐の両側のブロックに加え、特定OS専用のモジュール内やテストコード内であっても、`# pylint: disable=import-error`とその理由をコメントに明記する。
pylintは`sys.platform`や`os.name`による条件分岐をimportエラーの無視条件として認識せず、解析を実行しているOS上に存在しないモジュールを検出するとエラーを報告するためである。
一方のOSでのみ警告を抑制した場合、別のOS上でpylintを実行した際に初めてエラーが検出されることになる。
監査記録は`docs/development/audit-records.md`「agent-basics/skills/writing-standards/references/python.md：静的解析の誤検出と抑制：2026年10月20日」を参照。

## テストコード（pytest）

- テストファイルの配置は、各プロジェクトの方針に従う。特段の方針がない場合は、同居方式か専用ディレクトリにまとめる集約方式（`tests/<module>_test.py`）のいずれかを、プロジェクトの規模や配布形態に応じて選択する。同居方式では`<name>.py`と同じ場所に`<name>_test.py`を配置し、パッケージ配布時の除外設定が必要となる。
- テストフレームワークは対象プロジェクトですでに使われているものに合わせる。新規に選定する場合は`pytest`を採用する。
- 同一のテストロジックに対して異なる入力を網羅する場合は`@pytest.mark.parametrize`を用い、戻り値を使わず副作用（事前準備など）のみを目的とするfixtureには`@pytest.mark.usefixtures`を使用する。デコレーターを重ねる順番は、外側から順に`parametrize` → `asyncio` → `usefixtures`とする。
- コレクションが空であることを検証する際は、`assert not x`と記述する。pylintの`use-implicit-booleaness-not-comparison`規則では、空リスト等との等値比較（`x == []`）ではなく、真偽値評価（`not x`）による判定が求められる。
- 大規模なテストスイートを実行する際は、`pyfltr`の`command-timeout`の設定値を延長する。具体的な設定値は実際の計測結果に基づいて判断し、各プロジェクトの`pyproject.toml`内の`[tool.pyfltr]`セクションに記載する。デフォルトの設定値や挙動の詳細は、<https://ak110.github.io/pyfltr/llms.txt> で現行バージョンの仕様を確認する。

Windows環境における仮想環境の`python.exe`は、Python本体を子プロセスとして起動するためのランチャー（中継役）として動作する。
この仕組みにより、`subprocess.Popen([sys.executable, ...]).pid`で得られるプロセスのPIDと、子プロセス内部で`os.getpid()`を呼び出して取得したPIDが一致しない。
子プロセス自身の実際のPIDと比較するテストを行う場合は、標準出力やファイルを経由して子プロセスから自己申告させたPIDを使用する。
一方で、プロセスの親子ツリーのルートとしてプロセスの停止や生存確認を行う目的であれば、`Popen.pid`をそのまま利用できる。
監査記録は`docs/development/audit-records.md`「agent-basics/skills/writing-standards/references/python.md：テストコード（pytest）：2026年10月20日」を参照。

### Fixtureのコーディングルール

- fixture関数名はアンダースコア（`_`）で始め、テスト関数側から明示的に参照する場合は引数の`name`パラメーターで公開名を指定する。
- fixtureの`scope`や`autouse`は、テスト間の入力の独立性と後始末が確実に保たれる範囲で共有する。複数の値を返すfixtureでは、戻り値の型エイリアスやdataclassを定義して各値の意味を明確にする。

### 非同期テスト

- `pytest-asyncio`を使用する際は`asyncio_mode = "strict"`の設定を推奨する。非同期テスト関数には`@pytest.mark.asyncio`を、非同期fixtureには`@pytest_asyncio.fixture`を明示的に付与する。strictモードでは、`@pytest.fixture`と`async def`の組み合わせは正常に機能しない。

### 環境変数・設定ディレクトリのテスト隔離

- 設定ディレクトリや環境変数を読み込むCLIのテストを作成する際は、そのCLIが設定を読み取る可能性のある場所をプラットフォーム横断で漏れなく洗い出し、`monkeypatch`を使ってすべてのパスを一時ディレクトリ（`tmp_path`配下）に向ける共通fixtureを用意する。独自に定義された環境変数に加えて、POSIX環境の変数（`HOME`、`XDG_CONFIG_HOME`、`XDG_CACHE_HOME`、`XDG_DATA_HOME`など）を対象に含める。また、Windows環境の変数（`LOCALAPPDATA`、`APPDATA`、`USERPROFILE`、`PROGRAMDATA`など）も対象に含める。特定のOS向けに作成されたCLIであっても、テストを実行しているホストOSと参照対象のOS変数が異なると、隔離漏れが発生するためである。

### monkeypatchによる関数差し替え

- モックやスタブとして差し替える代用関数は、副作用と戻り値の返却をlambda式の中に混在させず、独立した通常の`def`関数として明示的に定義する。
- 代用関数の引数シグネチャの末尾には`**kwargs`を含め、将来的にテスト対象APIへ引数が追加された場合でもシグネチャ不整合で壊れないようにする。受け取った引数をそのまま別の関数へ委譲する代用関数の`*args`および`**kwargs`には、型ヒントとして`typing.Any`を指定する（`object`を指定すると、委譲先の呼び出し部分でpyright、ty、mypyなどの型チェッカーが型不一致エラーを報告するため）。

### ロギング出力の検証

- pytestの`caplog`はルートロガー（root logger）へ伝播したログレコードのみを捕捉するため、`propagate=False`に設定されたロガーの出力は捕捉できない。また、`capsys`が差し替えた`sys.stdout`と、テスト用fixture内で新規に追加した`StreamHandler(sys.stdout)`が参照しているストリームオブジェクトが一致しない場合がある。テスト対象のロガーに対してログ収集用の`logging.Handler`を直接登録し、fixtureの終了時（後始末）にそれを確実に削除するように構成すると、これらの環境条件に左右されずに安定してログ出力を検証できる。

## Python新構文と導入バージョン

新しい構文を採用できるかどうかは、対象コードがサポートするPythonバージョンと、`peps.python.org`の公式メタデータに記載された`Python-Version`を基準に判断する。
例えば、Python 3.14のPEP 758では`except`や`except*`における例外型の括弧の省略が認められているが、`as`節を用いて例外インスタンスを変数に束縛する場合は従来どおり括弧が必要となる。

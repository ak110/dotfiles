# Python記述スタイル

本書は、Pythonのコードとテスト（pytest）を書く・直す・レビューする時に、一般知識から導けない固有の事実と採る書き方を示す。
一般知識で導ける作法を書かないのは`agent-basics:writing-standards/references/quality-principles-subtraction.md`の適用である。
危険な構文を避ける規定は`agent-basics:writing-standards/references/quality-principles-constraints.md`の適用である。
ruff、pylint、型検査が導入されている環境では、その診断に従う。

## 言語スタイル

### import・型・docstring

- docstringは原則として省く。
  `__doc__`、`inspect.getdoc()`、引数を省いた `@mcp.tool()` などが参照する場合は、明示の引数へ移すか必要な部分だけを残す。
- `import`は可能な限り`import xxx`の形で最上部に書き、`as`の別名は定着した略称（`np`など）、名前の衝突の回避、互換APIへの差し替えに限る。
- `@typing.final`のメソッドはサブクラスの都合で外さず、差は抽象メソッドへの委譲などで吸収する。

### 標準ライブラリとログ

- 表形式のデータの処理を新しく選ぶ時は`pandas`より`polars`を選び、既存のプロジェクトでは採用済みのものに合わせる。
- シグネチャの都合で受けるが使わない引数は、関数の先頭で`del xxx # noqa`と書く。
- `Literal`で分岐する時は、`assert_never`などで全ての場合を網羅したことを確かめる。
- `subprocess`をテキストモード（`text=True`か`encoding`の指定）で起動すると、Windowsでは入力の`\n`が`\r\n`へ変わる。
  改行を厳密に保つ子プロセス（`git update-ref --stdin`など）へは、バイト列へ符号化して渡す。
- `subprocess`のテキストの入出力ではUTF-8を明示し、呼び出し先が別の文字コードを定める時はそれを指定する。
- `isinstance(x, int)`と`isinstance(value, type(reference))`は`bool`の値にも真を返すため、数値だけに限る時は`bool`を除く条件を足す。

### 入力検証とセキュリティ

- APIの境界と外部からの入力は、`pydantic` v2などの型で駆動する検証ライブラリで検証する。
- 外部入力は引数で渡し、SQLはプレースホルダーで組み立て、一時ファイルは`tempfile`の安全なAPIで作り、セキュリティ用途の乱数はCSPRNG（`secrets`など）で作る。
- PEP 750のテンプレート文字列（`t"..."`、Python 3.14以降）の安全性は後段のレンダラーで決まるため、SQLやHTMLの生成では安全対策を持つレンダラーと組み合わせる。

次の構文を避ける（括弧内はruffの規則名。配布先で有効とは限らないため、検査が無くても守る）。

- 動的なコードの評価: `eval`（`S307`）、`exec`（`S102`）
- 安全でない復元: `pickle`（`S301`）、`yaml.load`（`S506`）
- 推測できるパスの一時ファイル: `tempfile.mktemp`（`S306`）
- シェルを介したコマンドの実行: `shell=True`（`S602`、`S604`）、`os.system`など（`S605`）
- 文字列の結合や整形で組み立てたSQL（`S608`）
- セキュリティ用途の`random`（`S311`）

### 資源の寿命

- 資源は取得の直後に`with`か`async with`で囲み、寿命が関数やfixtureにまたがる時は`ExitStack`やfixtureの`yield`などで解放の責任を渡す。
- 資源を持つ自作のクラスには`__enter__`と`__exit__`（非同期は`__aenter__`と`__aexit__`）を実装する。
- 後始末を順に行う時は、前段で例外、`KeyboardInterrupt`、`CancelledError`が起きても後段の解放が実行される構造にする。
- pylintの`consider-using-with`とruffの`SIM115`を行単位で抑えるのは、常駐するプロセスが資源をプロセスの寿命を超えて持つ場合か、`__exit__`の仕様が要件に合わない場合に限り、理由をコメントに書く。

### 実行環境

Windowsでも実行・静的解析されるコードとテストは、次の差を事前に確かめる（Linuxでの検証はOSの差を検出できない）。

- 他のOSに無い標準モジュール（`fcntl`、`pwd`、`pty`、`msvcrt`、`winreg`など）がある。
- venvの`python.exe`が中継するため親と子でPIDが違い、テストでの扱いは本書「テストコード（pytest）」に書く。
- 実行ファイルは拡張子付きの名前や`.cmd`で起動する。
- `platformdirs`の`user_config_dir`などに`appauthor=False`を渡さないと、Windowsで`%LOCALAPPDATA%\<appname>\<appname>\...`と名前が重なる。

uvとPEP 723のスクリプト（`# /// script`のメタデータ）には次の固有の挙動がある。

- `pyproject.toml`がある階層の下で`uv run`すると、そのプロジェクトがインストールされる。
  独立したスクリプトは`uv run --no-project --script <path>`で実行する。
- `.python-version`はスクリプトの`requires-python`より優先され、その版が無いと`error: No interpreter found for Python <ver>`で失敗する。
  `--no-project`では避けられないため、`uv run --python <ver> --script <path>`で版を指定する。
- `uv run --script`の仮想環境のキャッシュはスクリプトのパスごとに分かれる場合がある（キャッシュキーの規則は公式に定めが無い）。
  時間制限のある実行の前に温める時は、実行時に使う絶対パスで温める。
- プロジェクトのlockfileを使う`uv run`には`--frozen`か`--locked`を付ける（prekなどは親の`UV_FROZEN`を引き継がない）。
- `uv run --script`では、スクリプト用のlockfileがある時だけ`--frozen`を付ける。
  lockfileが無いと`Unable to find lockfile for Python script`（終了コード2）で失敗するため、固定したい時は先に`uv lock --script <スクリプトの絶対パス>`で作る。

### argparseとCLIエントリポイント

- 既存のパーサーへオプションを足す前に、同じ名前空間で同じオプション文字列が使われていないかを検索する（重複は実行時の衝突エラーになる）。
- `action="append"`の引数は`default=None`にし、環境変数での補完は`parse_args()`の後で値が`None`の時だけ行う（空リストなどは型検査の誤りや初期要素の混入を招く）。
- 環境変数や設定ファイルでの値の解決を足しても`type=int`などの変換は残し、未指定時の解決は別の関数に分ける（CLIの不正値を早く型エラーにするため）。
- エントリポイントは`main`として公開し、終了コードを`sys.exit(exit_code)`か`sys.exit(main())`で明示し、対象ごとのエラーを捕捉して続ける処理は失敗が1件でもあれば0以外で終える。
  テストは`main`を直接呼び、失敗終了を含む全ての分岐の終了コードを確かめる。
- `[project.scripts]`の`module:main`を追加・変更したら、登録されたコマンド名で実行して確かめる（`python -m`は登録を経由しない）。

## Python新構文と導入バージョン

新しい構文を使えるかは、対象コードが対応するPythonの版と、`peps.python.org`のメタデータの`Python-Version`で判断する。
例えばPEP 758（Python 3.14）は`except`と`except*`の例外型の括弧を省けるが、`as`で束縛する時は括弧が要る。

## テストコード（pytest）

- テストファイルの配置はプロジェクトの方針に従い、無ければ同居（隣に`<name>_test.py`を置き配布物から除く）か集約（`tests/<module>_test.py`）を選ぶ。
- 入力だけが違うテストは`@pytest.mark.parametrize`、戻り値を使わないfixtureは`@pytest.mark.usefixtures`で書き、外側から`parametrize`、`asyncio`、`usefixtures`の順に重ねる。
- 空のコレクションの検証は`assert not x`と書く（pylintの`use-implicit-booleaness-not-comparison`）。
- pyfltrで大きなテストスイートを実行する時は、計測に基づいて`[tool.pyfltr]`の`command-timeout`を延ばす（現行の版の値と挙動は<https://ak110.github.io/pyfltr/llms.txt>で確かめる）。
- fixture関数の名前は`_`で始めてテストから参照する名前を`name`引数で公開し、複数の値を返すfixtureは型エイリアスかdataclassで各値の意味を示す。
- `pytest-asyncio`は`asyncio_mode = "strict"`で使い、テストに`@pytest.mark.asyncio`、fixtureに`@pytest_asyncio.fixture`を付ける（strictでは`@pytest.fixture`と`async def`の組み合わせは働かない）。
- monkeypatchの代用の関数は、副作用と戻り値をlambdaに混ぜず`def`で定義し、対象のAPIへ引数が増えても呼び出しが失敗しないよう引数の末尾に`**kwargs`を置く。
  引数をそのまま別の関数へ渡す代用の関数では、`*args`と`**kwargs`の型を`typing.Any`にする（`object`ではpyright、ty、mypyが渡し先で型の不一致を報告する）。

Windowsのvenvの`python.exe`は、Python本体を子プロセスとして起動するランチャーである。
そのため`subprocess.Popen([sys.executable, ...]).pid`は、子プロセスの中の`os.getpid()`と一致しない。
子プロセス自身のPIDと比べるテストでは、子プロセスに標準出力かファイルで自分のPIDを報告させる。
プロセスツリーの根として停止や生存を確かめる目的なら、`Popen.pid`をそのまま使える。

### 環境変数・設定ディレクトリのテスト隔離

- 設定ディレクトリや環境変数を読むCLIのテストは、CLIが設定を読みうる場所をOSを問わず洗い出し、`monkeypatch`で全てを`tmp_path`の下へ向ける共通のfixtureを置く。
- 対象には独自の環境変数に加え、POSIXの`HOME`、`XDG_CONFIG_HOME`、`XDG_CACHE_HOME`、`XDG_DATA_HOME`と、Windowsの`LOCALAPPDATA`、`APPDATA`、`USERPROFILE`、`PROGRAMDATA`を含める。
  特定のOS向けのCLIでも、テストを実行するOSと参照する変数が違うと隔離が漏れる。

### ロギング出力の検証

- `caplog`はルートロガーへ伝播したレコードだけを捕捉するため、`propagate=False`のロガーの出力を捕まえない。
  `capsys`が差し替えた`sys.stdout`と、fixtureで足した`StreamHandler(sys.stdout)`のストリームも一致しない場合がある。
  対象のロガーへ収集用の`logging.Handler`を直接登録し、fixtureの後始末で外すと、これらに左右されずに検証できる。

## 静的解析の誤検出と抑制

- Pythonでは複数の検査が同じ箇所を重ねて指摘しやすいため、`assert`や`del`などの構文で解決できる時は抑制のコメントより構文を選ぶ。
- `subprocess.run(..., capture_output=True)`の`proc.stdout`は、`text=True`でも型検査で`None`を含む型と推論されるため、使う前に`assert isinstance(proc.stdout, bytes)`などで型を限定する。
- `sys.path.insert()`の直後の`import`にpylintが`wrong-import-position`と`import-error`を誤って出す時は、`# pylint: disable=wrong-import-position,import-error`を併記する。
- 意図して関数の中で`import`する時は、`# noqa: PLC0415  # pylint: disable=import-outside-toplevel`を併記する。
- tyとmypyが両方有効な環境で型の警告を抑える時は、`# type: ignore[<mypy-code>]  # ty: ignore[<ty-rule>]`を同じ行に併記する。
- 実行中のOSに無い標準モジュールのimportには、OS判定の分岐の両側と、特定のOS専用のモジュールやテストの中でも、`# pylint: disable=import-error`と理由を書く。
  pylintは`sys.platform`や`os.name`の分岐を無視の条件として扱わないため、片方だけ抑えると別のOSで初めてエラーになる。

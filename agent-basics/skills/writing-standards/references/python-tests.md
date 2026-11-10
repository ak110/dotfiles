# Python記述スタイル：テストコード（pytest）

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## テストコード（pytest）

- テストファイルの配置は、各プロジェクトの方針に従う。
  特段の方針がない場合は、同居方式か専用ディレクトリにまとめる集約方式（`tests/<module>_test.py`）のいずれかを、プロジェクトの規模や配布形態に応じて選択する。
  同居方式では`<name>.py`と同じ場所に`<name>_test.py`を配置し、パッケージ配布時の除外設定が必要となる。
- テストフレームワークは対象プロジェクトですでに使われているものに合わせる。
  新規に選定する場合は`pytest`を採用する。
- 同一のテストロジックに対して異なる入力を網羅する場合は`@pytest.mark.parametrize`を用い、戻り値を使わず副作用（事前準備など）のみを目的とするfixtureには`@pytest.mark.usefixtures`を使用する。
  デコレーターを重ねる順番は、外側から順に`parametrize` → `asyncio` → `usefixtures`とする。
- コレクションが空であることを検証する際は、`assert not x`と記述する。
  pylintの`use-implicit-booleaness-not-comparison`規則では、空リスト等との等値比較（`x == []`）ではなく、真偽値評価（`not x`）による判定が求められる。
- 大規模なテストスイートを実行する際は、`pyfltr`の`command-timeout`の設定値を延長する。
  具体的な設定値は実際の計測結果に基づいて判断し、各プロジェクトの`pyproject.toml`内の`[tool.pyfltr]`セクションに記載する。
  デフォルトの設定値や挙動の詳細は、<https://ak110.github.io/pyfltr/llms.txt> で現行バージョンの仕様を確認する。

Windows環境における仮想環境の`python.exe`は、Python本体を子プロセスとして起動するためのランチャー（中継役）として動作する。
この仕組みにより、`subprocess.Popen([sys.executable, ...]).pid`で得られるプロセスのPIDと、子プロセス内部で`os.getpid()`を呼び出して取得したPIDが一致しない。
子プロセス自身の実際のPIDと比較するテストを行う場合は、標準出力やファイルを経由して子プロセスから自己申告させたPIDを使用する。
一方で、プロセスの親子ツリーのルートとしてプロセスの停止や生存確認を行う目的であれば、`Popen.pid`をそのまま利用できる。
監査記録は`docs/development/audit-records.md`「agent-basics/skills/writing-standards/references/python.md：テストコード（pytest）：2026年10月20日」を参照。

### Fixtureのコーディングルール

- fixture関数名はアンダースコア（`_`）で始め、テスト関数側から明示的に参照する場合は引数の`name`パラメーターで公開名を指定する。
- fixtureの`scope`や`autouse`は、テスト間の入力の独立性と後始末が確実に保たれる範囲で共有する。
  複数の値を返すfixtureでは、戻り値の型エイリアスやdataclassを定義して各値の意味を明確にする。

### 非同期テスト

- `pytest-asyncio`を使用する際は`asyncio_mode = "strict"`の設定を推奨する。
  非同期テスト関数には`@pytest.mark.asyncio`を、非同期fixtureには`@pytest_asyncio.fixture`を明示的に付与する。
  strictモードでは、`@pytest.fixture`と`async def`の組み合わせは正常に機能しない。

### 環境変数・設定ディレクトリのテスト隔離

- 設定ディレクトリや環境変数を読み込むCLIのテストを作成する際は、そのCLIが設定を読み取る可能性のある場所をプラットフォーム横断で漏れなく洗い出し、`monkeypatch`を使ってすべてのパスを一時ディレクトリ（`tmp_path`配下）に向ける共通fixtureを用意する。
  独自に定義された環境変数に加えて、POSIX環境の変数（`HOME`、`XDG_CONFIG_HOME`、`XDG_CACHE_HOME`、`XDG_DATA_HOME`など）を対象に含める。
  また、Windows環境の変数（`LOCALAPPDATA`、`APPDATA`、`USERPROFILE`、`PROGRAMDATA`など）も対象に含める。
  特定のOS向けに作成されたCLIであっても、テストを実行しているホストOSと参照対象のOS変数が異なると、隔離漏れが発生するためである。

### monkeypatchによる関数差し替え

- モックやスタブとして差し替える代用関数は、副作用と戻り値の返却をlambda式の中に混在させず、独立した通常の`def`関数として明示的に定義する。
- 代用関数の引数シグネチャの末尾には`**kwargs`を含め、将来的にテスト対象APIへ引数が追加された場合でもシグネチャ不整合で壊れないようにする。
  受け取った引数をそのまま別の関数へ委譲する代用関数の`*args`および`**kwargs`には、型ヒントとして`typing.Any`を指定する（`object`を指定すると、委譲先の呼び出し部分でpyright、ty、mypyなどの型チェッカーが型不一致エラーを報告するため）。

### ロギング出力の検証

- pytestの`caplog`はルートロガー（root logger）へ伝播したログレコードのみを捕捉するため、`propagate=False`に設定されたロガーの出力は捕捉対象外となる。
  また、`capsys`が差し替えた`sys.stdout`と、テスト用fixture内で新規に追加した`StreamHandler(sys.stdout)`が参照しているストリームオブジェクトが一致しない場合がある。
  テスト対象のロガーに対してログ収集用の`logging.Handler`を直接登録し、fixtureの終了時（後始末）にそれを確実に削除するように構成すると、これらの環境条件に左右されずに安定してログ出力を検証できる。

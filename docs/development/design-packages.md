# パッケージと検証環境の配置の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## agent-toolkit/agent_toolkit/のパッケージ構成

`agent-toolkit/agent_toolkit/`直下には配布物の外部から絶対パスで解決される公開スクリプトだけを置く。
該当するファイルはフック共通の`hook.py`、CLI`atk.py`、MCPサーバー`agents_server_mcp.py`、CI待機`wait_ci.py`、managed-tempの後始末`_managed_temp.py`、およびリモートホスト上で読み込んで実行するヘルパー2件とする。
それ以外の実装モジュールは、責務ごとのサブパッケージ`_common`・`_git`・`_plan`・`_atk`・`_agents_server`・`_hooks`へ収める。
この6つを依存の層とし、この並び順を層の順序とする。
後ろの層は前の層をimportしてよく、前の層は後ろの層をimportしない。
同じ層の中のimportは制限しない。
テスト専用の共有ヘルパーは`_testing`へ収める。
`_testing`は層の順序に含めない例外とし、`*_test.py`だけがimportできる。
非テストモジュールから`_testing`をimportしない。

構造の目的は、モジュールの所属をディレクトリで表し、ファイル名の接頭辞に依存しない参照へ変えることである。
接頭辞による群分けは、群に属するモジュールが増えるほど一覧の見通しを損なう。
群をまたぐ依存の向きも表せない。
サブパッケージは、所属と依存の向きの双方をディレクトリ階層で表す。

これらのスクリプトだけを直下へ残すのは、ファイルパスが配布物の外部契約であるためである。
`agent-toolkit/hooks/hooks.json`・`agent-toolkit/mcp.json`・`agent-toolkit/bin/atk`と2つのインストーラーは各ファイルのパスを直接指す。
リモートホスト側のヘルパーは、リモートホスト上の固定パスから本文を読み込んで実行する。
直下のスクリプトを含む全てのモジュールをサブパッケージへ移す案は、これらの外部契約を同時に変えるため採らない。

直下に置く公開ファイルは接頭辞`_`を付けずに命名する。
`_managed_temp.py`だけは接頭辞を残す。
このパスは`agent-toolkit/agent_toolkit/_hooks/permissionrequest_codex.py`がCodex側の許可判定で解決し、hookの出力契約テストが生成するコマンド文字列にも現れる。
改名すると同じコマンドが許可されなくなるため、名前を維持して`agent-toolkit/agent_toolkit/script_prefix_invariant_test.py`へ`_managed_temp.py`の除外を置く。

サブパッケージ内のimportには相対importを使わず絶対importを使う。
`scripts/check_script_imports.py`は`sys.path.insert`の静的評価と絶対importの解決によりPEP 723スクリプトのimport到達性を確かめる。
同スクリプトは相対importを解析の対象にしないため、相対importへ変えると検証の網羅性が失われる。
同スクリプトは層の順序に反するimportと、非テストモジュールからの`_testing`のimportも検出して失敗する。
同スクリプトは`agent-toolkit/agent_toolkit/`直下に公開スクリプト、`__init__.py`、`conftest.py`およびテスト以外のモジュールがある場合も失敗する。
層の順序の判定はサブパッケージだけを走査するため、層に収まらないimportを持つモジュールを直下へ置くと、直下の配置の規定から外れても検出されない。

モジュール名からは所属を表す接頭辞を除く。
ただしPythonの組込み名と標準ライブラリのトップレベル名に一致する名前は使わない。
`help`・`format`・`list`のように組込み名と重なる名前は、担う責務を表す語へ置き換える。

対象モジュールの動作テストは、`pytools-edit`「テスト配置」に従い、同じディレクトリへ`<モジュール名>_test.py`として置く。
`--import-mode`を指定せずにpytestを実行すると（`prepend`）、パッケージに属するテストはパッケージ名を含む一意なモジュール名で読み込まれる。
そのため、直下へ平坦に並べていたときに必要だったファイル名の一意性の制約が外れる。
既存のファイル名は維持し、この緩和を理由とする改名はしない。

肥大化したモジュールは、パッケージ化と同じ責務の区分でサブモジュールへ分ける。
分割だけを行いパッケージ化しない案は、直下のファイル数をさらに増やして所属の判別を難しくするため採らない。

## pyfltrのsubproject分割とチェック設定の置き場所

`agent-toolkit/`は独自の`pyproject.toml`と`uv.lock`を持つため、pyfltrはこれをsubprojectとして分割する。
分割したチェックは`agent-toolkit/pyproject.toml`とそのcwdの設定ファイルだけを読み、分割しないチェックはリポジトリ直下の設定だけを読む。
どのチェックがどちらから設定を読むかを設定ファイルから読み取れないため、直下だけの変更が`agent-toolkit/`配下のチェックへ届かない事象が繰り返し起きた。
lycheeの`.lycheeignore`が届かずCIが外部サイトの応答待ちで失敗した事例と、カスタムコマンドが`agent-toolkit/`配下の指定でskippedになる事例がこれに当たる。

設定の置き場所は次のように決める。

- pytest、mypy、pyright、ty、pylintなどのPython系のチェックは、agent-toolkitの環境で動く必要があるため分割を維持する。これらが読む`[tool.pyfltr]`のキーと`[tool.ruff]`・`[tool.pylint]`・`[tool.mypy]`・`[tool.pyright]`・`[tool.pytest.ini_options]`・`[tool.arid]`には両側へ同じ値を置く
- markdownlint、textlint、lychee、直下で定義した全カスタムコマンドなど、agent-toolkitの環境に依存しないチェックは`<名前>-subproject-aware = false`で分割を無効にし、直下の設定と設定ファイルでリポジトリ全体を1回で調べる。shellcheck、shfmt、colloquial-check、typosはpyfltrが元から分割しない
- 分割しないチェックのキーと設定ファイル（`.lycheeignore`、`.markdownlint-cli2.*`、`.textlintrc*`、`.textlintignore`）は`agent-toolkit/`側に置かない。置いても読まれず、直下と値が一致しなくても気付けない

`pyfltr_subproject_config_invariant_test.py`がこの対応を保証する。
分割しないチェックの値と設定ファイルがsubproject側に無いこと、直下のカスタムコマンドが分割を無効にしていること、分割するチェックと全体に作用するキーが両側で一致することを確かめる。
分割の有無はpyfltrの`resolve_subproject_aware`で直下の設定から求め、pyfltrの判定をテスト側に再実装しない。
探索パスをcwd基準で書く`ty-args`と`[tool.pyright]`の`extraPaths`、直下の実行だけの事情による`mypy-exclude`、直下基準のパスだけを持つ`extend-exclude`は意図的な差異としてテストに理由付きで列挙する。
意図的な差異を加える場合は同じ列挙へ理由とともに加える。
`[tool.typos]`はtyposが各ファイルに最も近い設定を自ら読み、語の不足は変更時点のチェックで失敗として現れるため対象から外す。

`subproject-exclude`で分割そのものを外す案は、Python系のチェックがリポジトリ直下の環境で動いてagent-toolkitの依存と設定を使えなくなるため採らない。
個別のキーを見つけるたびに両側へ値を追随させ、そのキーだけを比べるテストを足す案は、テストの無いキーで同じ事象が続くため採らない。

## テストの実行環境の隔離

テストを開発機だけにある状態から切り離す隔離は、`agent-toolkit/agent_toolkit/_testing/isolation.py`の1箇所に定義する。
対象の次元は、ホームと設定ディレクトリ、private-notes、一時ディレクトリ、Gitのglobal・system設定、開発セッションの環境変数、PATH上の開発機専用のエージェントCLI（`codex`・`claude`・`agy`）の6つである。
リポジトリ直下の`conftest.py`（`pytools/`・`scripts/`）と`agent-toolkit/conftest.py`（`agent_toolkit/`・`skills/`）が同じ定義をautouseで適用する。
pytestは祖先ディレクトリのconftestだけを読むため、起動範囲ごとにconftestを置くと隔離の集合が範囲ごとに異なり、範囲を横断する定義を持たないまま事象ごとに次元を足す運用になっていた。その結果、開発機で成功しCIだけで失敗する事象が、Git設定、private-notes、PATH上のCLIと次元を変えて繰り返された。
リポジトリ直下から`agent-toolkit/`配下を指定して起動すると2つのconftestが読まれるが、fixture名が同じため近い側の定義だけが適用され、二重に適用されない。

PATHの隔離は、エージェントCLIを含むディレクトリを、CLI以外の項目へのシンボリックリンクだけを持つ一時ディレクトリへ置き換える。ディレクトリごと外すと同じ場所の`uv`などテストが使うツールまで失われる。置き換えたPATHはセッションで1回だけ組み立てる。
実際のCLIやホームを意図して使うテストは、`host_environ`で子プロセスへ渡す環境変数を組み立てるか、`restore_host_environment`で同じプロセスの値を戻す。
`log_level = "DEBUG"`の取り込みからは、依存ライブラリのロガーを外す。定義は`agent-toolkit/agent_toolkit/_testing/dependency_logging.py`が持ち、2つのconftestが隔離と同じ形で適用する。
`log_level`は本リポジトリのコードのDEBUGログを失敗報告へ残すために置いているが、pytestは全ロガーへ一律に閾値を下げるため、markdown-it-pyのDEBUGレコード（1200行の計画の確認1件で約40万件）の記録にテストの所要の大半を使っていた。
インストール済みの配布物が提供する上位パッケージ名のロガーの閾値をINFOへ上げ、配布物として登録されない本リポジトリのコードはDEBUGの取り込みを残す。
`log_level`を下げる案は本リポジトリのDEBUGログを失い、姉妹プロジェクトとそろえた設定の目的に反するため採らない。ライブラリを名指しで外す案は、今後加わる依存へ働かないため採らない。

`asyncio.create_subprocess_exec`全体を遮断するファイル単位の仕組みを全範囲へ広げる案は、正当に子プロセスを起動する他のテストまで止めるため採らない。CIと同じ条件の環境で追加実行する案は、テスト自身が開発機でも失敗する本方式が成立するため採らない。

## 確かめる対象の近くへのテスト配置

文書・設定の実物を読むテストがPython実装のディレクトリにあると、文書や設定の変更からテストをたどれない。
個々の編集対象とテストの対応表を規範へ足す案は、新しいテストのたびに対応表を更新する費用を生むため採らない。
配置の定義元を`pytools-edit`「テスト配置」へまとめ、実物を読むテストはその実物か対象群を包含する最も近いディレクトリへ置く。
fixtureの文書を入力にして実装を呼ぶテストは実装の動作テストであり、実装と同居させる。
混在するファイルから実物を読むテストだけを分離し、assert・パラメーター・対象集合を維持する。

全ソースの走査と、文書と実装、マニフェストと設定など複数領域の契約の整合を調べるテストを横断テストとし、ファイル名`*_invariant_test.py`で識別する。
rootと`agent-toolkit/`の`pyproject.toml`の`pytest-fast-targets`に同じ単一globを指定し、commit時にprekが起動するfastから実行するため、通常のcommitで担当がファイル一覧を組み立てる必要がない。
通常テストと混在するファイルからは横断テストだけを近接する専用ファイルへ分ける。
pytestのマーカーでも識別する案は、fastがマーカーを選択に使わず、マーカーとファイル名の一致を保つ仕組みを別に要するため採らない。
通常の動作テストを一律にfastへ加えず、Codex投影も既存の除外で収集対象から外す。
検証する主体は近くのテストを探索の起点にし、`verification-scope.md`の直接消費側の類型でパッケージ境界を越える対象も集める。

隠しディレクトリのchezmoi配布原本はpytestの通常の収集では対象に入らないため、その契約を読むrootのテストを維持する。
近接のためだけにpytestの再帰設定やconftestを増やす案は、収集と隔離の別系統を生むため採らない。
Codex投影は既存の通常ファイルの同期を維持し、移動したテストも含める。wheelは既存のPythonパッケージだけを含み、テストを除外する。
生成同期と説明文の自動チェックのglobは新配置に追随させる。
fastへの集約はテストの対象選択を自動化する変更であり、commit後にrebaseでできた組合せを確かめる役割も保持する。
統合時も同じfastのpytestを実行して組合せを確かめる。
個別のテスト一覧を設定へ写す案は移動のたびに更新を要し、独立したhookを足す案は同じテストを別の呼び出しでも起動するため採らない。

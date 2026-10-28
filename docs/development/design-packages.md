# パッケージと検証環境の配置の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## agent-toolkit/agent_toolkit/のパッケージ構成

`agent-toolkit/agent_toolkit/`直下には配布物の外部から絶対パスで解決される公開スクリプトだけを置く。
該当するファイルはフック共通の`hook.py`、CLI`atk.py`、MCPサーバー`agents_server_mcp.py`、CI待機`wait_ci.py`とする。リモートホスト上で読み込んで実行するヘルパー2件（`atk_serve_plans_remote_helper.py`・`atk_serve_sessions_remote_helper.py`）は`agent-toolkit/scripts/`に置く。
それ以外の実装モジュールは、責務ごとのサブパッケージ`_common`・`_git`・`_plan`・`_atk`・`_agents_server`・`_hooks`へ収める。
この6つを依存の層とし、この並び順を層の順序とする。
後ろの層は前の層をimportしてよく、前の層は後ろの層をimportしない。
同じ層の中のimportは制限しない。
直下の公開スクリプトと`agent-toolkit/skills/*/scripts/`配下のスクリプトは、層の外から起動されるスクリプト（以下、起動スクリプト）とし、全ての層をimportできる。
ただし`_hooks`をimportできる起動スクリプトは`hook.py`だけとする。
`_hooks`はhookの実装だけを持ち、シェルのコマンド文字列の解析やセッション状態ファイルの読み書きのようにhook以外からも使う処理は`_common`などの前の層へ置く。
テスト専用の共有ヘルパーは`_testing`へ収める。
`_testing`は層の順序に含めない例外とし、`*_test.py`・`conftest.py`と`_testing`配下のモジュールだけがimportできる。
利用側がテストだけのモジュール（計画本文のテスト入力の組み立て、hook出力契約のJSON Schemaなど）も`_testing`へ置き、本番のサブパッケージに残さない。
Gitリポジトリの作成とGitコマンドの実行は`_testing/git_repository.py`に1つ置き、各テストは同じ手順を個別に持たない。

構造の目的は、モジュールの所属をディレクトリで表し、ファイル名の接頭辞を前提としない参照へ変えることである。
接頭辞による群分けは、群に属するモジュールが増えるほど一覧の見通しを損なう。
群をまたぐ依存の向きも表せない。
サブパッケージは、所属と依存の向きの双方をディレクトリ階層で表す。

これらのスクリプトだけを直下へ残すのは、ファイルパスが配布物の外部契約であるためである。
`agent-toolkit/hooks/hooks.json`・`agent-toolkit/mcp.json`・`agent-toolkit/scripts/atk`と2つのインストーラーは、共通の`scripts/launch.py`へ起動対象のパスを渡す。
リモートホスト側のヘルパーは、リモートホスト上の固定パスから本文を読み込んで実行する。
直下のスクリプトを含む全てのモジュールをサブパッケージへ移す案は、これらの外部契約を同時に変えるため採らない。

直下に置く公開ファイルは接頭辞`_`を付けずに命名する。
旧`_managed_temp.py`は、標準コマンドで一時領域を扱う2026年10月25日のユーザー指示に従って撤去した。

サブパッケージ内のimportには相対importを使わず絶対importを使う。
`scripts/check_script_imports.py`は`sys.path.insert`の静的評価と絶対importの解決によりPEP 723スクリプトのimport到達性を確かめる。
同スクリプトは相対importを解析の対象にしないため、相対importへ変えると検証の網羅性が失われる。
同スクリプトは層の順序に反するimportと、`*_test.py`・`conftest.py`・`_testing`配下以外のモジュールからの`_testing`のimportも検出して失敗する。
同スクリプトは`agent-toolkit/agent_toolkit/`直下に公開スクリプト、`__init__.py`、`conftest.py`およびテスト以外のモジュールがある場合も失敗する。
層の順序の判定はサブパッケージに加えて、直下の公開スクリプトと`skills/*/scripts/`配下の非テストのスクリプトも走査し、`hook.py`以外の起動スクリプトからの`_hooks`のimportを失敗にする。
同じ走査で、`_testing`配下を除く非テストのモジュールが`agent-toolkit/pyproject.toml`の開発用の依存グループにだけあるパッケージ（`jsonschema`など）をimportした場合も失敗にする。
開発環境には開発用の依存が導入済みのためテストは成功するが、配布先の環境には無く、そのモジュールを読み込んだ時点で失敗するためである。
`_testing`配下をこの走査から除くのは、テスト専用の依存を使う場所が`_testing`であり、本番のコードから`_testing`へのimportを前述の走査が失敗にするため、開発用の依存が本番のコードへ届かないからである。

モジュール名からは所属を表す接頭辞を除く。
ただしPythonの組込み名と標準ライブラリのトップレベル名に一致する名前は使わない。
`help`・`format`・`list`のように組込み名と重なる名前は、担う責務を表す語へ置き換える。

対象モジュールの動作テストは、`dotfiles-development`「テスト配置」に従い、同じディレクトリへ`<モジュール名>_test.py`として置く。
`--import-mode`を指定せずにpytestを実行すると（`prepend`）、パッケージに属するテストはパッケージ名を含む一意なモジュール名で読み込まれる。
そのため、直下へ平坦に並べていたときに必要だったファイル名の一意性の制約が外れる。
既存のファイル名は維持し、この緩和を理由とする改名はしない。

肥大化したモジュールは、パッケージ化と同じ責務の区分でサブモジュールへ分ける。
分割だけを行いパッケージ化しない案は、直下のファイル数をさらに増やして所属の判別を難しくするため採らない。
分けたサブモジュールは、使う名前を定義元から自らimportし、単独でimportできる状態を保つ。
相互に名前を使う組は、共有する名前を依存の末端のモジュールへ移すか、責務の境界を引き直して依存の向きを1方向にする。
分割前の名前空間を`__init__.py`が兄弟モジュールの名前の注入（`vars(...).setdefault`）とモジュールの型の置換で再現する構造は採らない。
この構造ではサブモジュールを単独でimportできず、同名の定義の衝突は注入の順序で解決されて警告も出ず、未定義名の検出をファイル単位で抑止することになる。
`__init__.py`はパッケージ外の呼び出し元が使う名前だけを定義元から再exportし、テストは名前を参照するサブモジュールの属性を差し替える。

`scripts/check_script_imports.py`は`agent-toolkit/agent_toolkit/`と`agent-toolkit/skills/*/scripts/`の非テストのモジュールで、次の2つを失敗にする。

- ファイル単位の`# ruff: noqa`による`F821`（未定義名）の抑止
- モジュールのトップレベルで名前空間へ書き込む処理（`vars(...)`・`globals()`への代入・`setdefault`・`update`と`sys.modules[...].__class__`の置換）

どちらも名前の注入を再び持ち込む書き方である。
ruffには特定の規則のファイル単位の抑止だけを禁じる設定が無いため、同スクリプトの判定へ加えた。
行単位の`# noqa: F821`は前方参照など特定の1行だけを抑止し、名前の注入を必要としないため対象から外す。
名前空間の読み取り（`vars(importlib.import_module(...))[...]`の参照）と関数の中の書き込みも、読み込み時の注入ではないため対象から外す。

`_hooks`の実装モジュールは、関数の実行時までだけ使う依存を`lazy import`・`lazy from`で読み込む。
hookはイベントごとに別プロセスで起動し、入力に応じて処理分岐の大半を実行せずに終わる。
それでもトップレベルのimportは初期化時に全て実行されるため、例えばPreToolUseは対象外の入力でも委譲の観測や報告の解析の依存まで読み込んでいた。
Python 3.15はPEP 810に従い、`lazy`を付けたimportを初回の使用時に読み込む。dotfilesとagent-toolkitのPython下限は3.15とする。
CLIと`pytools/`の各コマンドも、ヘルプや補完など一部の処理でだけ使う依存に同じ書き方を使う。
モジュールの定数、デコレーター、データクラス、正規表現と表の構築に使う依存は定義時に実体化するため`lazy`を付けない。
公開`hook.py`が常に読み込む標準ライブラリ（`json`・`os`・`pathlib`・`sys`など）も、`lazy`を付けても読み込みが減らないため対象から外す。
関数内importとStop判定のホスト別の動的な選択は、そのまま残す。

以前はモジュール冒頭の`__lazy_modules__`へ対象モジュールを列挙し、`lazy import`構文への一括置換は行わない判断としていた。
構文を解釈できない旧Pythonでも同じファイルを通常のimportとして実行できることが、列挙方式を採用した理由である。
Python 3.15以降だけを前提にするユーザーの要求を受けて、この判断を改めた。
下限が3.15になったため旧版向けの互換は不要となり、列挙とimport文が離れた書き方は、importの追加・削除・改名の際に列挙だけが古いまま残る原因になる。
例えば`from <パッケージ> import <サブモジュール>`の形で読むサブモジュールを列挙しても遅延されない。列挙と比べる名前は文のモジュール名だからである。
Python 3.15.0で`from xml import dom`に`xml.dom`を列挙すると通常のmoduleとなり、`xml`の列挙と`lazy from`では遅延される。
宣言と実際の読み込みのこの不一致は、列挙の文面からは見分けにくい。
各import文へ`lazy`を付ける書き方は、遅延の対象を文そのものが示すため、この不一致が生じない。

importを関数の中へ移す案は、トップレベルでimportする記述規約から外れ、関数ごとに抑制コメントを要するため採らない。
全プロセスの遅延を切り替える`-X lazy_imports=all`、独自のloaderと読み込み結果の永続キャッシュは、モジュールごとの指定だけで目的の読み込み数の削減を得られるため設けない。
`agent-toolkit/agent_toolkit/hook_test.py`は公開`hook.py`を子プロセスで実行し、遅延を無効化した対照より読み込むモジュールが少なく、応答が同じであることを確かめる。
PEP 810は無効化の指定として`-X lazy_imports=none`を記すが、Python 3.15.0はこの値を拒否するため、対照は`sys.set_lazy_imports_filter`へ常に偽を返す関数を渡して作成する。

## 起動層と子プロセスの環境

配布rootのコードと共有する依存環境を分け、起動したプロセスの子へは呼出元の環境を戻す。
`plugin_environment`は起動のために変えた環境値を変更前後の組として記録し、`inherited_venv`は現在値が起動時の値のままの項目だけを復元する。
起動後の変更と、委譲先へ意図して渡す共有データ領域は保持する。
この差分記録により、起動層へ値を加えたときに復元側の個別列挙を増やす必要がなくなる。

内部の再起動は起動側の配布rootを明示し、外側へ渡す環境の復元と分ける。
環境を渡せる呼出元は同じ`direct_environment`を使い、子の環境を整えるCLIとAPIキー補助コマンドは同じPythonのプロセス内でrootを読込先へ明示する。
内部CLIへ共有環境の準備を要求するbootstrapを重ねる案は、現在のPythonと依存を使える状況でも隔離HOMEのready markerを要求して失敗するため採らない。
起動元の`PYTHONPATH`を全子孫へ残す案は別worktreeのテストを配布物で動かし、単純に除く案は内部再起動を壊すため、外側への復元と内部root明示を同じ変更単位で扱う。
pytestは対象側のパッケージ構造と取得した`agent_toolkit`の実体を比べ、別版を読み込んだ検証を開始時に拒否する。

## pyfltrのsubproject分割とチェック設定の置き場所

dotfilesとagent-toolkitのmcp依存はSDK 2系を要求し、各lockは上書きなしの通常解決で生成する。
以前のmcp上書きはsemgrepによる脆弱版の固定を避けるために導入したが、pyfltrの2系要求まで1系へ置き換え、lock成功後にMCP起動のimportを失敗させた。
SDK移行後は通常解決で脆弱版の固定を避けられるため、両プロジェクトと配布先導入の上書きを撤去する。
宣言依存の両立判定をuvへ戻し、将来の要求の衝突を解決時に検出する。別環境のuvx起動の成功を、プロジェクト環境や配布先導入の依存充足の代わりにはしない。

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
aridの検出条件は両側で共有するが、既存負債の受容データはチェック範囲ごとに持つ。
`arid-baseline.json`は受容中の負債が残る直下だけに置き、負債を解消したagent-toolkit側は基準ファイルも参照も持たない。
空のファイルを同期する仕組みは追加せず、設定整合テストも検出条件の同値と受容データの有無を区別する。
`[tool.typos]`はtyposが各ファイルに最も近い設定を自ら読み、語の不足は変更時点のチェックで失敗として現れるため対象から外す。

`subproject-exclude`で分割そのものを外す案は、Python系のチェックがリポジトリ直下の環境で動いてagent-toolkitの依存と設定を使えなくなるため採らない。
個別のキーを見つけるたびに両側へ値を追随させ、そのキーだけを比べるテストを追加する案は、テストの無いキーで同じ事象が続くため採らない。

レビュー限定基準の定義と索引は`agent-basics`の`writing-standards/references/review-only-criteria.md`に置く。品質基準を所有する`agent-basics`へ定義を集約し、工程を扱う`agent-toolkit`の文書はその定義を参照する。toolkitからbasicsへの一方向の依存を保つことで、品質基準の改訂が工程文書との循環参照を生まず、作成とレビューの両方が同じ定義を適用できる。

## テストの実行環境の隔離

pytest-asyncioのfixtureとtestの既定loop scopeはfunctionとし、テストごとの非同期処理の寿命を一致させる。
ブラウザーテストは共有ブラウザーの寿命に合わせてsession scopeを明示する。
session scopeを全テストへ適用する案は、先行テストの遅延処理が後続テストの登録簿へ書く問題を残すため採用しない。
リポジトリ直下、`agent-toolkit/`全体、`agent_toolkit/`と`skills/`の4起動範囲で同じ隔離を確認する。

laneの統合検証と全てのpytest起動は、pyfltrのホスト共有実行APIへ接続する。
pytestのbasetempは実行単位で分離し、controllerとworkerの終了後に正常・失敗・中断のどの場合も回収する。
終了時に回収する子孫からは、同じプロセスのmultiprocessingのresource_trackerを除く。trackerはpytestの終了で自ら終わり、先に停止すると終了時の資源解放がtrackerを再起動し、未解放の資源を報告する警告を出力するためである。
明示basetempとJUnit・traceなどの保存証拠、serveとwatcherはそれぞれの所有主体が必要な寿命まで保持する。
資源所有クラスは文脈マネージャーで獲得直後から解放を保証し、再入可能なcloseと起動途中の所有移譲を扱う。
呼び出し元へ手動closeを分散する案は、例外が発生した場合と複数の後始末が失敗した場合に資源が残るため採用しない。

テストを開発機だけにある状態から切り離す隔離は、`agent-toolkit/agent_toolkit/_testing/isolation.py`の1箇所に定義する。
対象の次元は、ホームと設定ディレクトリ、private-notes、一時ディレクトリ、Gitのglobal・system設定、開発セッションの環境変数、PATH上の開発機専用のエージェントCLI（`codex`・`claude`・`agy`）の6つである。
開発セッションの環境変数には、`update-dotfiles`が子へ渡す実行状態（`UPDATE_DOTFILES_RUN_ID`など）と`AGENT_TOOLKIT_CONFIG_`で始まる設定の上書きを含める。Codex daemonの再起動を経てエージェントのシェルとpytestへ継承され、単独の公開操作を試すテストを更新処理配下の分岐へ入れていた。
ホームと設定ディレクトリは、試験関数ごとの差し替えに加えて、`pytest_plugin`が収集区間だけbasetemp配下へ向ける。収集時にimportされる製品モジュールは`Path.home()`などを定数へ確定し、試験関数ごとの差し替えでは実ホームを指したまま残るためである。セッション全体で差し替える案は、xdistのworkerが祖先から継承するpyfltrの検証枠のロック位置まで変わり、worker同士が互いの解放を待つため採用しない。
リポジトリ直下の`conftest.py`（`pytools/`・`scripts/`）と`agent-toolkit/conftest.py`（`agent_toolkit/`・`skills/`）が同じ定義をautouseで適用する。
pytestは祖先ディレクトリのconftestだけを読むため、起動範囲ごとにconftestを置くと隔離の集合が範囲ごとに異なり、範囲を横断する定義を持たないまま事象ごとに次元を追加する運用になっていた。その結果、開発機で成功しCIだけで失敗する事象が、Git設定、private-notes、PATH上のCLIと次元を変えて繰り返された。
リポジトリ直下から`agent-toolkit/`配下を指定して起動すると2つのconftestが読まれるが、fixture名が同じため近い側の定義だけが適用され、二重に適用されない。

パッケージ内の共通fixtureは自動・明示要求型とも`agent_toolkit._testing.pytest_plugin`が登録し、両`pyproject.toml`のpytest `addopts`から`-p`で読み込む。
pytest 9.1.1では、パッケージ内・親ディレクトリ・パッケージ内の順にファイルを指定するとcollectorが再生成され、conftestに結び付いたfixtureが後半で失われる（[pytest #14997](https://github.com/pytest-dev/pytest/issues/14997)、[修正 #14645](https://github.com/pytest-dev/pytest/pull/14645)）。fixtureの種別によらず配置で結果が変わるため、明示要求型も起動時に登録する。
起動時登録はcollectorの同一性から切り離し、自動fixture本体はテストのファイル位置で従来の適用範囲を守る。
環境変数・警告状態・Codexモデル一覧・終了待機・端末幅・agents_serverの診断ログhandlerの回収の6件は`agent_toolkit/`配下、外部真正性状態の隔離は`_atk/managed_temp/`配下、編集環境の隔離は`_atk/wi/mutations/`配下に限る。
診断ログhandlerの回収は、`configure_logging`の標準エラー向けhandlerが構築時の`sys.stderr`（試験中の取り込み用stream）を保持し、試験ごとに回収を書く方式では回収を持たない呼出し元の試験が残ったため、全ての試験で行う。
自動fixtureを実物へ戻す`real_end_turn_wait`、agents_serverの隔離、計画インデックス、口語検出の入力、Gitリポジトリのfactory、JST固定も同じプラグインへ置く。テスト内の個別差し替えはそのまま使う。
conftest.pyを置けるのはリポジトリ直下と`agent-toolkit/`直下だけとし、後者の登録名は前者にもそろえる。`pytest_fixture_registration_invariant_test.py`が配置と登録の不変条件を確かめる。
修正を含むpytest安定版を採用し、自動・明示要求型ともプラグインの回避を外して同じ収集順と適用範囲・個別差し替えの契約が成立すれば、この登録方式と配置制約の回避を撤去できる。

PATHの隔離は、エージェントCLIを含むディレクトリを、CLI以外の項目へのシンボリックリンクだけを持つ一時ディレクトリへ置き換える。ディレクトリごと外すと同じ場所の`uv`などテストが使うツールまで失われる。置き換えたPATHはセッションで1回だけ組み立てる。
実際のCLIやホームを意図して使うテストは、`host_environ`で子プロセスへ渡す環境変数を組み立てるか、`restore_host_environment`で同じプロセスの値を戻す。

実際のuv、pnpm、corepackを使い、取得物の冷えた状態自体はチェックしない試験は、`share_package_caches`を明示して取得物だけを再利用する。
uvの保存先は隔離前の環境から`uv cache dir`で解決し、`UV_CACHE_DIR`で子プロセスへ渡す。解決は利用時にプロセスで1回だけ行う。
HOME、設定、一時出力、private-notes、managed-tempの状態と仮想環境は試験ごとに隔離し、複数worktreeの仮想環境を共有しない。
公開された起動コマンド、起動形式、導入試験が個別にキャッシュを解決すると、隔離後の空の保存先を選ぶ試験が残るため、解決を共通fixtureへ集約する。
空キャッシュからの導入自体を確かめる試験には共有を強制せず、初回取得と取得済みキャッシュ・新規仮想環境の双方で公開結果を確かめる。

`log_level = "DEBUG"`の取り込みからは、依存ライブラリのロガーを外す。定義は`agent-toolkit/agent_toolkit/_testing/dependency_logging.py`が持ち、2つのconftestが隔離と同じ形で適用する。
`log_level`は本リポジトリのコードのDEBUGログを失敗報告へ残すために置いているが、pytestは全ロガーへ一律に閾値を下げるため、markdown-it-pyのDEBUGレコード（1200行の計画の確認1件で約40万件）の記録にテストの所要の大半を使っていた。
インストール済みの配布物が提供する上位パッケージ名のロガーの閾値をINFOへ上げ、配布物として登録されない本リポジトリのコードはDEBUGの取り込みを残す。
`log_level`を下げる案は本リポジトリのDEBUGログを失い、姉妹プロジェクトとそろえた設定の目的に反するため採らない。ライブラリを名指しで外す案は、今後加わる依存へ働かないため採らない。

`asyncio.create_subprocess_exec`全体を遮断するファイル単位の仕組みを全範囲へ広げる案は、正当に子プロセスを起動する他のテストまで止めるため採らない。CIと同じ条件の環境で追加実行する案は、テスト自身が開発機でも失敗する本方式が成立するため採らない。

## 確かめる対象の近くへのテスト配置

文書・設定の実物を読むテストがPython実装のディレクトリにあると、文書や設定の変更からテストをたどれない。
個々の編集対象とテストの対応表を規範へ追加する案は、新しいテストのたびに対応表を更新する費用を生むため採らない。
配置の定義元を`dotfiles-development`「テスト配置」へまとめ、実物を読むテストはその実物か対象群を包含する最も近いディレクトリへ置く。
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
個別のテスト一覧を設定へ写す案は移動のたびに更新を要し、独立したhookを追加する案は同じテストを別の呼び出しでも起動するため採らない。

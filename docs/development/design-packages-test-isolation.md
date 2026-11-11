# テストの実行環境の隔離の設計記録

[設計記録の索引](design.md)から参照する、テストの実行環境の隔離の記録である。

## テストの実行環境の隔離

pytest-asyncioのfixtureとtestの既定loop scopeはfunctionとし、テストごとの非同期処理の寿命を一致させる。
ブラウザーテストは共有ブラウザーの寿命に合わせてsession scopeを明示する。
session scopeを全テストへ適用する案は、先行テストの遅延処理が後続テストの登録簿へ書く問題を残すため採用しない。
リポジトリ直下、`agent-toolkit/`全体、`agent_toolkit/`と`skills/`の4起動範囲で同じ隔離を確認する。

laneの統合検証と全てのpytest起動は、pyfltrのホスト共有実行APIへ接続する。
pytestのbasetempは実行単位で分離し、controllerとworkerの終了後に正常・失敗・中断のどの場合も回収する。
xdist 3.8の失敗上限では、controllerの停止理由を`_testing/xdist_stop.py`がworkerの`session.shouldstop`へ渡す。
通常完了のキュー排出と早期停止を区別し、lookahead待機中の停止でも未開始の試験を実行しない。
上流[pytest-xdist #1395](https://github.com/pytest-dev/pytest-xdist/pull/1395)と同じ境界へ限定し、強制終了や実行中の試験の中断は行わない。
共通pytest pluginから登録するため、両プロジェクトの直接pytestとpyfltrで同じ適応が働き、worker回収後の既存の実行枠解放へ接続する。
修正を含む安定版へ更新し、`xdist_stop_test.py`の早期停止・正常完了と実行枠の回収を確認した時点で局所適応を撤去する。
pytestが最外側で実行枠を取得した場合は、controllerが開始からworker・子孫の回収まで`execution_slot.hold`を保持する。
入れ子の実行とworkerは祖先の所有を継承する。
2026年11月に、試験の区切りで譲る処理と参加券・完了印・譲渡予算を撤去した。
先に終了したworkerやidle fork-serverの扱い、同じプロセスのMCP呼出しで、譲渡の状態と実処理の到達が一致せず停止した記録がある。
全寿命の排他へ戻すことで、別の検証との重なりを防ぐ目的を保ち、区切りの停止と再開だけの状態を減らす。
各worktreeへ別キャッシュを割り当てて枠を分ける案と、dotfiles側で独自のロックや待機台帳を持つ案は、ホスト共有資源の保護を失うか所有管理を二重にするため採らない。
終了時に回収する子孫からは、同じプロセスのmultiprocessingのresource_trackerを除く。
trackerはpytestの終了で自ら終わり、先に停止すると終了時の資源解放がtrackerを再起動し、未解放の資源を報告する警告を出力するためである。
明示basetempとJUnit・traceなどの保存証拠、serveとwatcherはそれぞれの所有主体が必要な寿命まで保持する。
資源所有クラスは文脈マネージャーで獲得直後から解放を保証し、再入可能なcloseと起動途中の所有移譲を扱う。
呼び出し元へ手動closeを分散する案は、例外が発生した場合と複数の後始末が失敗した場合に資源が残るため採用しない。

テストを開発機だけにある状態から切り離す隔離は、`agent-toolkit/agent_toolkit/_testing/isolation.py`の1箇所に定義する。
対象の次元は、ホームと設定ディレクトリ、private-notes、一時ディレクトリ、Gitのglobal・system設定、開発セッションの環境変数、PATH上の開発機専用のエージェントCLI（`codex`・`claude`・`agy`）の6つである。
開発セッションの環境変数には、`update-dotfiles`が子へ渡す実行状態（`UPDATE_DOTFILES_RUN_ID`など）と`AGENT_TOOLKIT_CONFIG_`で始まる設定の上書きを含める。
Codex daemonの再起動を経てエージェントのシェルとpytestへ継承され、単独の公開操作を試すテストを更新処理配下の分岐へ入れていた。
ホームと設定ディレクトリは、試験関数ごとの差し替えに加えて、`pytest_plugin`が収集区間だけbasetemp配下へ向ける。
収集時にimportされる製品モジュールは`Path.home()`などを定数へ確定し、試験関数ごとの差し替えでは実ホームを指したまま残るためである。
セッション全体で差し替える案は、xdistのworkerが祖先から継承するpyfltrの検証枠のロック位置まで変わり、worker同士が互いの解放を待つため採用しない。
リポジトリ直下の`conftest.py`（`pytools/`・`scripts/`）と`agent-toolkit/conftest.py`（`agent_toolkit/`・`skills/`）が同じ定義をautouseで適用する。
pytestは祖先ディレクトリのconftestだけを読むため、起動範囲ごとにconftestを置くと隔離の集合が範囲ごとに異なり、範囲を横断する定義を持たないまま事象ごとに次元を追加する運用になっていた。
その結果、開発機で成功しCIだけで失敗する事象が、Git設定、private-notes、PATH上のCLIと次元を変えて繰り返された。
リポジトリ直下から`agent-toolkit/`配下を指定して起動すると2つのconftestが読まれるが、fixture名が同じため近い側の定義だけが適用され、二重に適用されない。

パッケージ内の共通fixtureは自動・明示要求型とも`agent_toolkit._testing.pytest_plugin`が登録し、両`pyproject.toml`のpytest `addopts`から`-p`で読み込む。
pytest 9.1.1では、パッケージ内・親ディレクトリ・パッケージ内の順にファイルを指定するとcollectorが再生成され、conftestに結び付いたfixtureが後半で失われる（[pytest #14997](https://github.com/pytest-dev/pytest/issues/14997)、[修正 #14645](https://github.com/pytest-dev/pytest/pull/14645)）。
fixtureの種別によらず配置で結果が変わるため、明示要求型も起動時に登録する。
起動時登録はcollectorの同一性から切り離し、自動fixture本体はテストのファイル位置で従来の適用範囲を守る。
環境変数・警告状態・Codexモデル一覧・終了待機・端末幅・agents_serverの診断ログhandlerの回収の6件は`agent_toolkit/`配下、外部真正性状態の隔離は`_atk/managed_temp/`配下、編集環境の隔離は`_atk/wi/mutations/`配下に限る。
診断ログhandlerの回収は、`configure_logging`の標準エラー向けhandlerが構築時の`sys.stderr`（試験中の取り込み用stream）を保持し、試験ごとに回収を書く方式では回収を持たない呼出し元の試験が残ったため、全ての試験で行う。
自動fixtureを実物へ戻す`real_end_turn_wait`、agents_serverの隔離、計画インデックス、口語検出の入力、Gitリポジトリのfactory、JST固定も同じプラグインへ置く。
テスト内の個別差し替えはそのまま使う。
conftest.pyを置けるのはリポジトリ直下と`agent-toolkit/`直下だけとし、後者の登録名は前者にもそろえる。
`pytest_fixture_registration_invariant_test.py`が配置と登録の不変条件を確かめる。
修正を含むpytest安定版を採用し、自動・明示要求型ともプラグインの回避を外して同じ収集順と適用範囲・個別差し替えの契約が成立すれば、この登録方式と配置制約の回避を撤去できる。

PATHの隔離は、エージェントCLIを含むディレクトリを、CLI以外の項目へのシンボリックリンクだけを持つ一時ディレクトリへ置き換える。
ディレクトリごと外すと同じ場所の`uv`などテストが使うツールまで失われる。
置き換えたPATHはセッションで1回だけ組み立てる。
実際のCLIやホームを意図して使うテストは、`host_environ`で子プロセスへ渡す環境変数を組み立てるか、`restore_host_environment`で同じプロセスの値を戻す。

pnpm 12以降の導入・版管理は`agent-basics/skills/writing-standards/references/dependency-management.md`「pnpm」に従う。
初回導入はnpmまたはstandaloneで行い、版固定と検証にはlockfile先頭のboot文書のintegrityとレジストリ署名、公開待機には`minimumReleaseAge`を使う。
更新には`pnpm self-update <版>`を使い、pnpm自身が検証しない`packageManager`のCorepack用ハッシュへ依存しない。
Node.js TSCの[投票](https://github.com/nodejs/TSC/pull/1697)は25系以降のCorepack同梱終了を決めたもので、非推奨化の決定ではない。
[議論](https://github.com/nodejs/TSC/pull/1527)ではNode.jsのリリースを要する保守負担と修正の配布遅延が指摘された。
Corepackを別途導入して残す案は、pnpm 12自身で版固定・検証・公開待機を満たせるため採らない。
pnpm 10以前のCorepack手順と、Corepackを実行し得る試験の環境変数・キャッシュの隔離は維持する。

実際のuv、pnpm、corepackを使い、取得物の冷えた状態自体はチェックしない試験は、`share_package_caches`を明示して取得物だけを再利用する。
uvの保存先は隔離前の環境から`uv cache dir`で解決し、`UV_CACHE_DIR`で子プロセスへ渡す。
解決は利用時にプロセスで1回だけ行う。
HOME、設定、一時出力、private-notes、managed-tempの状態と仮想環境は試験ごとに隔離し、複数worktreeの仮想環境を共有しない。
公開された起動コマンド、起動形式、導入試験が個別にキャッシュを解決すると、隔離後の空の保存先を選ぶ試験が残るため、解決を共通fixtureへ集約する。
空キャッシュからの導入自体を確かめる試験には共有を強制せず、初回取得と取得済みキャッシュ・新規仮想環境の双方で公開結果を確かめる。

`log_level = "DEBUG"`の取り込みからは、依存ライブラリのロガーを外す。
定義は`agent-toolkit/agent_toolkit/_testing/dependency_logging.py`が持ち、2つのconftestが隔離と同じ形で適用する。
`log_level`は本リポジトリのコードのDEBUGログを失敗報告へ残すために置いているが、pytestは全ロガーへ一律に閾値を下げるため、markdown-it-pyのDEBUGレコード（1200行の計画の確認1件で約40万件）の記録にテストの所要の大半を使っていた。
インストール済みの配布物が提供する上位パッケージ名のロガーの閾値をINFOへ上げ、配布物として登録されない本リポジトリのコードはDEBUGの取り込みを残す。
`log_level`を下げる案は本リポジトリのDEBUGログを失い、姉妹プロジェクトとそろえた設定の目的に反するため採らない。
ライブラリを名指しで外す案は、今後加わる依存へ働かないため採らない。

`asyncio.create_subprocess_exec`全体を遮断するファイル単位の仕組みを全範囲へ広げる案は、正当に子プロセスを起動する他のテストまで止めるため採らない。
CIと同じ条件の環境で追加実行する案は、テスト自身が開発機でも失敗する本方式が成立するため採らない。

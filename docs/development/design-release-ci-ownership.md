# CIの実処理所有権の設計記録

[設計記録の索引](design.md)と[branch・CI所有権・公開・終了の設計記録](design-release.md)から参照する主題別の記録である。

## developとmasterのbranch・リリース設計

### CIの実処理所有権

共通CIは`push`と`master`向け`pull_request`の全イベントでjobを開始し、job表示名とmatrixを評価する。
共通jobの所有・非所有の切り替えはstep-level条件で行い、job表示名をどちらの経路でも評価する。
結果集約と領域選択のjobには`!cancelled()`を置き、先行jobの失敗・出力欠落を必須checkの失敗として残す。

`test-linux`と`update-dotfiles-upgrade (windows)`は全履歴を取得し、現行HEADの時刻から約72時間前の祖先を選ぶ。
管理一時ディレクトリ内へローカルbare remote、旧版checkoutおよび隔離HOMEを構成し、旧版をchezmoiで適用する。
その後、同じbranchのremote refだけを現行HEADへ進め、旧版checkout内の`bin/update-dotfiles`または
`bin/update-dotfiles.cmd`を起動する。
公開ランチャーの終了コードが0で、更新後checkoutの`git rev-parse --short=7 HEAD`が
検証開始時の現行HEADと一致した場合だけ成功とする。
実行中のOSアカウントのHOMEと外部remoteは変更対象にしない。

| イベント | head repository | head branch | base branch | 共通8 jobの実処理所有者 | 表示名 |
| --- | --- | --- | --- | --- | --- |
| `push` | base repository | `master` | 該当なし | `master`の`push` run | 8件のrequired check名 |
| `push` | base repository | `develop`、同一SHAの利用可能なmaster runあり | 該当なし | `master`の`push` run | 8件の`(non-owner)`名 |
| `push` | base repository | その他 | 該当なし | この`push` run | 8件のrequired check名 |
| `pull_request` | base repository | `develop` | `master` | `develop`の`push` run | 8件の`(non-owner)`名 |
| `pull_request` | base repository | `develop`以外 | `master` | `pull_request` run | 8件のrequired check名 |
| `pull_request` | base repository以外 | 任意 | `master` | `pull_request` run | 8件のrequired check名 |

先行job`common-ci-owner`が所有判定を1回だけ行い、全共通jobの表示名、非所有marker、後続stepの条件へ同じ出力を渡す。
同一repositoryのheadが`develop`、baseが`master`のrelease pull requestは、`develop`のpushを所有者とする。
`develop`へのpushでは、`actions: read`を持つ先行jobが同一repository・同一workflowのmaster push runを完全SHAで照会する。
実行待ち・実行中または成功済みのrunがあれば、そのmaster runを所有者とする。
該当runがない場合、失敗・取消済みのrunだけの場合、API照会が失敗した場合は、このdevelop runが共通検証を実行する。
`master`へのpushは常に所有し、Release起点を維持する。
その他のbranchのpush、release PR以外の同一repository PR、fork PRも所有する。
非所有時はmarkerで理由を表示し、依存導入と実検査を省く。
所有判定・領域判定・結果集約に使う標準Pythonとshellのスクリプトは、checkoutで取得してから実行する。
markerはrelease PRではdevelop branch、同期pushでは所有master runのURLを確認先として示す。
非所有markerはcheckout前から存在する`${{ github.workspace }}`を作業場所とし、Windowsの共通jobは`pwsh`、その他の共通jobは`bash`を明示する。
`rust-lint`の作業場所は実検査のcomposite action内で指定し、判定とmarkerはworkspace rootで動く。
共通jobは短い先行判定だけを待つ。
Windowsの2ジョブは互いを待たず、`statusline-version`は所有判定から独立する。
concurrencyはeventとrefを分け、master runとdevelop runを相互取消しの対象にしない。
実行中のmaster runが後に失敗した場合は、その失敗結果が残る。
非所有側の別名checkはrequired checkの成功を代替しない。

job-level条件を使うと条件が偽のjobがmatrix展開前にskipされ、job名式が評価されないため、非所有時の8件の表示名を保証できない。
共通jobを開始して非所有markerを成功させる構成により、required check名と異なる表示名を生成し、同名のskip-successで所有runを代替しない。
pull requestの`GITHUB_SHA`はrunnerがcheckoutするtest merge commitを示すが、check runの`head_sha`はstatusを関連付けるpull request head commitを示すため、両者を同一視しない。
`master`が`develop`の祖先であり、release merge commitのtreeが`develop` headのtreeと同一になるrelease invariantを、共通CIの実処理を`develop`の`push` runへ帰属させる根拠とする。

実ブラウザーE2Eは、共通`python-lint`から分離した`browser-e2e` jobが所有する。
同jobはPython 3.15でPlaywright Chromiumを導入し、`agent-toolkit/agent_toolkit/_atk/serve/`配下の`browser`マーカーを持つ実ブラウザーテストだけを実行する。
分離により、`python-lint (3.15)`からChromiumの導入とE2Eの実行時間を外したうえで、最新のPythonでのE2E実行を維持する。
`browser-e2e`は他の共通jobと同じ非所有markerの構成を採用し、required checkへ加えることでE2Eの失敗がマージを遮断する状態を保つ。

Windowsの旧版更新検証は`update-dotfiles-upgrade (windows)`が所有し、`test-windows`はWindows固有pytest、公開ランチャー、通常profileへのchezmoi適用とpost-applyの確認を所有する。
両job間に依存関係を置かず、更新検証の通常profile監視に必要なMozilla予約タスク停止は更新検証jobが実行する。

検査の実行順と表示名はworkflowが所有し、実処理は`.github/actions/ci-*`、共通準備は`prepare-install-test`・`prepare-python-check`が所有する。
所有判定は`scripts/ci_owner.py`、変更領域の分類は`scripts/ci_scope.py`、領域出力の検証は`scripts/ci_check_scope.sh`に集約する。
Rustとブラウザーは領域出力を検証した後、選択された場合だけ実検査のcomposite actionを呼ぶ。
Windowsの配置・Mozilla予約タスク停止・固定版Codex導入・境界pytestの結果ログ・配布確認は`scripts/ci_*.ps1`、Linuxの配置と配布確認は`scripts/ci_*.sh`へ置く。
`prepare-install-test`はcheckout上のローカルactionでchezmoiを導入してから、OS固有の配置スクリプトでHOME配下へコピーし、コピー先でuvを準備する。
GitHub Actionsのpost工程もworkspace上のローカルaction定義を読み直すため、元のcheckoutはjob終了まで保持する。

Python系は`python-lint`、`pylint`、`pytest-root`、`pytest-toolkit`の責務別jobとする。
表示名はそれぞれ`python-lint-other (3.15)`、`pylint (3.15)`、`pytest (3.15)`、`pytest-toolkit (3.15, N/3)`である。
matrixはtoolkitの3分割だけに使い、`fail-fast: false`で全分割の結果を取得する。
Pylintは既存の直列設定のまま独立実行し、その他のlintから除外する。
直下はGit管理下のテストを明示してpytestへ渡し、toolkitは同サブプロジェクトを起点にskills配下を含めて実行する。
toolkitの各分割jobは同じ集合を収集し、`agent_toolkit._testing.pytest_plugin`の`--test-shard`が所要降順・同値node ID昇順で、合計所要が最小の分割へ各試験をちょうど1回割り当てる。
同値の分割は番号順とする。
所要は同一commitの`_testing/test_durations.json`を読み、記録外は記録の平均、記録無しは固定重み1で件数を均衡させる。
記録は成功runの全3shardのJUnit XMLを`scripts/update_test_durations.py`へ渡して生成し、1秒以上の試験だけ保存する。
公開担当が偏りを観測したときに更新する。
新しい外部plugin、CI中の前run取得、分割数増加は採らない。
1runnerのtoolkit pytestが約16分を要したため3分割し、通常pytestは最低Git 2.34.1、4worker、workstealと環境隔離を維持する。
Python版は3.15とし、cache keyにPython版・検査種別・分割番号・runと試行の識別子を含める。
ネイティブ依存・chezmoi・キャッシュ・uv syncは`prepare-python-check`、最低Gitの導入は`minimum-git`が共有する。

集約jobは`pytest-toolkit-result`と`python-lint-result`を分け、必須checkの`pytest-toolkit (3.15)`と`python-lint (3.15)`を所有する。
toolkitは同一検査だけのmatrix全体、lintはPylintとその他のlintの両jobの`needs`結果を`scripts/ci_result.py`で検証する。
必要な依存jobがすべて成功した場合だけ成功とし、失敗・取消・skip・所有出力欠落を失敗として残す。
非所有runは他の共通jobと同じ`(non-owner)`名と理由を表示する。
job表示名から検査を再発見するAPI照会を省き、依存関係を集約対象の定義元とする。

statuslineのCargo versionとbase・head versionおよびtagの確認は、共通`rust-lint`から分離した`statusline-version` jobが所有する。
`statusline-version`は`pull_request`かつbaseが`master`の全pull requestと、`develop`へのpushで実行し、head repository、head branchおよびrelease条件を追加の限定に使わない。
比較基点はpull request起点では`github.event.pull_request.base.sha`、push起点では`git fetch --no-tags origin master`の後の`git merge-base`が返す`master`との共通祖先とする。
push起点を加えるのは、版数更新の抜けをrelease pull requestの必須check一式が実行される前に検出するためである。
判定は`scripts/check_statusline_version.py`へ集約し、CIの同jobとpyfltrのcustom-command`statusline-version`の双方から呼ぶ。
レーン担当は版数更新の根拠を渡し、公開担当が版数を更新する。
pyfltr側は`pyproject.toml`で無効にしておき、公開担当が公開前のローカル検証で明示的に有効化する。
比較先は作業ツリーとしてcommit前の変更も判定し、push前に版数の更新忘れを見つける。
pre-commitの`pyfltr fast`で版数更新前の途中commitを遮断しないよう非fastとし、浅いcheckoutの`python-lint` jobでは無効化する。
同一repositoryのreleaseおよびnon-release pull requestとfork pull requestが同じ検証対象となり、`rust-lint`というrequired名の重複を生成しない。

agent-toolkitとagent-basicsの版数据置きは`plugin-version` jobが検出し、判定は`scripts/check_plugin_version.py`へ集約してpyfltrのcustom-command`plugin-version`からも呼ぶ。
両プラグインは`develop`へのpushごとに稼働環境へ導入され、Codexの導入処理は版数の不一致だけで再導入するため、比較基点はpush起点の`github.event.before`（ローカルではupstreamの最新の公開済みcommit）とし、`master`との合流点を使わない。
`*_test.py`だけの差分を除き、コメント・文書を含むプラグインroot配下の差分を版数更新の対象とする。
差分の意味（挙動の変化の有無）で対象を絞る案は、文書だけの変更も配布物として稼働環境へ届ける必要があり、機械判定で区別できないため採らない。
全0のOID、解決できないOID、upstreamの不在は成功扱いにせず終了2とする。
2026年10月31日にhookの不具合修正が版数を据え置いたまま公開され、稼働中のCodexへ届かなかったため加えた。
pyfltr側は`statusline-version`と同じく既定で無効、非fastとし、浅いcheckoutの`python-lint` jobでは無効化する。
required checkへは加えず、`develop`へのpushで失敗を可視化する。

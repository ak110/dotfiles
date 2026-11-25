# CIの実処理所有権の設計記録

[設計記録の索引](design.md)

## CIの実処理所有権

共通CIは`push`と`master`向け`pull_request`の全イベントでjobを開始し、job表示名とmatrixを評価する。
step-level条件で共通jobの所有を切り替え、所有・非所有の両方でjob表示名を評価する。
結果集約と領域選択のjobには`!cancelled()`を置き、先行jobの失敗・出力欠落を必須checkの失敗として残す。

旧版からの更新検証（`test-linux`と`update-dotfiles-upgrade (windows)`）で旧版の初期適用が失敗した場合は、同じ検証homeで同じ旧版を1回だけ再適用する。
旧版の後処理には並行起動の競合で不定期に失敗し、再実行で回復する版があり、旧版のcommitは固定のため現行版の変更では直らないからである。
再適用でも失敗した場合は、旧版の状態が無いまま更新が通っても旧版からの更新を確かめたことにならないため、更新を実行せずに失敗とする。
実行中のOSアカウントのHOMEと外部remoteは変更対象にしない。

| イベント | head repository | head branch | base branch | 共通8 jobの実処理所有者 | 表示名 |
| --- | --- | --- | --- | --- | --- |
| `push` | base repository | `master`、同一treeのdevelop CIを再利用可能 | 該当なし | `develop`の`push` run | 8件の`(non-owner)`名 |
| `push` | base repository | `master`、その他 | 該当なし | `master`の`push` run | 8件のrequired check名 |
| `push` | base repository | `develop`、同一SHAの利用可能なmaster runあり | 該当なし | `master`の`push` run | 8件の`(non-owner)`名 |
| `push` | base repository | その他 | 該当なし | この`push` run | 8件のrequired check名 |
| `pull_request` | base repository | `develop` | `master` | `develop`の`push` run | 8件の`(non-owner)`名 |
| `pull_request` | base repository | `develop`以外 | `master` | `pull_request` run | 8件のrequired check名 |
| `pull_request` | base repository以外 | 任意 | `master` | `pull_request` run | 8件のrequired check名 |

先行job`common-ci-owner`が所有判定を1回だけ行い、全共通jobの表示名、非所有marker、後続stepの条件へ同じ出力を渡す。
masterのpushでは2親のmergeで、第一親が第二親の祖先、mergeと第二親のtreeが同一の場合だけ、第二親のdevelop pushを照会する。
同じリポジトリの正規CIが成功し、Rust・ブラウザーのstepと分割を含む全jobが同じattemptで実行され、成功した時だけ再利用する。
欠落や結果の不一致、APIから取得できない時はmasterで実行する。
`develop`へのpushでは、`actions: read`を持つ先行jobが同一repository・同一workflowのmaster push runを完全SHAで照会する。
実行待ち・実行中または成功済みのrunがあれば、そのmaster runを所有者とする。
該当runがない場合、失敗・取消済みのrunだけの場合、API照会が失敗した場合は、このdevelop runが共通検証を実行する。
`master`へのpushはReleaseの起点として維持する。
concurrencyはeventとrefを分け、master runとdevelop runを相互取消しの対象にしない。
実行中のmaster runが後に失敗した場合は、その失敗結果が残る。
非所有側の別名checkはrequired checkの成功を代替しない。

job-level条件を使うと条件が偽のjobがmatrix展開前にskipされ、job名式が評価されないため、非所有時の8件の表示名を保証できない。
共通jobを開始して非所有markerを成功させる構成により、required check名と異なる表示名を生成し、同名のskip-successで所有runを代替しない。
pull requestの`GITHUB_SHA`はrunnerがcheckoutするtest merge commitを示すが、check runの`head_sha`はstatusを関連付けるpull request head commitを示すため、両者を同一視しない。
`master`が`develop`の祖先であり、release merge commitのtreeが`develop` headのtreeと同一になるrelease invariantを、共通CIの実処理を`develop`の`push` runへ帰属させる根拠とする。

実ブラウザーE2Eは、共通`python-lint`から分離した`browser-e2e` jobが所有する。
分離により、`python-lint (3.15)`からChromiumの導入とE2Eの実行時間を外したうえで、最新のPythonでのE2E実行を維持する。
`browser-e2e`は他の共通jobと同じ非所有markerの構成を採用し、required checkへ加えることでE2Eの失敗がマージを遮断する状態を保つ。

Windowsの旧版更新検証は`update-dotfiles-upgrade (windows)`が所有する。
Windows固有pytest、Codex統合、公開ランチャーは`test-windows (tests)`、通常profileへの初回導入（chezmoi適用とpost-apply）の確認は`test-windows (install)`が所有する。
3つのjobの間に依存関係を置かず、各jobが自身のrunnerで準備し、更新検証の通常profile監視に必要なMozilla予約タスク停止は更新検証jobが実行する。
境界テストと初回導入は互いの実行順序や同じユーザープロファイルへの書き込みを必要としないため、別jobにして待ち時間を加算しない。
必須checkの`test-windows`は`test-windows (tests)`と`test-windows (install)`を待ち、両方の成功だけを成功として集約する。

GitHub Actionsのpost工程もworkspace上のローカルaction定義を読み直すため、元のcheckoutはjob終了まで保持する。

toolkitの検証matrixは`fail-fast: false`で全分割の結果を取得する。
toolkitの各分割jobは同じ集合を収集し、`agent_toolkit._testing.pytest_plugin`の`--test-shard`が所要降順・同値node ID昇順で、合計所要が最小の分割へ各テストをちょうど1回割り当てる。
新しい外部plugin、CI中の前run取得、分割数増加は採らない。
1runnerのtoolkit pytestが約16分を要したため3分割し、通常pytestは最低Git 2.34.1、4worker、workstealと環境隔離を維持する。

Pylintも所有時だけmatrixを展開し、ファイル単位の診断を重複しない2範囲で、循環importを全対象で並行して検査する。
非所有時は単一の表示名で実処理を行わず、既存の`python-lint-result`は所有時の全分割の結果を待つ。

集約jobは`pytest-toolkit-result`と`python-lint-result`を分け、必須checkの`pytest-toolkit (3.15)`と`python-lint (3.15)`を所有する。
必要な依存jobがすべて成功した場合だけ成功とし、失敗・取消・skip・所有出力欠落を失敗として残す。
同じrunの結果集約はAPIを照会せず、依存関係を集約対象の定義元とする。

statuslineのCargo versionとbase・head versionおよびtagの確認は、共通`rust-lint`から分離した`statusline-version` jobが所有する。
`statusline-version`は`pull_request`かつbaseが`master`の全pull requestと、`develop`へのpushで実行し、head repository、head branchおよびrelease条件を追加の限定に使わない。
比較基点はpull request起点では`github.event.pull_request.base.sha`、push起点では`git fetch --no-tags origin master`の後の`git merge-base`が返す`master`との共通祖先とする。
push起点を加えるのは、版数更新の抜けをrelease pull requestの必須check一式が実行される前に検出するためである。
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
pyfltr側は`statusline-version`と同じく通常の実行とfastの対象から外し、浅いcheckoutの`python-lint` jobでは無効にする。
required checkへは加えず、`develop`へのpushで失敗を可視化する。

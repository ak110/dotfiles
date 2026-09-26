---
name: ak110-projects-operations
description: >
  作者個人のプロジェクト群の運用を扱う。姉妹プロジェクト間のツールチェイン（Makefile、mise、prek、
  GitHub Actionsなど）・ドキュメント構成の同期、gv・lcなど姉妹プロジェクト自体の編集、推奨ガイド・
  共有ファイルへの追従、個人プロジェクトのAWI処理への着手、リリース、lint設定・足回りファイルの
  変更時に使う。`/ak110-projects-operations`、「他プロジェクトへの反映」
  「プロジェクト間の同期」などのキーワードで自動トリガーしてよい。プロジェクト固有のアプリケーションロジック変更は対象外
---

# 作者個人のプロジェクト運用

## 前提

姉妹プロジェクト群は、共通化対象のツールチェインを同じコマンド名・設定キーで揃える。
ドキュメントの章構成は「ドキュメント章構成の統一」節が定める。
意図的に維持している差異に該当しないコメント表現・項目順・既定値は、同一文面または同等の設定へ揃える。
1プロジェクトで変更した場合、後述のマトリクスに基づいて他プロジェクトへの波及要否を確認し、必要ならユーザーへ提案する。

対象プロジェクトの一覧と絶対パスはセッション開始時にコンテキストへロードされるローカル指示から得る。
個別プロジェクトのパスはそのコンテキスト経由で参照する。

対象リポジトリが本スキルの対象に該当するかは、そのリポジトリの絶対パスが前段のプロジェクト一覧に現れるかで判定する。
一覧に現れない対象は本スキルの対象外とする。

明らかにプロジェクト固有の変更や、「ツールチェインやドキュメント構成など」以外の変更であれば確認不要。

## 判定手順

リリース専用依頼でも本スキルの起動は維持する。現在の会話コンテキストと自身が実行した操作から、
次の条件を両方確認できる場合は、
姉妹プロジェクトへの波及調査を開始せず、受領した作業だけを実施する。

- 既存commitのPR作成・マージ・CI検収だけを行う
- 現行セッションで同期対象を変更・計画・レビューしていない

いずれかを確認できない場合は、以下を実施する。

- 変更内容の分類を特定する（例: prek設定、mise設定、CI workflow、README構成など）
- 「変更時の同期対象マトリクス」で波及プロジェクトを決める
- 「意図的に維持している差異」に該当しないか確認する
- 移植対象の記述が依拠する前提（配布形態・外部サービスの有効状態など）を列挙し、
  各前提が移植先で成立することを、移植先の現物を観測して確かめるか、移植先の明文化された方針と比べて確認する。
  成立しない前提に依拠する記述は移植先の実情に合わせて書き換えるか、移植対象から除く
- 該当プロジェクトに対して`agents_server`の`start_explore`で読み取り専用の並列調査を行い、差分を把握する
  （対象が複数リポジトリへまたがるため、起動元のコンテキストで全文を読むと後続の判断へ回せる容量が残らない）
- 同期が必要なプロジェクトと推奨アクションをユーザーに報告する

スコープに含むのは「ツールチェインやドキュメント構成」の範囲。
典型パスは以下。

- ビルド/タスク: `Makefile` / `mise.toml`
- Python設定: `pyproject.toml`
- Node.js設定: `package.json`
- lint/format: `.pre-commit-config.yaml`
- CI: `.github/workflows/**`
- ドキュメント: `README.md` / `AGENTS.md` / `docs/**/development.md` / `docs/**/security.md`

## 追従作業と複数リポジトリ横断投入

推奨ガイド（`~/pyfltr/docs/guide/recommended.md`・`recommended-nonpython.md`）または
姉妹プロジェクト共有ファイルへの追従作業では、着手前に次の2点を確認する。

- 同期対象マトリクスで波及先とした各プロジェクト宛の未処理AWIを`atk wi list`と`atk wi grep`で照会し、実行レビューで確定した
  検証結果・訂正記録が記録されていないかを確認する。記録があった場合はその結論を変更の前提として取り込む
- 推奨ガイドが新設した設定へ追随する場合、追随先が実際に取得する配布物の公開版にその設定が
  含まれることを、公開版を明示指定した実行で確認する

追従を目的とする計画では、本計画は追従元の現行内容への追従に限定して追従元の設計自体の改善は対象外とする旨と、
追従元の改善が必要と判明した場合は追従元リポジトリ宛のAWIとして登録する旨を計画本文へ明記する。

全波及先へ同じ設定値を配布する場合は、その設定を制御するツールを本プロジェクト群が所有しているか確認する。
所有している場合は、上流ツールの既定値または新設オプションへ吸収する案を、各リポジトリへ配布する案と同じ階層で比較する。
比較では次を確認する。

- 上流の公開既定を変更した場合の影響
- 波及先に固有の要件が存在するか
- 新設オプションの既定動作によって個別設定が不要になるか

上流吸収の十分条件は前記3点の比較結果とし、設定値の同一性だけでは満たさない。
比較の結果として上流吸収を選ぶ場合は、`agent-toolkit:wi-standards`で上流リポジトリ宛のAWIを1件投入する。
未投入の配布提案は取り下げ、適用済みの個別設定は別の削除対象として扱う。

推奨ガイドまたは同期対象マトリクスが対象とするファイル群を更新した場合、
同一セッション内で実行主体が`agent-toolkit:wi-standards`をSkill機能で起動し、
他プロジェクト向けの追従提案を各リポジトリのAWIとして投入する。
複数リポジトリでは`agent-toolkit:wi-standards`の`references/cross-repository-submission.md`に従う。

- 追従提案の本文には適用すべき変更内容を対象リポジトリ単独で実施できる粒度で転記し、
  更新元のリポジトリ名とコミットを関連情報として併記する
- 既に別計画で同内容の改訂を扱うことが判明しているリポジトリは投入対象から除き、その旨を各提案の関連欄へ記す
- 推奨ガイドの改訂を要する変更を`~/pyfltr`以外のリポジトリで確定した場合は、先に`~/pyfltr`向けのAWIを
  投入し、他プロジェクト向け追従提案の`depends_on`へそのファイル名を記録する。
  典拠のないままの追従着手による整合の崩れを防ぐためである

## 個人プロジェクト着手時の依存更新

利用者からの要求又は採用済みのAWIによる明示的な更新要求が一括更新コマンドの生成範囲に属する場合は、
ロックファイルの最終コミット日時にかかわらず、対象プロジェクトの一括更新コマンドを1回実行する。
一括更新コマンドは、各プロジェクトが正式に定義したコマンドを使う。`Makefile`が`update`ターゲットを持つ`dotfiles`・`pyfltr`・`pytilpack`・`smpr`・`glatasks`では`make update`、`Makefile`を持たず`mise.toml`が`[tasks.update]`を持つ`gv`・`lc`では`mise run update`を使う。
実行後はコマンドが生成した差分全体を検収し、同一のコミットへ含める。

明示的な更新要求がない個人プロジェクトのAWI処理では、一括更新コマンドの実処理が更新対象とする
ロックファイルだけを、更新要否の判定に使う。対象プロジェクトの開発手順を定めるスキルが
一括更新コマンドの対象外ファイルを明示する場合は、判定前にそのスキルを起動して除外を適用する。
判定対象のロックファイルの最終コミット日時（`git log -1 --format=%cI -- <ロックファイル>`）が
1日以上前（目安）であれば、実装着手時に一括更新コマンドを1回実行して依存を最新化する。
1日未満なら実行しない。
`agent-toolkit:plan-mode`を使う場合は更新要否を計画へ記載し、計画承認後、計画対象の編集前に実行する。
計画前の診断専用実行、隔離worktree、差分の退避、同じ依存更新の再実行は追加せず、この1回の実行で完了とする。

一括更新コマンドが依存更新後の全体検証を連鎖し、前景実行の時間上限内に完了しない場合は、
依存更新部分と全体検証を別の前景実行へ分ける。この分割は同一の依存更新の継続であり、
同じ依存更新の再実行には当たらない。依存更新を委譲する場合は、分割条件と同一更新の継続であることを依頼文へ含める。

依存更新後の検証が失敗した場合、配布物の版指定（`pyproject.toml`の`dependencies`等）が破壊を生じる版へ
到達しうるか否かで扱いを分ける。到達しうる場合は利用者環境で成立している欠陥として同一セッション内で是正し、
開発環境のロックファイル内に閉じる場合は更新を巻き戻して独立したAWIとして登録してよい。

`agent-toolkit:process-wi`でレーンへ依存更新を委ねる場合は、レーンの起動文の固有指示へ、依存更新後の検証が失敗した場合の前段の扱いと「足回りファイルの推奨設定維持」の適用を含める。
本スキルを起動するのはメインだけであり、要否の判定結果だけを渡すと、レーンは失敗を据え置く判断や推奨設定の緩和を本スキルの規定と比べずに選ぶ。

破壊的変更の波及判定では、上限の記載があっても破壊を生じる版がその範囲に含まれる場合は波及すると判定する。
Cargoの既定のキャレット要件のように上限が常に存在する記法があるため、上限の有無そのものを判定基準に含めない。

## リリース運用

`gv`・`lc`・`glatasks`・`pyfltr`・`pytilpack`のリリースは、ユーザーの恒常的な認可に基づき、エージェントが要否とバージョン区分を判断して実施する。
この認可は`agent-toolkit:process-wi`の手動起動と`atk wi process-loop`による自動常駐起動のどちらにも適用し、リリースのたびのユーザー確認とUWIは省く。
リリースworkflowを持たない`dotfiles`（`develop`から`master`へのマージは`dotfiles-release`が扱う）と`smpr`は対象外とし、リリースworkflowを持ったプロジェクトは対象へ加える。

### 実施時機と前提

一連の作業の公開（pushとCI成功）が終わった時点でリリース要否を判定する。
`agent-toolkit:process-wi`では、公開工程の終端担当が返却し、`agent-toolkit:commit`の`references/push-and-ci.md`「公開状態の4項目」の成立を確認した後、`agent-toolkit:completion-report`の起動前に判定する。
process-wi以外の作業でも、作業の変更をpushしてCI成功を確認した時点で同じく判定する。

ローカルのベースbranchが既定branchであり、未pushのcommitと未コミットの変更が無いことを実施の前提とする。
前提が成立しない場合はリリースせず、その理由を完了報告へ含める。`releaser`は未pushのcommitをpushするため、この前提でpushの所有者を保つ。

### 判定

判定対象は引数なしの`releaser`が表示する`<直近のリリースタグ>..HEAD`の未リリースcommit全体とする。各commitは差分の内容で判定し、commit typeは補助の手掛かりに留める。

エンドユーザー影響がある変更は、利用者がリリースされた配布物を通じて観測する挙動や内容を変える変更である。
配布されるコード（CLI・公開API・設定の既定値・画面を含む）の変更と、配布物に同梱されて公開される利用者向け文書（PyPIの説明になるREADMEなど）の変更が該当する。
実行時依存の版指定の更新（`pyproject.toml`の`dependencies`、`package.json`の`dependencies`など）はエンドユーザー影響の対象外とする。
テスト、CIとworkflow、開発手順と開発用ツールの設定（`Makefile`・`mise.toml`・pre-commit・lint設定）の変更も対象外とする。
エージェント向け文書（`AGENTS.md`・`CLAUDE.md`・`.claude/`配下）、開発者向け文書（`docs/development/`）、開発専用のロックファイル更新も同じく対象外とする。
docsサイトは`master`へのpushで`docs.yaml`が公開するため、docsサイトだけの変更も対象外とする。
いずれとも判別できない変更は、配布物に含まれて利用者から観測できるかで判定する。

### 実施

該当する変更が1件以上ある場合だけ、次のバージョン区分からエージェントが区分を決め、`releaser <patch|minor|major>`を実行して完了まで検収する。
0件の場合はリリースせず、リリースしなかったことと理由を完了報告へ含める。

- バグ修正・軽微な機能追加: パッチ
- ある程度大きい機能追加や変更: マイナー
- 大規模な機能追加など: メジャー

`releaser`はdotfilesの`pytools/releaser.py`が提供するコマンドである。
既定branchの確認、未コミット変更の確認、未pushのcommitのpush、CI完了待機、`release.yaml`のworkflow_dispatch起動、runの監視、ローカルの`git pull --ff-only`を行う。
引数を省略した`releaser`はヘルプと未リリースコミットの一覧を表示するだけで終わる。
`gh workflow run release.yaml`などの低水準コマンドは、`releaser`の内部実装又は人間が手動で補助する場合にだけ用いる。
`releaser`はCI待機とリリースworkflowの監視で長時間かかるため、前景の実行時間上限を超える場合は背景実行か委譲で実行し、終了状態を観測してから報告する。

## 足回りファイルの推奨設定維持

各プロジェクトの`pyproject.toml`・`.textlintrc.yaml`・`.markdownlint-cli2.yaml`・
`.pre-commit-config.yaml`・`.github/workflows/`配下はpyfltr配布の推奨ガイドに揃える。
推奨ガイドは`~/pyfltr/docs/guide/recommended.md`と`~/pyfltr/docs/guide/recommended-nonpython.md`である。

- lint違反への対応と推奨設定の緩和は、`agent-toolkit:writing-standards`の`references/implementation-time.md`「lintと機械チェック」の原則に従う。設定の緩和（ruff・pylint・textlint等の設定ファイルへのignore追加、lint設定の弱体化）は根本原因の修正と行単位の無視で足りない場合に限る慎重な手段とする。推奨から逸脱する設定を導入する場合は、該当箇所に理由を述べたコメントを直接記述する
- 推奨ガイド自体の改訂を要する場合の投入順は「追従作業と複数リポジトリ横断投入」節に従う

## 変更時の同期対象マトリクス

変更内容に応じて確認すべきプロジェクトを示す。

| 変更内容 | dotfiles | pyfltr | pytilpack | smpr | glatasks | gv | lc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitHub Actions全般 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| リリースワークフロー | N/A | ★ | ★ | N/A | ★ | ★ | ★ |
| git-cliff設定 | N/A | ★ | ★ | N/A | ★ | ★ | ★ |
| Makefile構成 | ★ | ★ | ★ | ★ | ★ | N/A | N/A |
| commit.template設定 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| mise設定 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| pre-commit設定 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| textlintルール | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| pyfltr設定・更新 | ★ | N/A | ★ | ★ | ★ | ★ | ★ |
| pinact/アクション更新 | ★ | ★ | ★ | ★ | ★ | ★ | ★ |
| UV_FROZEN運用 | ★ | ★ | ★ | ★ | ★ | N/A | N/A |
| ドキュメント構成 | ★ | ★ | ★ | △ | ★ | ★ | ★ |
| Python CI構成 | ★ | ★ | ★ | ★ | N/A | N/A | N/A |

★=必須同期、△=確認推奨（smprは厳密一致不要）、N/A=スキップ

パッケージ管理系に固有の規範を横展開する場合、対象リポジトリの判定はコンテキスト上のローカル指示の
プロジェクト一覧の記載だけを根拠とせず、対象ファイルまたはロックファイルの実在を確認して確定する。

本スキルの対象範囲（ツールチェイン・ドキュメント構成）に該当すると判定済みで、マトリクスに該当行が無い変更内容は、
「前提」節の対象プロジェクト一覧を候補集合とする。
判定手順で列挙した適用前提を、各候補の現物を観測して確かめるか、明文化された方針と比べる。
適用前提が成立したプロジェクトだけを★相当の必須確認へ進める。

`commit.template設定`はsetupタスク（`make setup`または`mise run setup`）から
`git config --local commit.template .gitmessage`を呼ぶ実装を指す。
`.gitmessage`本文の追加変更も含め、setup実装と本体ファイルを揃えて変更する。

## 意図的に維持している差異

以下の差異はプロジェクト間で意図的に異なる設定としている。
統一対象外として扱う。

- `textlint-rule-prh`（dotfilesのみ）: Claude Codeのコンテキスト汚染を防ぐためtextlint系を特に厳しくする方針
- pytilpackの`docs.yaml`に`paths:`なし（pytilpackのみ）: mkdocstringsがPythonソースから
  ドキュメントを生成するため、ソース変更でもdocs workflowが起動する必要がある
- Dependabot alertsの有効・無効（dotfiles・GLATasksは有効、pytilpackは無効）:
  pytilpackはライブラリであり、ロックファイルが開発専用のため利用者の実行環境への脆弱性の影響が限定的である

## ドキュメント章構成の統一

README.md・AGENTS.md・docs/development/development.mdの標準章構成・共通文面・記述基準・バッジ記法は
[references/doc-structure.md](references/doc-structure.md)が定める。
ドキュメント構成を変更・同期する場合は同ファイルを読む。

## 補足事項

### ドキュメント・運用方針

- 他プロジェクト作業中に`~/.claude/rules/agent-toolkit/*`や`/agent-toolkit:*`スキルの問題を
  発見したらdotfiles側を修正する（マスター）

### gv / lc（Windows用プロジェクト）の特殊事情

- Linuxでの検証はlint系（textlint / markdownlint / prettier）のみ確認可能
- Makefileではなく`mise.toml`のタスクを使用する。prekフレームワークは`uvx prek`で呼び出す
- `package.json`の`lint`/`lint:fix`スクリプトは`AGENTS.md`もtextlint/markdownlint-cli2対象に含める
  - 新規Node系プロジェクトでも同様に設定する
- cargo-denyの導入は`taiki-e/install-action@v2`と`with: tool: cargo-deny`を用い、
  actionをpinactのハッシュピン対象にする。
  `taiki-e/install-action@cargo-deny`のshort-handを維持する場合だけ、そのactionを`.pinact.yaml`の
  ハッシュ固定対象から除外する（ツール名タグのSHA固定は更新後に参照不能となり得るため
  公式に強く非推奨であることによる）

`~/gv`の`mise.toml`は`LOCALAPPDATA`を参照しない。
`~/lc`の`mise.toml`による参照はWindows用タスクの内側だけにあり、Linuxでの設定読み込みには影響しない。
両リポジトリでは、Linuxからmiseを起動するための`LOCALAPPDATA`の付与は不要である。

加えて`~/gv`のRustコードは、`windows-future`等のWindows専用クレートが依存ツリーに含まれるため、
Linux環境で`cargo check`・`cargo clippy`・`cargo test`がビルド段階で失敗する。
Linuxから`~/gv`のRustコードを変更する場合は次のいずれかで対処する。

- Windows実機で`cargo`系チェックを実行してからpushする
- `SKIP=pyfltr`でcargo系チェックを含むhookを無効化してコミットし、cargo対象外の変更パスへ`uvx pyfltr run`を実行する
- 該当コードを`#[cfg(windows)]`ガードで囲み、Linux向けビルド対象外にする

### prek / pyfltr / ビルド関連

- 全プロジェクトでprekフレームワークにより`pyfltr fast`が実行される
  - `markdownlint-fast`／`textlint-fast`によりmd変更時のlintが軽量に実行される
  - `~/dotfiles`はdev依存へ固定した`uv run --frozen pyfltr fast`を呼び出し、その他のプロジェクトは`uvx pyfltr fast`を呼び出す

### CI / リリース関連

- CI workflowのLinuxジョブはpyfltr公式イメージの`container:`実行を方針とし、
  container適用対象・キャッシュ方式の具体は各リポジトリの`.github/workflows/**`をSSOTとして揃える

以下4点はworkflow編集時の確認観点であり、実値は各リポジトリの`.github/workflows/**`に従う。

- container化ジョブではuv / pnpm / Node.js / mise / pinactのセットアップステップは不要で、
  `pinact run --check`を直接呼び出せる。
  ただしGitHub Actionsのピン留め確認には独立したstepを置かず、pyfltrの組み込みlinter`pinact`へ任せる。
  `pinact`は`pyproject.toml`の`[tool.pyfltr]`が持つ`preset = "latest"`で有効になり、CIの`ci.yaml`が実行する`pyfltr ci`と、push前に実行する`pyfltr run`・`pyfltr fast`（prekのpre-commitを含む）のいずれにも含まれるため、独立したstepは同じ確認の重複になる。
  Pythonバージョンマトリクスは
  `env: UV_PYTHON: ${{ matrix.python-version }}`で引き継ぐ。
  `defaults.run.shell: bash`の指定が必須（GitHub Actionsの`container:`既定シェルが`sh`のため）
- `release.yaml`の`GH_TOKEN`は`${{ github.token }}`を使う（推奨構文）
- `release.yaml`のCI待機は、対象コミットを指定してCIワークフロー（`ci.yaml`）の実行を直接照会し、
  その結論で判定する方式とする。`check-suites` APIの先頭suiteを判定に使う方式は、
  リリースワークフロー自身のsuiteを拾い、CIが成功していても待機がタイムアウトするため、前段の直接照会を用いる。
  各リポジトリが用いる照会コマンドは`.github/workflows/`配下をSSOTとし、本文へ写さない
- `container:`実行ジョブのstepへ新しいコマンド呼び出しを追加する場合は、先行stepで導入されることを確認するか、
  ジョブが宣言する`image`上でそのコマンドの存在を確認する
  - どちらでも利用可能と確認できないコマンドは、呼び出す前に同じジョブで導入する
  - `ENTRYPOINT`がシェル以外のimageでは、
    `docker run --rm --entrypoint sh <image> -c 'command -v <command>'`でimage内の存在を確認する

---
name: sync-cross-project
description: >
  作者個人の姉妹プロジェクト群の間でツールチェイン（Makefile、mise、prek、GitHub Actionsなど）や
  ドキュメント構成を揃える際に必ず使う。gv・lcなど姉妹プロジェクト自体を編集する作業と、
  推奨ガイド・共有ファイルへの追従作業でも使う。個人プロジェクトのAWI処理への着手時、
  リリース作業時、lint設定・足回りファイルの変更時も使う。
  `/sync-cross-project`、「他プロジェクトへの反映」
  「プロジェクト間の同期」などのキーワードで自動トリガーしてよい。プロジェクト固有のアプリケーションロジック変更は対象外
---

# 姉妹プロジェクト間のツールチェイン・ドキュメント構成同期

## 前提

姉妹プロジェクト群は、共通化対象のツールチェインとドキュメント構成を同じ章構成・コマンド名・設定キーで揃える。
意図的に維持している差異に該当しないコメント表現・項目順・既定値は、同一文面または同等の設定へ揃える。
1プロジェクトで変更した場合、後述のマトリクスに基づいて他プロジェクトへの波及要否を確認し、必要ならユーザーへ提案する。

対象プロジェクトの一覧と絶対パスはセッション開始時にコンテキストへロードされるローカル指示から得る。
個別プロジェクトのパスはそのコンテキスト経由で参照する。

明らかにプロジェクト固有の変更や、「ツールチェインやドキュメント構成など」以外の変更であれば確認不要。

## 判定手順

リリース専用依頼でも本スキルの起動は維持する。現在の会話コンテキストと自身が実行した操作から、
次の条件を両方確認できる場合は、
姉妹プロジェクトへの波及調査を開始しない。

- 既存commitのPR作成・マージ・CI検収だけを行う
- 現行セッションで同期対象を変更・計画・レビューしていない

いずれかを確認できない場合は、以下を実施する。

- 変更内容の分類を特定する（例: prek設定、mise設定、CI workflow、README構成など）
- 「変更時の同期対象マトリクス」で波及プロジェクトを決める
- 「意図的に維持している差異」に該当しないか確認する
- 移植対象の記述が依拠する前提（配布形態・外部サービスの有効状態など）を列挙し、
  各前提が移植先で成立することを実測または移植先の明文化された方針との照合で確認する。
  成立しない前提に依拠する記述は移植先の実情に合わせて書き換えるか、移植対象から除く
- 該当プロジェクトに対してサブエージェント（`general-purpose`）で並列調査して差分を把握する
- 同期が必要なプロジェクトと推奨アクションをユーザーに報告する

スコープに含むのは「ツールチェインやドキュメント構成」の範囲。
典型パスは以下。

- ビルド/タスク: `Makefile` / `mise.toml`
- Python設定: `pyproject.toml`
- Node.js設定: `package.json`
- lint/format: `.pre-commit-config.yaml`
- CI: `.github/workflows/**`
- ドキュメント: `README.md` / `CLAUDE.md` / `docs/**/development.md` / `docs/**/security.md`

## 追従作業と複数リポジトリ横断投入

推奨ガイド（`~/pyfltr/docs/guide/recommended.md`・`recommended-nonpython.md`）または
姉妹プロジェクト共有ファイルへの追従作業では、着手前に次の2点を確認する。

- 同期対象マトリクスで波及先とした各プロジェクト宛の未処理AWIを`atk wi list`と`atk wi grep`で照会し、実装レビューで確定した
  検証結果・訂正記録が記録されていないかを確認する。記録があった場合はその結論を変更の前提として取り込む
- 推奨ガイドが新設した設定へ追随する場合、追随先が実際に取得する配布物の公開版に当該設定が
  含まれることを、公開版を明示指定した実行で確認する

追従を目的とする計画では、本計画は正本の現行内容への追従に限定し正本の設計自体の改善は対象外とする旨と、
正本の改善が必要と判明した場合は正本リポジトリ宛のAWIとして登録する旨を計画本文へ明記する。

全波及先へ同じ設定値を配布する場合は、その設定を制御するツールを本プロジェクト群が所有しているか確認する。
所有している場合は、上流ツールの既定値または新設オプションへ吸収する案を、各リポジトリへ配布する案と同じ階層で比較する。
比較では次を確認する。

- 上流の公開既定を変更した場合の影響
- 波及先に固有の要件が存在するか
- 新設オプションの既定動作によって個別設定が不要になるか

設定値の同一性だけを上流吸収の十分条件にしない。
比較の結果として上流吸収を選ぶ場合は、`agent-toolkit:add-awi`で上流リポジトリ宛のAWIを1件投入する。
未投入の配布提案は取り下げ、適用済みの個別設定は別の削除対象として扱う。

推奨ガイドまたは同期対象マトリクスが対象とするファイル群を更新した場合、
同一セッション内で実行主体が`agent-toolkit:add-awi`をSkill機能で起動し、
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
一括更新コマンドは、各プロジェクトが正式に定義する`make update`か`mise run update`相当の入口とする。
実行後はコマンドが生成した差分全体を検収し、同一のコミットへ含める。

明示的な更新要求がない個人プロジェクトのAWI処理では、対象プロジェクトのロックファイルの
最終コミット日時（`git log -1 --format=%cI -- <ロックファイル>`）が1日以上前（目安）であれば、
実装着手時に一括更新コマンドを1回実行して依存を最新化する。1日未満なら実行しない。
`agent-toolkit:plan-mode`を使う場合は更新要否を計画へ記載し、計画承認後、計画対象の編集前に実行する。
計画前の診断専用実行、隔離worktree、差分の退避、同じ依存更新の再実行は追加しない。

一括更新コマンドが依存更新後の全体検査を連鎖し、前景実行の時間上限内に完了しない場合は、
依存更新部分と全体検査を別の前景実行へ分ける。この分割は同一の依存更新の継続であり、
同じ依存更新の再実行には当たらない。依存更新を委譲する場合は、分割条件と同一更新の継続であることを依頼文へ含める。

依存更新後の検証が失敗した場合、配布物の版指定（`pyproject.toml`の`dependencies`等）が破壊を生じる版へ
到達しうるか否かで扱いを分ける。到達しうる場合は利用者環境で成立している欠陥として同一セッション内で是正し、
開発環境のロックファイル内に閉じる場合は更新を巻き戻して独立したAWIとして登録してよい。

破壊的変更の波及判定では、上限の記載があっても破壊を生じる版がその範囲に含まれる場合は波及すると判定する。
Cargoの既定のキャレット要件のように上限が常に存在する記法があるため、上限の有無そのものを判定基準としない。

## リリース運用

`gv`・`lc`・`glatasks`・`pyfltr`・`pytilpack`のリリースは`releaser`コマンドで起動する。
バージョン区分は次のとおりとする。

- バグ修正・軽微な機能追加: パッチ
- ある程度大きい機能追加や変更: マイナー
- 大規模な機能追加など: メジャー

## 足回りファイルの推奨設定維持

各プロジェクトの`pyproject.toml`・`.textlintrc.yaml`・`.markdownlint-cli2.yaml`・
`.pre-commit-config.yaml`・`.github/workflows/`配下は、pyfltr配布の推奨ガイドに揃える。
推奨ガイドは`~/pyfltr/docs/guide/recommended.md`と`~/pyfltr/docs/guide/recommended-nonpython.md`である。

- 推奨設定を独自判断で緩和しない（ruff・pylint・textlint等のignore追加、lint設定の弱体化）
  - 緩和を提案する場合は、ignore追加のメリットとデメリットを比較した文面案を提示してユーザーの合意を得る
- プロジェクト固有事情で推奨から逸脱する設定を導入する場合、該当箇所に理由を述べたコメントを直接記述する
- lint違反が出た場合は根本原因（コード側）を修正する。設定でのignore追加は避ける
- 推奨ガイド自体の改訂が必要と判断した場合は、`~/pyfltr`の推奨ガイドを先に更新してから各プロジェクトへ反映する

## 変更時の同期対象マトリクス

変更内容に応じて確認すべきプロジェクトを示す。
プロジェクト名とローカルパスの対応はコンテキスト上のローカル指示から取得する。

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
判定手順で列挙した適用前提を、各候補で実測または明文化された方針と照合する。
適用前提が成立したプロジェクトだけを★相当の必須確認へ進める（努力目標）。
対象範囲外の変更は現行の除外規定どおり確認不要とする。

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

README.md・CLAUDE.md・docs/development/development.mdの標準章構成・共通文面・記述基準・バッジ記法は
[references/doc-structure.md](references/doc-structure.md)が定める。
ドキュメント構成を変更・同期する場合は同ファイルを読む。

## 補足事項

### ドキュメント・運用方針

- ツールチェイン周りの修正の場合は以下のメンテナンスも確認する（気付きにくい）
  - `pyfltr/docs/guide/recommended.md`
  - `pyfltr/docs/guide/recommended-nonpython.md`
- 他プロジェクト作業中に`~/.claude/rules/agent-toolkit/*`や`/agent-toolkit:*`スキルの問題を
  発見したらdotfiles側を修正する（マスター）
- README.md・CLAUDE.md・docs/development/development.md間で、
  共通化が可能な節（役割分担・コミットメッセージ等）が出てきた場合も同様に揃える

### gv / lc（Windows用プロジェクト）の特殊事情

- Linuxでの検証はlint系（textlint / markdownlint / prettier）のみ確認可能
- Makefileではなく`mise.toml`のタスクを使用する。prekフレームワークは`uvx prek`で呼び出す
- `package.json`の`lint`/`lint:fix`スクリプトは`CLAUDE.md`もtextlint/markdownlint-cli2対象に含めている
  - 新規Node系プロジェクトでも同様に設定する
- cargo-denyの導入は`taiki-e/install-action@v2`と`with: tool: cargo-deny`を用い、
  actionをpinactのハッシュピン対象にする。
  `taiki-e/install-action@cargo-deny`のshort-handを維持する場合だけ、当該actionを`.pinact.yaml`の
  ハッシュ固定対象から除外する（ツール名タグのSHA固定は更新後に参照不能となり得るため
  公式に強く非推奨であることによる）

`~/gv`・`~/lc`の`mise.toml`はWindows前提で`{{ env.LOCALAPPDATA }}`を参照しているため、
Linux環境ではmiseの評価時に未定義変数エラーで展開に失敗する。
pre-commit hookや`uvx pyfltr`配下のmarkdownlint・textlintなどmise経由で動く処理も同じ理由で中断する。
ドキュメント修正等でLinuxから作業する場合は、以下のいずれかで対処する。

- 全実行コマンドの先頭に`LOCALAPPDATA=/tmp/dummy`を付与する（`git commit`時にも必須）
- mise依存のlint・buildタスクを呼ばず、該当箇所はスキップする

加えて`~/gv`のRustコードは、`windows-future`等のWindows専用クレートが依存ツリーに含まれるため、
Linux環境で`cargo check`・`cargo clippy`・`cargo test`がビルド段階で失敗する。
Linuxから`~/gv`のRustコードを変更する場合は次のいずれかで対処する。

- Windows実機で`cargo`系チェックを実行してからpushする
- `SKIP=<該当hook>`環境変数でpre-commit hookを部分的に無効化してコミットする
- 該当コードを`#[cfg(windows)]`ガードで囲み、Linux向けビルド対象外にする

### prek / pyfltr / ビルド関連

- 全プロジェクトでprekフレームワークにより`pyfltr fast`が実行される
  - `markdownlint-fast`／`textlint-fast`によりmd変更時のlintが軽量に実行される
  - 全プロジェクト共通で`uvx pyfltr fast`を呼び出す

### CI / リリース関連

- CI workflowのLinuxジョブはpyfltr公式イメージの`container:`実行を方針とし、
  container適用対象・キャッシュ方式の具体は各リポジトリの`.github/workflows/**`をSSOTとして揃える
- リリース手段とバージョン区分は本スキル「リリース運用」節を参照する

以下4点はworkflow編集時の確認観点であり、実値は各リポジトリの`.github/workflows/**`に従う。

- container化ジョブではuv / pnpm / Node.js / mise / pinactのセットアップステップは不要で、
  `pinact run --check`を直接呼び出せる。Pythonバージョンマトリクスは
  `env: UV_PYTHON: ${{ matrix.python-version }}`で引き継ぐ。
  `defaults.run.shell: bash`の指定が必須（GitHub Actionsの`container:`既定シェルが`sh`のため）
- `release.yaml`の`GH_TOKEN`は`${{ github.token }}`を使う（推奨構文）
- `release.yaml`のCI待機ロジックはbash系（pyfltr / pytilpack / glatasks）が`gh api` + `jq`方式、
  PowerShell系（gv / lc）が`check-suites` API方式
- `container:`実行ジョブのstepへ新しいコマンド呼び出しを追加する場合は、先行stepで導入されることを確認するか、
  ジョブが宣言する`image`上で当該コマンドの存在を確認する
  - どちらでも利用可能と確認できないコマンドは、呼び出す前に同じジョブで導入する
  - `ENTRYPOINT`がシェル以外のimageでは、
    `docker run --rm --entrypoint sh <image> -c 'command -v <command>'`でimage内の存在を確認する

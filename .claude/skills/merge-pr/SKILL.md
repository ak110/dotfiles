---
name: merge-pr
user-invocable: false
description: 「PRをマージして」などの明示的な依頼を受けたとき、対象PRを検証し、マージ後のブランチ同期、CI確認、および必要なリリースの検収を完了させる
---

# PRマージの完遂手順

このスキルは、明示的にPRのマージを依頼された場合や、`agent-toolkit:process-wi`の終了時に日次リリースの条件（`dotfiles-release`「developとmasterのリリース運用」を参照）を満たした場合にのみ実行する。単にPRが存在することを確認しただけでは、本スキルを起動する契機としては扱わない。
リリースの成否はリモートのブランチ、CI、および必要なリリースの結果に基づいて判定し、ローカルの作業ツリーやブランチはユーザーの変更を壊さない範囲でのみ操作する。
`gh`コマンドの受け付け可能な引数形式や取得できる項目については、操作を実行する直前にヘルプで確認する。

## 名指し参照の読込方法

本文中の各参照について適用条件を確認し、選んだ資料の該当範囲を読み込んでから、その基準に基づく具体的な判断へ進む。起動時の表にある資料は、起動本文とともに提供される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-release/SKILL.md#「developとmasterのリリース運用」` | 判断 | `section:*/skills/dotfiles-release/SKILL.md#「developとmasterのリリース運用」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| CIまたはReleaseのrunが失敗し、原因を分類する前 | `agent-toolkit:bugfix`の`references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md` | |
| マージ後に到着したレビューを取得して判定する前 | `agent-toolkit/skills/process-wi/references/github-copilot-review-audit.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/github-copilot-review-audit.md` | |

## 失敗時の対応

- CIやリリース以外の工程が失敗した場合は、すでに完了した外部の状態を保持したまま、失敗した工程、現在の外部状態、runのURL、および再開に必要な情報を報告して停止する。PR作成やマージの再試行、auto-mergeの有効化、自動修復、自動ロールバックなどは行わない。
- CIやリリースの実行（run）が失敗した場合は、run全体の終了状態と失敗ログを確認し、`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「再現性」に沿って原因を分類する。ネットワーク障害、配布元のタイムアウト、レート制限、ランナー障害といった一時的な外部要因が疑われる場合は、同ドキュメントの手順に従って広域障害の有無を確認したうえで、同一の原因について失敗したジョブを1回だけ再実行して検収を続行する。
- 再実行しても再度失敗した場合、初回のログで一時的な外部要因が疑われない場合、またはログを取得できない場合は、元のrunの失敗状態を維持したまま、上記と同様の内容を報告して処理を停止する。

## 対象PRの選択

PR番号かURLが明示されている場合は、そのPRを対象とする。指定がない場合は、`develop`から`master`へのopenなPRが1件のみ存在する場合に限って対象とする。該当するPRが0件か複数件ある場合は、PR番号やURLの指定を求めて停止する。headが`develop`以外、またはbaseが`master`以外のPRについては、状態を変更せずに停止する。
なお、GitHub側のリポジトリ設定でheadブランチを`develop`に制限するような操作は行わず、既存の設定をそのまま維持する。

## ローカルdevelopブランチの同期条件

ユーザーの未コミットの変更やローカルブランチを保護するため、ローカルの`develop`ブランチは以下の条件をすべて満たす場合にのみ同期する（基準とするrefは参照元の各節で指定する）。

- `git worktree list --porcelain`を実行したとき、`branch refs/heads/develop`を持つworktreeが1件だけ存在する（その絶対パスを`develop` worktreeとする）。
- そのworktreeで`git status --porcelain`を実行した結果が空であり、現在のブランチが`develop`である。
- rebase、merge、cherry-pickが中断した状態にない（対象worktreeのGitディレクトリ内に`rebase-merge`、`rebase-apply`、`MERGE_HEAD`、`CHERRY_PICK_HEAD`が存在しない）。
- `git merge-base --is-ancestor HEAD <基準ref>`の終了コードが0である（fast-forwardマージが可能である）。

## マージ前の確認事項

- `git fetch origin develop master`を実行したあと、`origin/develop`と、PR情報から取得した`headRefOid`が同一のcommitを指していることを確認し、この`headRefOid`を確認対象のcommitとして保持する。
- PRがopen状態かつdraftでなく、baseが`master`、headが`develop`であり、マージ可能な状態（mergeable）であることを確認する。マージの前提条件はこれらのリモート側の状態のみとし、ローカル環境の状態は前提条件に含めない。
- 後続の処理でローカルの`develop`を同期できるかどうかは、基準refを`origin/develop`として「ローカルdevelopブランチの同期条件」を判定する。条件を満たさない場合はローカル環境には手を加えず、リモート側の処理のみで完遂する。
- 必須チェックの完了を待つ前に、対象PRに`event=pull_request`、`workflow=CI`、`name=statusline-version`のチェックが登録されるまで待機する。この待機は、`gh pr checks`のJSON出力を上限回数付きで繰り返し確認することによって行う。同じheadに対する`push`イベント起点の同名チェックは対象に含めない。これは、`push`起点のチェックが成功したあとに、`pull_request`起点の`statusline-version`チェックが非同期で遅れて登録される場合があるためである。観測結果と再検証手順は`docs/development/audit-records.md`「.claude/skills/merge-pr/SKILL.md：マージ前の確認事項：2026年10月5日」を参照する。
- チェックの登録を確認したあと、`gh pr checks --required --watch`を実行して必須チェックが完了するまで待機する。チェック成功後は、保持していたheadと`headRefOid`が一致した状態を維持したまま、`mergeStateStatus=CLEAN`になるまで上限回数を設けて待機する。
- 各種照会の途中でheadが変更された場合は直ちに処理を停止する。待機上限への到達、照会の失敗、対象の不一致、チェックの失敗、headの変更、`CLEAN`以外のステータスが確認された場合はマージせずに停止する。必須チェックの失敗時は「失敗時の対応」に従い、それ以外の理由による停止では外部の状態と再開に必要な情報を報告する。

## レビューコメントの確認

必須チェックの完了を待つ処理と並行して、対象PRのレビュー本文と行ごとのコメントを取得し、各指摘の妥当性をコード実装やエージェント向け文書と照らし合わせて判定する。

- 妥当と判断した指摘については、`agent-toolkit/rules/01-agent.md`「完遂と先送り」に従って、同一セッション内で対応するものと次セッション以降へ回すものに分類し、後者のみをAWIとして登録する。妥当ではないと判断した指摘は登録せず、その判断根拠を報告に残す。
- すべての指摘に対する判定と処置を完了させてからマージの工程へ進む。同一セッション内で修正した場合は、修正後のPR headを新たな確認対象として「マージ前の確認事項」を最初からやり直す（修正前の必須チェック成功を根拠としてマージしない）。

## PRのマージ手順

マージを実行する直前に`headRefOid`と`mergeStateStatus`を再取得し、確認対象のcommitと一致しており、ステータスが`CLEAN`であることを確認する（一致しない場合は処理を停止して報告する）。
マージの実行時には、操作直前のhead OIDをアトミックな一致条件として渡し、明示的なマージコミットを作成する。自動マージの指定やマージ後のブランチ削除は指定せずに実行する。この条件を`gh`コマンドで保証できない場合は、マージを見送る。マージコマンドは1回のみ実行し、失敗した場合は再試行せずに失敗理由と再開に必要な情報を報告する。

## マージ後のブランチ同期とCI確認

- `git fetch origin master`を実行したあと、PRの`mergeCommit.oid`と`origin/master`が一致することを確認する。以降のGit操作は`origin/master`を基準として行う。
- `origin/develop`の更新は、ローカルブランチを操作元にせず、`git push origin origin/master:refs/heads/develop`を実行して直接更新する。fetch後に`origin/develop`と`origin/master`の短縮OID（7文字以上）が一致することを確認する。
- ローカルの`develop`の同期が可能であると見込んでいた場合は、実行直前に基準refを`origin/master`として「ローカルdevelopブランチの同期条件」を再確認し、`develop` worktreeのパスがマージ前と変わっていないことも確かめる。条件を満たしていれば`git -C <develop worktreeの絶対パス> merge --ff-only origin/master`を実行し、`origin/master`と一致することを確認する。もし条件が崩れていた場合はローカルの同期処理のみをスキップし、スキップした理由、対象worktreeの絶対パス、ローカルの`develop`および`origin/master`の短縮OIDを完了報告に記録する。
- マージ後のmasterへのpushや同期後のdevelopへのpushに伴うCIは、すでに必須チェックを通過したPR headと同一ツリーの再確認となるため、完了を待たずに進めてよい（努力目標）。ただし、statuslineの変更を含む場合のmaster CIの結果については、後述の「条件付きリリース検収」において、master pushのCI成功を契機に起動する`Release statusLine` runの成否を通じて確認する。

## 条件付きリリース検収

マージコミットの第一親との差分に`rust/claude-statusline/`の変更が含まれていない場合は、この工程をスキップする。変更が含まれている場合は、以下の点を確認する。

- `origin/master`の完全OIDに対応する`Release statusLine`のrunを完全なdatabase IDで特定し、`prepare`ジョブがスキップされずに実行されたrunが完了するまで待機する。`gate`以外のすべてのジョブがスキップされたrunは対象外として次のrunを待ち、`prepare`の実行可否が未確定の間は確定するまで状況を監視する。master CIが失敗したことでrun自体が作成されない場合は、「失敗時の対応」に従う。
- マニフェスト（`Cargo.toml`）のバージョン番号に対応する`statusline-v<version>`タグが、`origin/master`を指していること。
- GitHub Releaseが作成されており、`pytools/_internal/setup_statusline_binary.py`で定義された`ASSET_NAME_POSIX`および`ASSET_NAME_WINDOWS`という名前のアセットが添付されていること。

同一の`origin/master`完全OIDに対応する実行結果のみを検収の対象とする。Release runが失敗した場合は「失敗時の対応」を適用し、タグ、Release、アセットの検収に失敗した場合は外部の状態、失敗した工程、runのURL、および再開に必要な情報を報告する。

## マージ後に到着したレビューの確認

GitHub Copilotによるレビューはマージ完了後に遅れて到着することがあるため、前述の2つの工程を終えた段階で1回だけ確認する。

- `atk review-audit pending --repo <OWNER>/<REPO>`を実行し、終了コードが0でJSONの`reviews`および`threads`が正常に取得でき、かつ対象PRがどちらにも含まれていない場合はレビューの取得を省略する。それ以外の場合（対象PRが含まれている、終了コードが非0、または出力が解析不能な場合）は、対象PRのCopilotによるレビュー本文とスレッドを1回取得する。
- レビューの取得、妥当性の判定、GitHubへの記録、および判定済み情報の記録は、`agent-toolkit/skills/process-wi/references/github-copilot-review-audit.md`に従って実施し、対象はそのPRのみに限定する。新たなレビューを要求したり、到着を待機したりすることはない（未着分のレビューは、次回以降の`agent-toolkit:process-wi`選定工程における監査で取得される）。
- 修正が必要と判断した指摘については、「レビューコメントの確認」で述べた振り分けルールに従って処理する。同一セッション内で修正する場合は`develop`への通常の変更として扱い、マージ工程を再度実行することはせず、次回のリリースPRでの公開に委ねる。

## マージ後にdevelopへ加えた変更のCI確認

`agent-toolkit:process-wi`の終了処理から本スキルを実行した場合に限り、マージ完了後に同じセッション内で`develop`へ加えた変更は、pushを行ってCI runが起動したことを確認した時点で公開工程を完了とし、CIの完了待ちは省略する。次回のリリースPR作成時に改めて必須チェックの完了を待機することになり、また`master`ブランチは必須CIを通過したマージコミットのみで更新されるため、未検証の変更が`master`に混入することなく`develop`内に留まるためである。変更範囲のローカル検証は通常どおりすべてパスさせてからpushを行い、省略したCIのrun URLを完了報告に記録する。なお、本スキルのマージ工程自体のCI検収については、前述の各節の規定に従って確実に完了させる。

## 完了条件

- マージ後のブランチ同期時に`origin/develop`と`origin/master`の短縮OIDが一致していることを確認し、マージ対象の必須CIおよび必要なリリースの検収が成功していること。
- 「マージ後に到着したレビューの確認」を1回実施し、各指摘の分類と必要な処置を確定していること。
- ローカルの`develop`ブランチを同期した場合は、その同期処理において`origin/master`との一致を確認していること。

マージ完了後に同じセッション内で`develop`へ加えた変更がある場合は、「マージ後にdevelopへ加えた変更のCI確認」に定めた条件に基づいて判定し、検収済みのマージ結果とは区別して報告する。

作業の合否判定はリモート側の状態だけで行い、ローカルの作業ツリーやブランチの状態は判定から外す。`git status --short`の出力結果は現状の情報として完了報告に添え、リモート側の完了状況と、ローカルの`develop`を同期できたかどうかを明確に区別して報告する。

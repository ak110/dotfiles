---
name: merge-pr
user-invocable: false
description: 「PRをマージして」などの明示的な依頼を受けたとき、対象PRを検証し、マージ後のブランチ同期、CI確認、および必要なリリースの検収を完了させる
---

# PRマージの完遂手順

明示的なマージの依頼か、`release-dotfiles`「developとmasterのリリース運用」の手順から起動された時だけ、PRをマージして検収まで終える手順書である。
PRの状態を尋ねられただけの時は、状態を1回取得して報告するところで終える（マージと継続の監視は始めない）。
合否はリモートのブランチ、CI、リリースの結果で判定し、ローカルの作業ツリーとブランチはユーザーの変更を壊さない範囲でだけ操作する（`agent-basics:writing-standards/references/quality-principles-constraints.md`）。
`gh`の引数の形式と取得できる項目は、操作の直前にヘルプで確かめる。
どの段で止まっても、本書「失敗時の対応」に従う。

## 手順

1. 対象PRを決める。
   PR番号かURLの指定があればそのPRを、無ければ`develop`から`master`へのopenなPRが1件だけの時にそのPRを対象とする。
   0件か複数なら番号かURLの指定を求めて停止する。
   headが`develop`以外かbaseが`master`以外のPRは、状態を変えずに停止し、GitHubの既存の設定はそのまま保つ。
2. マージ前の確認事項を確かめる。
   1. `git fetch origin develop master`の後、`origin/develop`とPRの`headRefOid`が同じcommitを指すことを確かめ、この`headRefOid`を確認対象として保持する。
   2. PRがopenでdraftでなく、baseが`master`、headが`develop`で、mergeableであることを確かめる。
      前提はリモートの状態だけとする。
   3. 基準のrefを`origin/develop`として本書「ローカルdevelopブランチの同期条件」を判定し、満たさなければローカルには手を加えずにリモートの処理だけで完遂する。
   4. 対象PRに`event=pull_request`、`workflow=CI`、`name=statusline-version`のチェックが登録されるまで、`gh pr checks`のJSONを上限回数付きで繰り返し確かめる。
      数えるのは`pull_request`起点のチェックだけである（`push`起点の同名チェックの成功の後に、`pull_request`起点のチェックが遅れて登録される場合があるため）。
      観測結果と再検証手順は`docs/development/audit-records-project-environment.md`「.claude/skills/merge-pr/SKILL.md：マージ前の確認事項：2026年10月5日」にある。
   5. `gh pr checks --required --watch`で必須チェックの完了を待つ。
      成功の後、`headRefOid`が保持した値と一致したまま`mergeStateStatus=CLEAN`になるまで上限回数付きで待つ。
   6. 照会の途中でheadが変わったら直ちに停止する。
      待機の上限、照会の失敗、対象の不一致、チェックの失敗、headの変更、`CLEAN`以外の状態のどれかでも、マージせずに停止する。
3. 必須チェックを待つのと並行して、レビュー本文と行ごとのコメントを取得し、各指摘の妥当性をコードとエージェント向け文書に照らして判定する。
   妥当な指摘は`agent-toolkit/skills/atk-mode/references/conduct.md`「完遂と先送り」に従って同じセッションで直すものと次へ回すものに分け、後者だけをAWIとして登録する。
   妥当でない指摘は登録せず、判断の根拠を報告に残す。
   全ての指摘の処置を終えてからマージへ進む。
   同じセッションで直した場合は、直した後のheadを新しい確認対象として手順2からやり直す（直す前の必須チェックの成功を根拠にマージしない）。
4. マージする。
   永続データ（スキーマ・保存形式・移行）、認可・権限、公開・配布の挙動（リリース物、配布するプラグイン・hook、外部公開）に差分が触れる場合だけ、確認対象の `headRefOid` に結び付けて、次をマージ前に2〜4行で示す。
   - マージ後に元へ戻す手段と戻せない影響
   - 問題を最初に検知する手段（CI・監視・ユーザーの画面など）
   - 残るリスク

   PR本文とマージ前確認の結果を再利用し、別の定型報告は作らない。
   対象外のPRには追加の記述を求めない。
   外部からheadが変わった時は照会中もマージ直前も停止する。
   自ら修正し手順2から確認し直す時は、旧評価を流用せず新しいheadで評価し直す。
   この評価はCIや承認の代わりにしない。
   直前に`headRefOid`と`mergeStateStatus`を取り直し、確認対象のcommitと一致して`CLEAN`であることを確かめる（一致しなければ停止して報告する）。
   操作直前のhead OIDをアトミックな一致条件として渡し、明示的なマージコミットを作る。
   自動マージとマージ後のブランチ削除は指定せずに実行する。
   この一致条件を`gh`で保証できない場合はマージを見送る。
   マージのコマンドは1回だけ実行し、失敗したら再試行せずに理由と再開に必要な情報を報告する。
   コマンドが受け付けられたこととマージされたことは別に確かめ、PRの`state`が`MERGED`で`mergeCommit.oid`が取れることを照会して確かめる。
5. マージ後にブランチを同期する。
   1. `git fetch origin master`の後、PRの`mergeCommit.oid`と`origin/master`が一致することを確かめ、以降のGit操作は`origin/master`を基準にする。
   2. `origin/develop`は、ローカルブランチを操作元にせず`git push origin origin/master:refs/heads/develop`で直接更新し、fetchの後に`origin/develop`と`origin/master`の短縮OID（7文字以上）が一致することを確かめる。
   3. 手順2でローカルの同期ができると見込んでいた場合は、実行の直前に基準のrefを`origin/master`として同期条件を判定し直し、`develop` worktreeのパスがマージ前と同じことも確かめる。
      満たせばリリース作業を行うworktreeの `libexec/sync_release_checkout.py` をPython 3.13以降で起動する。
      引数は順に `<develop worktreeの絶対パス> <確認済みのorigin/masterの完全OID> <managed-tempの絶対パス>` とする。
      このコマンドは主checkoutを進める前に対象コミットの共有依存環境を準備し、成功した場合だけ同じOIDへfast-forwardする。
      準備に失敗したら通常の `git merge` で代替せず、診断を完了報告に残してCIの確認へ進む。
      満たさなければローカルの同期だけを省き、理由、対象worktreeの絶対パス、ローカルの`develop`と`origin/master`の短縮OIDを完了報告へ書く。
   4. マージコミットの完全OIDと一致する`master` pushのCIを特定して結果を確認する。
      同じツリーの`develop` pushで全ジョブの成功を所有判定が確認した場合は、その再利用を含むCI成功を受理する。
      それ以外は`master`側で全体を実行するため、結果を待つ。
      statusLineを含む場合は手順6で配布を検収する。
6. マージコミットの第一親との差分に`rust/claude-statusline/`の変更があれば、リリースを検収する（無ければ省く）。
   1. `origin/master`の完全OIDに対応する`Release statusLine`のrunを完全なdatabase IDで特定し、`prepare`ジョブが省かれずに実行されたrunの完了を待つ。
      `gate`以外の全ジョブが省かれたrunは対象外として次のrunを待ち、`prepare`の実行可否が決まるまで監視する。
      master CIの失敗でrun自体が作られない場合は本書「失敗時の対応」に従う。
   2. `Cargo.toml`のバージョンに対応する`statusline-v<version>`タグが`origin/master`を指すことを確かめる。
   3. GitHub Releaseがあり、`pytools/_internal/setup_statusline_binary.py`の`ASSET_NAME_POSIX`と`ASSET_NAME_WINDOWS`の名前の添付物があることを確かめる。
   4. 先に新しいバージョンが配布されていた場合は以前のバージョンへのlatestの巻き戻りがないことを確かめる。
      同じ`origin/master`の完全OIDに対応する結果だけを検収の対象とする。
7. マージ後に到着したレビューを1回だけ確かめる（GitHub Copilotのレビューはマージの後に遅れて届くことがある）。
   `atk review-audit pending --repo <OWNER>/<REPO>`の終了コードが0で、JSONの`reviews`と`threads`が取れて対象PRがどちらにも無ければ、取得を省く。
   それ以外（対象PRがある、終了コードが0以外、解析できない）は、対象PRのCopilotのレビュー本文とスレッドを1回取得し、取得、判定、GitHubへの記録、判定済みの記録は`agent-toolkit/skills/atk-mode/playbooks/process-wi-review-audit.md`に従って対象PRだけに行う。
   新しいレビューの要求と到着の待機はしない（未着分は次回以降の`agent-toolkit:atk-mode`の作業`process-wi`の選定工程の監査が取得する）。
   直すべき指摘は手順3と同じく分け、同じセッションで直す場合は`develop`への通常の変更とし、マージをやり直さず次回のリリースPRで公開する。
8. セッションをこの後に終える場合（`--once`の単回実行と協調モードの終了直前）に限り、マージの後に同じセッションで`develop`へ加えた変更は、変更範囲のローカル検証を全て通してからpushし、CIのrunが起動したことを確かめた時点で公開工程を終える（CIの完了は待たない）。
   次回のリリースPRで必須チェックを改めて待ち、`master`は必須CIを通ったマージコミットだけで更新されるため、未検証の変更は`develop`に留まる。
   省いたCIのrunのURLを完了報告に書く。
   常駐の巡回では、この変更を通常の公開単位として扱い、CI検収と`release-dotfiles`「developとmasterのリリース運用」の手順6の反映まで進める。
9. 完了を判定する。
   - `origin/develop`と`origin/master`の短縮OIDの一致を確かめ、マージ対象の必須CIと必要なリリースの検収が成功していること。
   - 手順7を1回行い、各指摘の分類と処置が決まっていること。
   - ローカルの`develop`を同期した場合は、`origin/master`との一致を確かめていること。

   合否はリモートの状態だけで判定し、`git status --short`の出力は現状の情報として添える。
   手順8の変更は、検収済みのマージの結果と分けて報告する。

返す報告に含める項目: 対象PRと確認したhead OID、マージコミットの完全OID、`origin/develop`と`origin/master`の一致、必須CIとReleaseの検収結果、ローカルの`develop`の同期の可否と理由、レビューの指摘の処置、`git status --short`。

## ローカルdevelopブランチの同期条件

ユーザーの未コミットの変更とローカルブランチを守るため、ローカルの`develop`は次の全てを満たす時だけ同期する（基準のrefは手順の各段が指定する）。

- `git worktree list --porcelain`で`branch refs/heads/develop`を持つworktreeが1件だけある（その絶対パスを`develop` worktreeとする）。
- そのworktreeの`git status --porcelain`が空で、現在のブランチが`develop`である。
- rebase、merge、cherry-pickが中断していない（worktreeのGitディレクトリに`rebase-merge`、`rebase-apply`、`MERGE_HEAD`、`CHERRY_PICK_HEAD`が無い）。
- `git merge-base --is-ancestor HEAD <基準のref>`の終了コードが0である。

## 失敗時の対応

- ローカル同期を見送る場合を除き、CIとリリース以外の工程が失敗したら、完了した外部の状態を保ったまま、失敗した工程、現在の外部の状態、runのURL、再開に必要な情報を報告して停止する。
  PRの作成やマージの再試行、auto-mergeの有効化、自動修復、自動ロールバックはしない。
- CIかリリースのrunが失敗したら、run全体の終了状態と失敗のログを確かめる。
  原因は`agent-basics:bugfix/references/ci-failures.md`「分類」と`agent-toolkit:atk-mode/references/ci.md`で分類する。
  ネットワーク障害、配布元のタイムアウト、レート制限、ランナー障害などの一時的な外因が疑われる時は、同資料に従って広域の障害を確かめたうえで、同じ原因について失敗したジョブを1回だけ再実行して検収を続ける。
- 再実行でも失敗した場合、初回のログで一時的な外因が疑われない場合、ログを取得できない場合は、元のrunの失敗を保ったまま上と同じ内容を報告して停止する。
- 必須CIの結果の所要時間の判定は`.claude/skills/develop-dotfiles/references/verification.md`「テストとCIの所要基準」に従う。

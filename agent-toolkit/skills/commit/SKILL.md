---
name: commit
user-invocable: false
description: >
  git commit作業（通常commit・amend・fixup）に着手する直前、
  またはコミットメッセージ案（計画ファイル・PR説明など）を書く時点で起動する。
# 編集時の注意点:
# コミット境界判断のSSOTは本ファイル「通常commit」節。
# 本文中の判断分岐・規範削除・機能的な規範変更はchoreへ分類しない。
---

# コミット運用とコミットメッセージ

本スキルはgit commitの操作手順とコミットメッセージの記述規約を提供する。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 本スキルを起動した時点 | `references/git-identifier.md` |
| 公開工程に着手する前 | `references/publish.md` |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `references/history-rewrite.md` |
| 実際にpushする前、またはリリース操作に着手する前 | `references/push-and-ci.md` |
| 実行工程（`exec.subagent.md`の手順）のcommit履歴を扱う前 | `${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md` |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `references/message.md` |
| 一時ブランチ、`git stash`の退避または別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `references/stash-cleanup.md` |

push後のCI失敗は`agent-toolkit:bugfix`を起動し、同スキルの`references/ci-failure-handling.md`で原因を分析する。

## 検証

全コミットの完了後に計画全体を対象に変更範囲を検証し、レビューへ進む。

## 通常commit

コミット数はセッションや計画ごとに固定せず、変更目的、依存関係、レビュー、revert時の理解しやすさを
基準に境界を決める。計画の想定commit単位がある場合はその単位を起点とし、各単位で実装、変更範囲の検証、
差分確認、commitを反復する。各中間`HEAD`も、その時点の公開契約とテストを満たす状態にする（努力目標。bisectとrevertで各commitを単独で扱えるようにするため）。
`agent-toolkit/rules/01-agent.md`が定める付帯作業は、関連する開発のcommitへ含めた場合も、関連する開発と同じ変更目的として扱う。

コミットはメインが所有する。サブエージェントは明示的に許可された場合、または`exec.subagent.md`の正規手順として
指定された場合だけcommitできる。`git push`は委譲先へ明示された場合を除き委譲元が所有する。

別セッションが同一リポジトリへコミットしうる前提で作業する。
コミットの前にベースコミットを再確認する（努力目標）。
再確認したベースコミットのOIDが記録時と異なる場合の続行可否は、`agent-toolkit/rules/02-agent-operations.md`「作業中に観測したリポジトリ状態変化の扱い」節で判定する。
ステージする対象は自セッションの担当範囲に限り、ユーザーの未コミット変更はそのまま残す。
他セッションの未完成の変更を巻き込むとコミットが技術的に成立しない。

commit直前に次を実施する。

1. `git status --short`、`git diff --cached`、`git diff`で現在の単位だけがstage済みであることを確認する
2. 計画記載の目的およびファイル群別の変更説明と、実装中に目的への帰属と必要性を確認した追加変更を、差分と特徴的な文字列で確認する
3. format、lint、testの結果と警告ゼロを確認する。対象プロジェクトの規範が検証の関門をCIと定め、ローカル検証の完了を後続工程の条件から外すと宣言している場合は、CIが無条件に実行するチェックを本項の対象から外し、CIが実行しないチェックだけを確認する
4. 競合解決後は`git grep -nE '^(<{7}|={7}|>{7})( |$)' -- .`で競合マーカーが無いことを確認する
5. 本文またはtrailerを持つコミットメッセージでは、第2行が空行であることを確認する。件名だけのメッセージでは本項を適用しない

commit時に本来実行されるGit hookまたはhook管理ツール内の対象チェックを省略・迂回する操作には、同じ適用条件を課す。対象には`--no-verify`と`-n`、必要なhookが実行されなくなる`core.hooksPath`の指定（`-c core.hooksPath=/dev/null`など）、`SKIP=<hook名>`などの環境変数による対象チェックの省略を含む。適用条件はpre-commitのstashが競合し、かつ対象ファイルを正式な手順で検証済みであることとする。検証省略やhook失敗の回避は、この適用条件に含めない。プロジェクト規範が個別に認める`SKIP=<hook名>`の運用はその規範に従う。条件を満たして迂回した場合は、その手段と理由をcommit後の報告へ記す。

## WI実装commitの対応

WIを入力に持つ実装commitとAWIの対応は、計画の進捗ログ（計画なしでは引き継ぎ記録）だけへ構造化して残し、記録・取得・履歴変更の各操作を本節の手段で行う。各工程の文書は、操作する時点、使う記録とworktree、工程固有の入力だけを持ち、手順は本節を参照する。

記録: 実装commitの作成前に対応AWI集合と現在のHEADの完全OIDを取得する。`git commit`を単独で実行して終了コード0を確認し、成功後に新しいHEADの完全OIDを取得する。記録へ進むのはcommitが成功した場合に限る。通常実装・レビュー修正・CI修正の各commitで、作成前HEAD、新HEAD、worktreeと全対応AWIを`atk run-script plan-progress`で計画（計画なしでは引き継ぎ記録）へ記録する。各値を渡すオプションと計画なしの場合の指定は同コマンドの`--help`に従い、計画とworktreeは絶対パス、AWIはファイル名で渡す。

取得: 終端担当などAWIを終端する主体は、`atk run-script plan-commits -- <記録> --worktree <worktree> --awi <AWI>`へ同じ記録と対象を渡す。記録とworktreeは絶対パスを使い、JSON Linesの`awi`・`commits`から現在の完全OID集合を取得する。保存で消えた作業中の計画パスも、同名の保存済み計画が一意なら読取りに使える。候補が複数ある場合は保存済み計画の実在パスを指定する。計画なしでは同じ`--handoff`・`--allowed-awi`を使う。取得した完全OIDは単一の`atk wi adopt --commit`へ渡し、複数commitでは全対応を`--note-file`へ記録する。記録の欠落・対象外AWI・Gitで解決できないOIDは生成側が補完してから再取得し、説明文や件名から対応を推定しない。

履歴変更: rebase・autosquash・amendでWI実装commitのOIDが変わった場合は、`references/history-rewrite.md`「WI実装commitの対応の継承」に従い、旧完全OIDから新完全OIDへの対応を`--rewrite-map`で同じ記録へ追記する。対応表へ入れる旧OIDは、記録済みの対応を持つものに限る。`git range-diff`などで検収した全commitのうちWI対応を持たないcommitを入れると、追記が失敗する。記録済みの旧OIDが対応表に無いと、取得時に現在のHEADにないOIDとして失敗する。

対象外: 実装差分なしの充足済み、WIと無関係なcommit、回答だけのUWIはこの対応記録の対象外とし、計画の進捗ログまたは引き継ぎ記録に残した根拠を使い、commitの指定を省く。公開commitのメッセージへWI識別子と内部の認可の出所を書かず、それらは同じ記録へ残す。計画は`atk plans commit`で保存する。

## 作業用ブランチと退避物の削除

退避・バックアップ・実験などのために作成した一時ブランチ、`git stash`による退避、別パスへの複製は、その目的を達した時点で削除する（努力目標。退避物の蓄積で後続の判別を妨げないため）。削除前の帰属と反映の確認、委譲先が開示した退避物の処置および保持する場合の記録は、読込表の`references/stash-cleanup.md`が定める。

## コミットメッセージとリリース

コミットメッセージは、件名の形式、type、description、本文と`Co-Authored-By:`を読込表の`references/message.md`に従って書く。計画ファイルやPR説明へコミットメッセージ案を書く場合も同じ基準を適用する。

---
name: commit
user-invocable: false
description: >
  git commit作業（通常commit・amend・fixup）に着手する直前、
  push、CI確認、公開工程、リリースに着手する前、
  またはコミットメッセージ案（計画ファイル・PR説明など）を書く時点で起動する。
# 編集時の注意点:
# コミット境界判断のSSOTは本ファイル「通常commit」節。
# 本文中の判断分岐・規範削除・機能的な規範変更はchoreへ分類しない。
---

# コミット運用とコミットメッセージ

履歴の変更と公開にはGit、対象リポジトリおよび必要な公開先を操作できるCLIが必要である。Gitを操作できない環境では、提示された差分からメッセージ案と確認する条件を返し、commit・push・CI監視は機能を提供するClaude CodeまたはCodexで行う。メッセージ案には[文章の品質基準](references/quality-standards.md)を使う。

git commitの運用とコミットメッセージの規約を定める。最初に`references/git-identifier.md`を全文読む。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 本スキルを起動した時点 | `references/git-identifier.md` |
| 公開工程またはリリース操作に着手する前 | `references/publish.md` |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `references/history-rewrite.md` |
| 実際にpushする前 | `references/push-and-ci.md` |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `references/message.md` |
| 一時ブランチ、`git stash`の退避または別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `references/stash-cleanup.md` |

push後のCI失敗は`agent-toolkit:bugfix`を起動し、同スキルの`references/ci-failure-handling.md`で原因を分析する。

## 検証

全コミットの完了後に計画全体を対象に変更範囲を検証し、レビューへ進む。

## 通常commit

コミットの境界は変更目的、依存関係、レビューとrevertのしやすさで決め、計画に想定commit単位があればそれを起点にする。各中間`HEAD`もその時点の公開契約とテストを満たす状態にする（努力目標。bisectとrevertで各commitを単独で扱えるようにするため）。
`agent-toolkit/rules/01-agent.md`が定める付帯作業は、関連する開発のcommitへ含めた場合も関連する開発と同じ変更目的として扱う。

コミットはメインが所有する。サブエージェントは明示的に許可された場合か`exec.subagent.md`の正規手順の場合だけcommitでき、`git push`は委譲先へ明示された場合を除き委譲元が所有する。

別セッションが同じリポジトリへコミットしうる前提で、自セッションの担当範囲だけをstageし、ユーザーや他セッションの未コミット変更は残す。commit前にベースコミットが記録時から変わっていた場合の続行可否は`agent-toolkit/rules/02-agent-operations.md`「作業中に観測したリポジトリ状態変化の扱い」で判定する。

commit前に、現在の単位だけがstageされ意図した変更を含むこと、format・lint・testが警告なく通ること、競合解決後は競合マーカー（`git grep -nE '^(<{7}|={7}|>{7})( |$)' -- .`）が無いことを確かめる。対象プロジェクトの規範が検証の関門をCIと定めている場合は、CIが無条件に実行するチェックをローカルの確認から外す。
解除条件付きの暫定回避を含むcommitでは、`agent-toolkit:bugfix`「対策の選定」が求める保存済みAWIに正式対応・解除条件・復帰後の結果と、時間経過・外部公開待ちの冷却期限が残っていることを確かめ、不足は同じWI投入手順で保存してからcommitする。

commit時のGit hookやhook管理ツールのチェックを省略・迂回する操作は、pre-commitのstashが競合し、かつ対象ファイルを正式な手順で検証済みの場合に限る。
対象は`--no-verify`・`-n`、`-c core.hooksPath=/dev/null`など必要なhookを外す指定、`SKIP=<hook名>`である。検証省略やhook失敗の回避には使わない。プロジェクト規範が個別に認める`SKIP=<hook名>`はその規範に従う。迂回した場合は手段と理由をcommit後の報告へ記す。

## WI実装commitの対応

WIを入力に持つ実装commitとAWIの対応は、計画（計画なしでは引き継ぎ記録）と同じディレクトリ・同じstemの対応記録ファイル`<stem>.wi-commits.jsonl`だけへ構造化して残す。書くのは`atk run-script plan-progress`と`atk run-script plan-rewrite`、読むのは`atk run-script plan-commits`である。commitは一意な長さの短縮OIDで表し、入力には短縮OIDと完全OIDのどちらも渡せる。各工程の文書は操作の時点、使う記録とworktree、工程固有の入力だけを持ち、手順は本節を参照する。オプションの詳細は各コマンドの`--help`に従い、計画・引き継ぎ記録とworktreeは絶対パス、AWIはファイル名で渡す。

記録: 実装commitの作成前に対応AWI集合と現在のHEADを取得し、`git commit`を単独で実行して成功した場合だけ、作成前HEAD、新HEAD、worktreeと全対応AWIを`atk run-script plan-progress`へ渡す。引き継ぎ記録では`--handoff`と、その記録の対象AWI全件の`--allowed-awi`も渡す。通常実装・レビュー修正・CI修正のcommitのいずれも記録する。cherry-pickやfast-forwardなど1回の操作で複数commitを加えた場合は操作前後のHEADで一括記録できる。範囲内のcommitが異なるAWI集合に対応するなら取り込みをAWI集合ごとに分ける。マージcommitを含む範囲は記録できないため、マージを使わずに取り込む。

取得: AWIを終端するエージェントは次の形で呼び、出力のJSON Linesの`awi`・`commits`から現在のcommit集合を短縮OIDで取得する。

```sh
atk run-script plan-commits -- <計画Markdownの絶対パス> --worktree <対象worktreeの絶対パス>
atk run-script plan-commits -- <引き継ぎ記録Markdownの絶対パス> --worktree <対象worktreeの絶対パス> --handoff --allowed-awi <記録の対象AWI全件を反復指定>
```

取得対象を絞る場合は`--awi <AWIファイル名>`を反復する。省略時は計画の関連WI全件（引き継ぎ記録では`--allowed-awi`全件）を取得する。同じstemのJSONLは保存先であり、位置引数には渡さない。保存で消えた作業中の計画パスも、同名の保存済み計画が一意なら使え、複数あれば保存済み計画の実在パスを指定する。取得した短縮OIDは単一の`atk wi adopt --commit`へ渡し、複数commitでは全対応を`--note-file`へ記録する。実装差分なしの項目に記録が無ければ、その項目を除いた対象を`--awi`で指定する。実装commitを持つ項目の記録の欠落・対象外AWI・解決できないOIDは生成側が補完してから再取得し、説明文や件名から対応を推定しない。

履歴変更: WI実装commitのOIDが履歴書換えやcommit分割で変わった場合は、`references/history-rewrite.md`「WI実装commitの対応の継承」に従う。
検収した対応表と書換え前のHEADを、書換え範囲にWI実装commitを持つ全ての計画と引き継ぎ記録とともに1回の`atk run-script plan-rewrite`へ渡す。不足があれば同コマンドはどの記録も変えずに失敗する。

対象外: 実装差分なしの充足済み、WIと無関係なcommit、回答だけのUWIは対応記録の対象外とし、進捗ログか引き継ぎ記録の根拠を使ってcommitの指定を省く。公開commitのメッセージへWI識別子と内部の認可の出所を書かず、それらは同じ記録へ残す。計画は対応記録ファイルと共に`atk plans commit`で保存する。

## コミットメッセージ

件名の形式、type、description、本文と`Co-Authored-By:`は`references/message.md`に従う。計画ファイルやPR説明へ書くコミットメッセージ案にも同じ基準を適用する。

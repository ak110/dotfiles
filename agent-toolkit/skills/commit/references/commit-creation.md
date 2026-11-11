# コミット：通常commit

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:writing-standards/references/investigation-sources.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-sources.md` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` | |
| 保存済みの検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` | |
| 未コミット検証の実行前とcommit後の適用確認前 | `../../../share/verification-evidence-observation.md` | 判断 | `read:agent-toolkit:*/share/verification-evidence-observation.md` | |
| 取得版の異なる結果をレビューへ渡す前 | `../../../share/exec-review-recording-grounds.md` | 判断 | `read:agent-toolkit:*/share/exec-review-recording-grounds.md` | |
| Git識別子・worktree・履歴・公開済みの状態を判定または操作する前 | `git-identifier.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/git-identifier.md` | |
| 公開工程またはリリース操作に着手する前 | `publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `history-rewrite.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite.md` | |
| 実際にpushする前 | `push-and-ci.md` | 操作 | `bash:git push` | |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `message.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/message.md` | |
| 既存未コミット差分を不採用として破棄する前 | `stash-cleanup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/stash-cleanup.md` | |
| 一時ブランチ、`git stash`の退避、hookの退避ファイルまたは別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `stash-cleanup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/stash-cleanup.md` | |
| CI失敗を扱う前 | `agent-toolkit:commit/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md` | |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `agent-toolkit:commit/references/design-time.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/design-time.md` | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `agent-toolkit:commit/references/testing.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/testing.md` | |

## 通常commit

**4.** 安全にレビュー・revertできる単位を保つため、変更目的、依存関係、レビュー・revertのしやすさで分割を決める。
計画にcommit単位があれば基準にする。
各コミットの中間状態（中間`HEAD`）でも、その時点の公開契約とテストを満たすように整える（努力目標。git bisectやrevertで各コミットを単独で安全に扱えるようにするため）。

付帯作業を関連する開発のコミットに含める場合も、その開発作業と同じ変更目的として扱う。

**5.** commit権限はメインが持つ。
サブエージェントは明示的な許可か`exec.subagent.md`の正規手順がある場合に限りcommitする。
`git push`は委譲先への明示指示がある場合を除き、委譲元が行う。

**6.** WI実装はcommit前に`commit-wi-records.md`「WI実装commitの対応」の手順1を行う。
他セッションの成果を守るため、自身の担当範囲だけをステージングし、ユーザーや他セッションの未コミット変更を残す。
commit直前に開始時からベースcommitが変わっていれば、`agent-toolkit/share/rules/common/02-agent-operations.md`「作業中に観測したリポジトリ状態変化の扱い」に従う。

**7.** 保存対象を確かめるため、commit前に対象変更だけのステージングとformat・lint・testの警告なし成功を確認する。
マージの競合を解決したあとは、競合マーカー（`git grep -nE '^(<{7}|={7}|>{7})( |$)' -- .`）が残っていないことを確認する。
プロジェクトの規範でCIを主要な検証関門と定めている場合は、CIが無条件に実行するチェックをローカルの事前確認から省いてよい。

保存する内容は、`agent-basics:writing-standards/references/investigation-sources.md`「根拠と主張の一致」に従い、要求・計画から今回保存すべき担当変更を先に確定する。
その集合とステージ済み実差分を比べ、担当外の混入と担当変更の欠落を判定する。
担当外の未ステージ差分を保護し、不足する担当差分だけを追加して再比較する。
status・diffの表示やhook成功の確認とは別に、保存対象とステージ済み実差分を比べる。
解除条件付きの暫定対応を含むコミットでは、`agent-toolkit:wi-standards/references/analysis-records.md`「暫定回避と結果報告」に従い、保存済みAWIに正式な対応内容、解除条件、復帰後の期待結果、および待機期限が記録されているかを確認する。
不足がある場合は、通常のWI登録手順でそれらを記録してからコミットする。

**8.** フック（Git hookやhook管理ツール）のチェック迂回は、pre-commitの退避（stash）が競合し、対象ファイルが所定の手順で検証済みの場合に限り認める。

該当する操作には、`--no-verify`、`-n`、`-c core.hooksPath=/dev/null`などのフック無効化オプションや、環境変数`SKIP=<hook名>`の指定が含まれる。
通常の検証を省略したり、フックの失敗を安易に回避したりする目的では使用しない。
ただし、プロジェクト規範で個別に許可されている`SKIP=<hook名>`はその定めに従う。
フックを迂回した場合は、その手段と理由をコミット完了後の報告に明記する。

**9.** 「hookの打ち切りと退避の復元」に従ってcommitを実行し、成功後は`commit-verification.md`「検証」の最終内容への適用を確認し、WI実装は対応を記録する。
push後のCI失敗は`agent-basics:bugfix`を起動し、同スキルの`ci-failure-handling.md`で原因を分析する。

### hookの打ち切りと退避の復元

1. POSIXのホストでcommit hookを伴う`git commit`は、`atk run-script commit-with-timeout -- --timeout <秒> -- <git commitの引数>`で時間上限を設けて実行する。
   `--timeout`は実行枠の順番待ちを含むコマンド全体の上限であり、ホストがコマンドを止める時間上限より先に達する値にする。
   Claude Codeでは`Bash`の`run_in_background: true`で起動し、`Bash`の`timeout`を`--timeout`より長く、背景実行の最大値（7,200,000ミリ秒）以下にする。
   前景で実行する場合は、`--timeout`を前景の`timeout`の最大値（600,000ミリ秒）より短くする。
   Claude Code以外のホストは、ホストの時間上限の値を確かめていないため、`Bash`の入力に関するこの指定の対象外とする。
   hook管理ツールのprekはhookの実行中に未ステージの変更を一時ファイルへ退避し、通常終了とSIGINTでは作業ツリーへ戻すが、SIGTERM・SIGKILLやホストの時間上限による強制終了では戻さない（prek 0.5.7で確認）。
   起動側は所有する子孫へSIGINTを送り、退避復元の猶予と残存回収を終えてから、時間切れは124、明示中断は130を返す。
   実行枠を待ったまま上限に達した場合も124を返し、標準エラーへ「commitは開始していない」と出力する。
   このとき退避は行われていない。
   親だけが終了しても回収完了とは扱わず、残存を示す診断があれば対象の識別情報から処置する。
   hookが長引いた原因と退避の復元を確かめてからやり直す。
   Windowsのホストは同等の手段の挙動を確認していないため、この手順の対象外とする。
   観測条件と再検証は`docs/development/audit-records.md`にある。
   参照する節は「agent-toolkit/skills/commit/references/commit-creation.md：hookの打ち切りと退避の復元：2026年11月10日」と「agent-toolkit/skills/commit/references/commit-creation.md：hookの打ち切りと退避の復元：2026年11月11日」である。

2. commitが失敗・中断した出力に`Unstaged changes detected. Temporarily saving them to <パス>`がある場合は、他の操作より先に退避が作業ツリーへ戻ったかを確かめる。
   SIGINTの打ち切りではcommitの終了後にprekが退避を戻すことがあり、`Restored unstaged changes from <パス>`の行の有無だけでは判定できない。
   prekのプロセスが終了してから、作業ツリーと退避ファイル（`~/.cache/prek/patches/`、`PREK_HOME`の指定があればその配下）を比べる。
   比較には`git diff --binary --src-prefix=a/ --dst-prefix=b/`の出力と、退避ファイルから先頭の`#`で始まる注記行を除いた本文を使う（`diff.noprefix`の設定によらず同じ形式で比べるため）。
   一致しなければ退避は戻っておらず、`git apply <退避ファイル>`で復元し、同じ比較で一致することを確かめる。
   復元後の退避ファイルは`stash-cleanup.md`のhookの退避ファイルの項に従って扱う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| バグ対応の基準を適用する前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 編集時の注意点:：検証の基準を適用する前 | `commit-verification.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-verification.md` |
| 編集時の注意点:：WI実装commitの対応の基準を適用する前 | `commit-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/commit-wi-records.md` |

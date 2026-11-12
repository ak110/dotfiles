# コミット：commitの実行前確認

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:writing-standards/references/investigation-sources.md` |

## commitの実行前確認

**1.** 保存対象を確かめるため、commit前に対象変更だけのステージングとformat・lint・testの警告なし成功を確認する。
マージの競合を解決したあとは、競合マーカー（`git grep -nE '^(<{7}|={7}|>{7})( |$)' -- .`）が残っていないことを確認する。
プロジェクトの規範でCIを主要な検証関門と定めている場合は、CIが無条件に実行するチェックをローカルの事前確認から省いてよい。

保存する内容は、`agent-basics:writing-standards/references/investigation-sources.md`「根拠と主張の一致」に従い、要求・計画から今回保存すべき担当変更を先に確定する。
その集合とステージ済み実差分を比べ、担当外の混入と担当変更の欠落を判定する。
担当外の未ステージ差分を保護し、不足する担当差分だけを追加して再比較する。
status・diffの表示やhook成功の確認とは別に、保存対象とステージ済み実差分を比べる。
解除条件付きの暫定対応を含むコミットでは、`agent-toolkit:wi-standards/references/analysis-records.md`「暫定回避と結果報告」に従い、保存済みAWIに正式な対応内容、解除条件、復帰後の期待結果、および待機期限が記録されているかを確認する。
不足がある場合は、通常のWI登録手順でそれらを記録してからコミットする。

**2.** フック（Git hookやhook管理ツール）のチェック迂回は、pre-commitの退避（stash）が競合し、対象ファイルが所定の手順で検証済みの場合に限り認める。

該当する操作には、`--no-verify`、`-n`、`-c core.hooksPath=/dev/null`などのフック無効化オプションや、環境変数`SKIP=<hook名>`の指定が含まれる。
通常の検証を省略したり、フックの失敗を安易に回避したりする目的では使用しない。
ただし、プロジェクト規範で個別に許可されている`SKIP=<hook名>`はその定めに従う。
フックを迂回した場合は、その手段と理由をコミット完了後の報告に明記する。

**3.** 「hookの打ち切りと退避の復元」に従ってcommitを実行し、成功後は`commit-verification.md`「検証」の最終内容への適用を確認し、WI実装は対応を記録する。
push後のCI失敗は`agent-basics:bugfix`を起動し、同スキルの`references/ci-failure-handling.md`で原因を分析する。

### hookの打ち切りと退避の復元

1. POSIXのホストでcommit hookを伴う`git commit`は、`atk run-script commit-with-timeout -- --timeout <秒> -- <git commitの引数>`で時間上限を設けて実行する。
   このコマンドが`git commit`を補うため、`--`の後には`commit`を書かず引数だけを渡す（例: `atk run-script commit-with-timeout -- --timeout 560 -- -m '<本文>'`）。
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

| 時点または条件 | 全文読む資料 |
| --- | --- |
| バグ対応の基準を適用する前 | `agent-basics:bugfix/SKILL.md` |
| 検証の基準を適用する前 | `commit-verification.md` |
| WI実装commitの対応の基準を適用する前 | `commit-wi-records.md` |

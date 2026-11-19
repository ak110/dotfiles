# commitの作成

通常のcommitを作り、WIとの対応を記録するまでの手順書である。
方針: commitは履歴を書き換えないと取り消せず、担当外の未commitの差分を巻き込むと他者の成果が失われる。
このため、保存する範囲を確かめてから作る（原則4、`agent-basics:writing-standards/references/quality-principles-constraints.md`）。
成功は、commitの終了コードと保存後の差分で確かめる（原則3、`agent-basics:writing-standards/references/quality-principles-facts.md`）。

## 作業の要否と読む範囲

該当する条件の行だけを実行する。

| 条件 | 行動 |
| --- | --- |
| 通常のcommitを作る（mergeの競合解消、cherry-pick、revertを含む） | 本書「通常commit」から上から実行する |
| amend、fixup、autosquash、rebase、push済みかの判定 | `agent-toolkit:atk-mode/playbooks/history-rewrite.md`を上から実行する |
| push（候補branch、作業branch、履歴を書き換えるpushを含む）、CIの確認、公開、リリース | 本書を適用してから`agent-toolkit:atk-mode/playbooks/publish.md`を上から実行する |
| CIの失敗を扱う | `agent-toolkit:atk-mode/references/ci.md` |
| commitの前の検証 | 本書「commitとフックの検証」 |
| コミットメッセージを書く（計画やPRの説明へ書くメッセージ案を含む） | `agent-toolkit:atk-mode/references/commit-message.md` |
| WIを実装したcommitを作る、取り込む、完了を記録する | `agent-toolkit:atk-mode/references/commit-wi-records.md` |
| Git識別子、worktree、別のリポジトリを指定して操作する | `agent-toolkit:atk-mode/references/git-identifier.md` |
| 既存の未commitの差分を不採用として捨てる、一時ブランチや退避物を消す、委譲先の報告が退避の識別子か複製のパスを示した | 本書「作業用ブランチと退避物の扱い」 |
| Gitを操作できない環境 | 差分からメッセージ案と確認の条件を返し、commit、push、CIの監視はホストの機能で行う |

## 通常commit

commitの単位の判断は本節を定義元とし、計画と他の手順書は本節を指す。

1. 分け方を決める。
   安全にレビューとrevertができる単位を保つため、変更の目的、依存関係、レビューとrevertのしやすさで分ける。
   計画にcommitの単位があればそれを基準にする。
   単位はWIの区切りと切り離して決め、同じファイル群へ一体で適用し同じ検証で確かめる変更は、複数のWIにまたがっても1つのcommitにまとめてよい。
   WIごとの採否とcommitの対応は`agent-toolkit:atk-mode/references/commit-wi-records.md`「WI実装commitの対応」の記録で保つ。
   変更から生じた付帯作業は、関連する開発と同じ計画、WI、commitで完了し、その開発と同じ変更の目的として扱う。
   各commitの中間の状態でも、その時点の公開契約とテストを満たすように整える（努力目標。git bisectとrevertで各commitを単独で扱えるようにするため）。
2. commitする主体を確かめる。
   commitの権限はメインが持つ。
   サブエージェントは、明示的な許可か`agent-toolkit:/share/exec.subagent.md`の手順がある時だけcommitする。
   `git push`は、委譲先へ明示的に指示した場合を除いて委譲元が行う。
3. 本書「commitの実行前確認」を行う。
4. メッセージを`agent-toolkit:atk-mode/references/commit-message.md`に従って書き、本書「hookの打ち切りと退避の復元」の形でcommitする。
5. 成功したら、検証が最終の内容に当たっていることを本書「commitとフックの検証」で確かめる。
   WIの実装なら、`agent-toolkit:atk-mode/references/commit-wi-records.md`「WI実装commitの対応」の手順1で対応を記録する。

## commitの実行前確認

1. 保存する範囲を確定する。
   `agent-basics:writing-standards/references/investigation.md`「根拠と主張の一致」に従い、要求と計画から今回保存する担当の変更を先に決める。
   ステージ済みの実差分（`git diff --cached`）と比べて、担当外の混入と担当の変更の欠落を判定する。
   `git status`の表示とhookの成功は、この比較の代わりにならない。
   ユーザーと他の担当の未commitの差分はステージせずに残し、足りない担当の差分だけを加えて比べ直す。
2. 担当外の差分を失う操作を避ける。
   作業ツリーかindexを戻す操作（`git reset --hard`、`git checkout -- <パス>`、`git restore <パス>`、`git clean`、`git stash`）は、対象に担当外の未commitの差分が無いことを`git status --short`で確かめ、パスを限って行う。
   担当外の差分を捨てるのは、本書「作業用ブランチと退避物の扱い」の条件を満たした時だけにする。
   commitの直前にベースcommitが開始時から変わっていたら、担当ファイルと直接影響範囲に及ぶ差分だけを読み、影響が無ければ今の状態で続ける。
3. 検査が警告なしで成功することを確かめる。
   対象の変更に対してformat、lint、testを実行する。
   プロジェクトの規範がCIを主な検証の関門と定めている場合は、CIが無条件に実行する検査を手元の確認から省いてよい。
   mergeの競合を解消した後は、`git grep -nE '^(<{7}|={7}|>{7})( |$)' -- .`で競合の印が残っていないことを確かめる。
4. 解除の条件付きの暫定対応を含むcommitでは、保存済みのAWIに正式な対応、解除の条件、復帰後の期待結果、待機の期限があることを`agent-toolkit:atk-mode/references/analysis-records.md`「暫定回避と結果報告」で確かめる。
   足りなければ通常の投入手順で記録してからcommitする。
5. WIの実装なら、`agent-toolkit:atk-mode/references/commit-wi-records.md`「WI実装commitの対応」の手順1で、対応するAWIの集合と今のHEADを取得する。
6. hookを迂回しない。
   `--no-verify`、`-n`、`-c core.hooksPath=/dev/null`、`SKIP=<hook名>`などでhookを外すのは、pre-commitの退避が競合し、対象のファイルを所定の手順で検証済みの場合に限る。
   プロジェクトの規範が個別に許した`SKIP=<hook名>`はその定めに従う。
   迂回した場合は、手段と理由をcommitの後の報告に書く。

### hookの打ち切りと退避の復元

1. POSIXのホストでhookを伴う`git commit`は、`atk run-script commit-with-timeout -- --timeout <秒> -- <git commitの引数>`で時間の上限を設けて実行する。
   `--`の後には`commit`を書かず、引数だけを渡す（例: `atk run-script commit-with-timeout -- --timeout 560 -- -m '<本文>'`）。
   `--timeout`は実行枠の順番待ちを含むコマンド全体の上限であり、ホストがコマンドを止める上限より先に達する値にする。
   Claude Codeでは`Bash`の`run_in_background: true`で起動し、`Bash`の`timeout`を`--timeout`より長く、背景実行の最大値（7,200,000ミリ秒）以下にする。
   前景で実行する場合は、`--timeout`を前景の`timeout`の最大値（600,000ミリ秒）より短くする。
   Claude Code以外のホストは上限の値を確かめていないため、`Bash`の入力に関するこの指定から外す。
   Windowsのホストは同等の手段の挙動を確かめていないため、本節の手順の対象外とする。
2. 強制終了で止めない。
   hook管理ツールのprekはhookの実行中に未ステージの変更を一時ファイルへ退避し、通常の終了とSIGINTでは作業ツリーへ戻すが、SIGTERM・SIGKILLやホストの時間上限による強制終了では戻さない（prek 0.5.7で確認）。
   `commit-with-timeout`は所有する子孫へSIGINTを送り、退避の復元の猶予と残存の回収を終えてから、時間切れでは124、明示の中断では130を返す。
   実行枠を待ったまま上限に達した場合も124を返し、標準エラーへ「commitは開始していない」と出す（この時は退避されていない）。
   親だけが終わっても回収の完了とは扱わず、残存を示す診断があれば対象の識別情報から処置する。
   hookが長引いた原因と退避の復元を確かめてからやり直す。
3. 退避が戻ったかを確かめる。
   commitが失敗か中断した出力に`Unstaged changes detected. Temporarily saving them to <パス>`がある場合は、他の操作より先に確かめる。
   SIGINTの打ち切りではcommitの終了後にprekが退避を戻すことがあり、`Restored unstaged changes from <パス>`の行の有無では判定できない。
   prekのプロセスが終わってから、`git diff --binary --src-prefix=a/ --dst-prefix=b/`の出力と、退避ファイルから先頭が`#`の注記行を除いた本文を比べる。
   退避ファイルは`~/.cache/prek/patches/`（`PREK_HOME`の指定があればその配下）にあり、接頭辞を指定するのは`diff.noprefix`の設定によらず同じ形式で比べるためである。
   一致しなければ退避は戻っておらず、`git apply <退避ファイル>`で復元し、同じ比較で一致を確かめる。
   復元した退避ファイルは本書「作業用ブランチと退避物の扱い」に従って消す。

観測の条件と再検証は`docs/development/audit-records-commit-timeout.md`の次の節にある。

- 「agent-toolkit/skills/atk-mode/playbooks/commit.md：hookの打ち切りと退避の復元：2026年11月10日」
- 「agent-toolkit/skills/atk-mode/playbooks/commit.md：hookの打ち切りと退避の復元：2026年11月11日」

## commitとフックの検証

初回の検証を選ぶ時は、commitフックが実行するチェックと対象集合も含めて比べ、同じ条件の検証をフックの前後に重ねない。
フックの保存出力で計画の検証を省く・置き換えるのは、実行したツールごとの名前・対象ファイル・終了状態を観測し、計画のコマンドの分を含むと確かめた場合に限る。
フック単位の`pyfltr....Passed`のような集約表示や「lint」という種別名は、元のツールと対象の集合を含んだことの根拠として不足する。
pyfltrでは、`pyfltr list-runs`の各runの`commands`と`files`、`pyfltr show-run <run_id>`のツール別の結果で確かめ、対象件数に加えて実行時の対象指定も対応付ける。
計画のツール集合、フックの実行ツール集合、対象ファイルの対応、不足分を検証記録へ残し、不足するツールだけを別に実行する。
commitとフックの後は、保存した本文と最終の内容を比べ、フックの更新が影響する成功結果は取り直す。
レビューへ出す最終結果は、全てのcommitとフックの更新を終えた内容へ当てはまることと、計画全体の変更範囲を覆うことを、取得した版と対応付けて示す。

## 作業用ブランチと退避物の扱い

退避、バックアップ、実験のために作った一時ブランチ、`git stash`の退避、hookの退避ファイル、別パスへの複製は、目的を果たしたら消す（努力目標。溜まると後の作業で必要なデータを見分けにくくなるため）。
委譲先の完了報告が退避の識別子か複製のパスを示した場合は、委譲元が受領と検収の時に本節で処置する。
過去のエージェントが残した退避物は、由来、現在の利用、残すべき成果、認可を調べてから扱う（原則7、`agent-basics:writing-standards/references/quality-principles-ownership.md`）。
残す場合は、保護の対象か残るユーザーの判断と、回収の条件を記録する。

1. 着手前からあった未commitの差分を不採用として捨てる前は、復元できる退避を確保する。
   並行するworktreeでは`atk worktree-stash save --label <ラベル>`を使い、mergeの進行中は本書「merge進行中の退避」の方法を使う。
   private-notesでは退避も差分の破棄もしない。
2. 退避物を消す前に、採用した変更か不採用の変更かを決め、採用した変更は統合先へ反映済みであることを種類ごとに確かめる。
   - 一時ブランチ: `git cherry -v <統合先> <対象ブランチ>`と`git diff --diff-filter=A --name-only <統合先>..<対象ブランチ>`で、同等の変更が統合先に全て入り、対象だけにあるファイルが無いことを確かめる。
     cherry-pickか履歴の整理を経た統合先ではpatch-idが一致しないことがあるため、変更したファイル群を`git diff <対象ブランチ> <統合先> -- <files>`で比べる。
   - `git stash`とworktree固有の退避（`refs/worktree/<ラベル>`）: `git stash show --include-untracked -p <識別子>`で追跡ファイルと未追跡ファイルの差分を確かめ、`atk worktree-stash drop '<識別子>'`で消す。
   - 別パスへの複製: `diff`で複製元と比べる。
   - hookの退避ファイル: 本書「hookの打ち切りと退避の復元」の手順3で復元し、同じ比較で作業ツリーの差分と一致することを確かめてから消す。
     一致しないか復元できない場合は消さずに残し、差分と退避ファイルの所在を報告する。

### merge進行中の退避

`git merge`の進行中は、差分を別パスへの`cp`か別ブランチの作成で退避する。
進行中に`git stash`すると、`git stash pop`で競合の解消中の状態とmergeの文脈が戻らず、それまでの解消の結果が失われるためである。
別パスへの複製も、作業の完了後に本節の確認で回収する。

## 返す報告に含める項目

作ったcommitの短縮OIDと件名、迂回したhookとその理由、記録したWIとの対応、残した退避物とその理由、続行できない理由を返す。

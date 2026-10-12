# レーン統合タスク

計画の起草と実装を担当したレーン担当threadが、専用branchの統合、作成した計画の最終化およびAWIの終端を行う。pushは委譲元が担う。専用worktreeとbranchは統合後も追加に備えて保持し、回収は全工程・外部プロセスの終了後に所有登録から回収する通常終了とprocess-loopの子終了後の処理へ渡す。

## 読込表

次の時点または条件が成立したら、対象の操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 「マージありの統合」でrebaseが競合で停止したとき、またはrebase後の変更範囲の検証が失敗したとき | `agent-toolkit:process-wi`の[references/lane-integration-failure.md](../skills/process-wi/references/lane-integration-failure.md) |
| `マージあり`の統合を開始する時（統合時の完成条件判定を行う時） | `agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」 |
| 「マージありの統合」の検証を実行した後 | `agent-toolkit:check-execution`の`references/diagnostics.md` |

## 入力

```text
必須入力名: 統合区分,実行レビュー済みHEAD,統合先worktree,統合先branch,計画ファイル名一覧,AWI終端区分,レーン開始時刻
任意入力名: 完成条件証拠,プロジェクト固有の公開後の操作の順序
```

- `実行レビュー済みHEAD`: `マージあり`では統合入力HEAD（実行レビューが収束したラウンドの対象HEAD、または収束後に再レビューを省いた修正の後のHEAD）の7文字以上の一意な短縮OID。`マージなし`では`なし`
- `完成条件証拠`: `マージあり`で受領する、計画ファイル名（計画なしは`なし`）、対応AWI集合、実行レビューが返した`完成条件証拠のパス`の絶対パスを持つ順序付きJSON配列。統合開始時に全証拠を読み、採用予定AWIの集合を重複も欠落もなく覆うことを確かめ、統合時の完成条件判定を各AWIへ適用する
- `計画ファイル名一覧`: 重複のないファイル名の順序付きJSON配列。計画を作成しなかったレーンでは`[]`。引き継ぎ記録の計画集合、対応AWIと保存状態に一致するか確かめる
- `レーン開始時刻`: メインが保持した最初のレーン担当起動の時差付き時刻。欠落・不正な値はメインへ補完を求め、自身や置換後のsessionの開始時刻で補わない
- `プロジェクト固有の公開後の操作の順序`: 指定がある場合だけ、AWIごとの対象と時機

`引き継ぎ記録先`はレーン担当の起動時に受領した値を継続する。書込先は受領した統合先worktreeとする。
延期対象（判定は`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/run-lanes.md`「採否の確定と終端区分」）は統合時に終端せず、`adoptを延期したAWIとcommit`で終端担当へ渡す。版数更新の完成条件も、全レーン統合後に終端担当が版数、派生manifestおよび公開結果を検収してから`adopt`する。

## マージありの統合

1. 専用worktreeがcleanであり、受領した`実行レビュー済みHEAD`と専用branchのHEADを`git rev-parse --short=7 <revision>`で正規化した値が一致することを確かめる。判定はこの2値だけで行い、レビュー指摘管理表の内容は使わない。
2. 統合先worktreeの現在branchが統合先branchであり、別の書込主体とGitの中断状態が無いことを確かめる。統合先branchの現在HEAD（以下、統合前HEAD）と`git merge-base <専用branch> <統合先branch>`（rebase前のベースOID）を取得する。
3. 専用branchのHEADが統合前HEADの子孫なら、rebaseせず手順6へ進む。
4. 子孫でない場合はrebaseする。
   - 前提: 統合前HEADより先にある専用branchの全commitが未pushであることを`agent-toolkit:commit`の`references/history-rewrite.md`「プッシュ済み判定」で確かめる。push済みが1件でもあればrebaseせず、その短縮OIDを`続行できない理由:`で返す。
   - rebase前の専用branchのHEADを`引き継ぎ記録先`へ記録し、同書「履歴確認の起動形」の`git log`を書換えとは別の呼び出しで実行してから、専用worktreeで`git rebase <統合前HEAD>`を実行する。書き換えるのは専用branchだけとする。
   - 競合で停止したら、読込表の`lane-integration-failure.md`「rebaseの競合」に従う。
   - 成功したら次のコマンドで検収し、旧新OIDの対応表を保存する。同コマンドは文脈を除いた`git show --format= --binary --no-ext-diff --no-textconv --unified=0`の差分を`git patch-id --verbatim`で比べ、変更内容の一意な全1対1対応を確かめる。件名・本文・周辺文脈・hunk位置の変化は許容し、変更行の空白、パス・種類・モード・バイナリー内容の変化、commitの追加・欠落・曖昧な対応は拒否する。合否は`range-diff`の表示記号ではなくこの対応で決める。非0なら診断を`続行できない理由:`で返す。

   ```text
   atk run-script history-compare -- --operation rebase --work-dir <専用worktreeの絶対パス> --old-base <rebase前のベースOID> --old-head <rebase前の専用branchのHEAD> --new-base <統合前HEAD> --new-head <rebase後の専用branchのHEAD> --output <managed-temp内の対応表JSONの絶対パス>
   ```

5. rebase後のHEADで変更範囲の検証を再実行する。計画ありは`atk run-script plan-verify -- --plan <計画の絶対パス> --worktree <専用worktreeの絶対パス> --timeout <正の有限秒数>`を使う。直前に同じ入力の`--list`を確認し、実行後は保存した結果まとめの`results_path`を`atk run-command --records-file <絶対パス>`へ渡して読む。計画なしは引き継ぎ記録に確定した検証コマンドを使い、`--record <record_path>`の反復で読む。子は再実行せず、全件の取得版・Git状態・子とwrapperの終了状態・両出力を判定する。旧記録の未記録と保存・読取失敗は失敗として扱う。合否は後掲「検証結果の警告の判定」で決め、失敗は`lane-integration-failure.md`「統合後の変更範囲の検証の失敗」に従う。
6. 対象リポジトリのプロジェクト規範に統合後にだけ成立する検証があれば、統合先を変える前に専用worktreeで実行する（専用branchは統合前HEADの子孫なので、treeはfast-forward後の統合先と同じになる）。規範のコマンドを、専用worktreeを`cwd`とする`agents_server`の`start`（`mode`は`shell`）で1回実行し、標準出力と標準エラーはmanaged-temp内に確保した保存先へ全量保存させる。保存済みの全量から後掲「検証結果の警告の判定」で合否を決める。不成立なら統合先を変えず、専用worktreeで是正commitを作成し、変更範囲の検証からやり直す。
7. `git -C <統合先worktreeの絶対パス> merge --ff-only <専用branch>`でfast-forwardマージする。別の作業ツリーからの`git push`で統合先branchを更新しない（チェックアウト中のbranchへの更新は`receive.denyCurrentBranch`が拒否する）。fast-forwardできない場合はmerge commitやcherry-pickで解決せず、続行できない理由を返す。
8. マージ後の統合先branchの短縮OIDを`統合後のHEAD`とする。

rebaseした場合は、`history-compare`の対応表を`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」の履歴変更の手段の`--rewrite-map`へそのまま渡し、rebase前のHEADと統合する全ての計画または引き継ぎ記録も同じ1回で渡す。完成条件証拠と未判定検証記録の参照更新にも同じ対応表を使い、更新するのは`evidence`の`commit:<OID>`だけとする（分割の各対応先も`commit:`標識を保つ）。裸の取得版OID・HEAD・git_head・比較元、観測ファイル、判定と`reviewed_head`、既存自由文の形式はそのまま保持する。結果に作用する内容・条件の差がある行だけを再観測し、根拠の更新は両証拠CLIの保存物直接指定と`--select-row`で行う。

### 検証結果の警告の判定

検証結果は読込表の`references/diagnostics.md`で判定し、統合の基準版には統合前HEADを使う。統合に固有の扱いは次のとおり。

- 比較のための再実行は、統合先worktreeで該当コマンドだけを読み取り専用で行い、統合先の追跡ファイルとbranchを変えない
- 新規の警告であることだけで統合とAWI終端を止めない。実装で残した警告は、意味・影響・帰属・比較版・残す理由が統合後の入力でも成立することを確かめ、診断本文と結果を記録する。阻害は是正し、比較不能と意味・帰属の未確定は統合を止めて理由と不足する条件を続行できない理由として返す

## マージなしの統合

実装commitを作成せず、統合先branchを統合開始時の状態のまま保つ。統合開始時の統合先branchの短縮OIDを`統合後のHEAD`とする。
未終端項目の計画を作業領域へ残す指示も、全計画を保存したうえでセッション終了時のメインが復元する扱いとする。

## 計画最終化

受理した完成条件証拠がある計画は、そのJSONの絶対パスを`受理済みの完成条件証拠`として進捗ログへ残す（後続レビューの参照入力に使い、未判定検証記録と区別する）。

`計画ファイル名一覧`が`[]`なら`plan-check`と計画の保存を省き、後述の稼働記録、統合結果およびAWI終端前の状態を引き継ぎ記録へ残して、`保存した計画ファイル`を`[]`で返す。計画がある場合は配列の順に各計画へ次を適用する。

- 先行する統合時に保存済みで、その後に進捗を追記していない計画は操作を省き、返却には含める
- 操作場所は計画メタ情報が示す専用worktreeとする。回収済みの場合は、引き継ぎ記録が反映後の観測のみの再開と旧worktreeの回収を示し、保存時の本文から進捗ログ以外が変わっていないときだけ現在の専用worktreeで続ける。それ以外はメタ情報を書き換えず続行できない理由を返す
- `~/.claude/plans`に無い未保存の計画は保存状態と所在を引き継ぎ記録へ残し、必要なら`atk plans checkout <private-notes/plans/からの相対パス>`で取得する
- `atk run-script plan-progress --`で`## 進捗ログ`へ、統合区分、統合先branch、`統合後のHEAD`、実行レビューの収束、AWI終端前の状態、rebaseした場合は前後の短縮OIDの対応を追記する
- 同じ追記へレーンの稼働時間と予測補正記録を残す。値は`レーン開始時刻`から統合完了までの総経過秒数（担当の置換があっても起点を変えない）とし、書式は`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「進捗ログ」に従う。補正前の見込みと係数は保持した選定結果の根拠から取り、追加WIは累積予測を使い、不明なら不明と記す。同一レーンの複数計画には同じ値を記す。この記録は次回の選定がレーン配分の見込みを導く入力になる
- 専用worktreeをcwdとして`atk run-script plan-check -- --reject-migration-warnings --work-dir <計画メタ情報の対象リポジトリ> <計画ファイルの絶対パス>`を単独実行し、終了コード0と警告の不在を確かめる。観測のみの再開で本文凍結を確かめた計画は、保存時に通過済みのため省く
- `atk plans commit <計画ファイル名>`を計画ごとに単独実行し、保存した計画名と結果を直ちに引き継ぎ記録へ残す。途中で失敗したら保存済みと未保存を区別して返す。全計画の保存が終わるまで`統合完了`を返さない

## AWI行の終端

計画または引き継ぎ記録に確定した採否と、受領した終端区分を適用する。

- 採用と充足済みは`atk wi adopt`、不採用は`atk wi reject`で終端する。adoptのcommit対応付けは`agent-toolkit:wi-standards`「状態と依存」の遷移表に従い、開始時や統合時のHEADを全項目へ機械的に複製しない。複数commitのメモは`--note-file`へ記録する
- 対応commitは`agent-toolkit:commit`の`SKILL.md`「WI実装commitの対応」の取得の手段で、計画または引き継ぎ記録と同じstemの対応記録ファイルから取得し、`--worktree`へ統合後のworktreeを渡す
- commitを記録する採否操作では、統合先worktreeの絶対パスを`--target-repo`へ渡す（同節の共通前提）
- プロジェクト固有の公開後の操作の後へ延期する項目は状態を変えず、AWIとOIDの組を全件返す
- `終端しない`項目、観測のみの再開で残った項目、観測が完成条件を満たさなかった項目は状態を変えない。`混在`の項目と後二者の後続処置はメインがセッション終了工程で行う

各状態変更コマンドは単独実行し、成功の報告、対象の完全識別子および警告の不在で完了を判定する。同じ状態は報告の値をそのまま使う。

## 出力

`変更したエージェント向け文書`には統合先worktreeで`atk run-script agent-doc-changes -- <統合前HEAD> <統合後のHEAD>`を単独実行し、終了コード0で得たJSON配列をそのまま使う。`マージなし`では空配列とする。
同じレーンの統合を再び行う場合は、前回までに返した値との和集合を重複なく返す（メインはこの値でレーン全体の規範変更を再取得する）。返すたびにその値を引き継ぎ記録先へ記録する。

次の形式だけを返す。

```text
統合完了
統合後のHEAD: <7文字以上の一意な短縮OID>
保存した計画ファイル: <["計画ファイル名",...]形式のJSON配列。計画なしは[]。入力と同じ順序で重複なし>
adoptしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
rejectしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
adoptを延期したAWIとcommit: <[{"awi":"AWIファイル名","commit":"7文字以上の一意な短縮OIDまたは実装差分が無い場合の空文字列"}]形式のJSON配列。無い場合は[]>
変更したエージェント向け文書: <["リポジトリ相対パス"]形式のJSON配列。無い場合は[]>
```

続行不能時は`agent-toolkit/share/rules-subagent.md`「返却形式の受け渡し」の`続行できない理由:`の1行だけを返す。自身が起動した外部プロセスの終了を確認してから終端する。想定外事象の追加行は`agent-toolkit/share/rules-subagent.md`に従う。

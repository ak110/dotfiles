# レーン統合タスク

必要な計画の起草と実装を担当したレーン担当threadが、専用branchの統合、作成した計画の最終化およびAWIの終端を行う。pushと専用worktreeの回収は行わず、委譲元が担う。

## 入力

```text
必須入力名: 統合区分,実行レビュー済みHEAD,統合先worktree,統合先branch,計画ファイル名一覧,AWI終端区分
任意入力名: 完成条件証拠,プロジェクト固有の公開後の操作の順序
```

`実行レビュー済みHEAD`は`統合区分`が`マージあり`の場合は統合入力HEAD（`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の指示と受領」）の7文字以上の一意な短縮OID、`マージなし`の場合は`なし`を受領する。統合入力HEADは、実行レビューが収束したラウンドのレビュー対象HEAD、または収束後に再レビューを省いた修正の後のHEADである。
`プロジェクト固有の公開後の操作の順序`は、その順序の指定がある場合だけAWIごとの対象と時機として受領する。`引き継ぎ記録先`はレーン担当の起動時に受領した値を継続し、統合指示からは受領しない。通常の統合書込先は受領した統合先worktreeとする。統合先との組合せだけで生じた変更範囲の検証の失敗は、手順9に従って専用worktreeで是正する。
`マージあり`では`完成条件証拠`として、計画ファイル名、対応AWI集合と、実行レビューが返した`完成条件証拠のパス`の絶対パスを持つ順序付きJSON配列を受領する。計画なしの要素は計画ファイル名を`なし`とする。統合開始時に全証拠を読み、採用予定AWIの集合が重複も欠落もなく覆われることを確かめ、各AWIへ統合時の完成条件判定（`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の指示と受領」）を適用する。元のユーザー発言の要求単位は逐語引用、ユーザーコメント、UWI回答から取得する。延期条件と後続工程の対応を確認できない場合はマージを保留する。延期対象は統合時に終端せず、`adoptを延期したAWIとcommit`で終端担当へ渡す。版数更新の完成条件は、全レーン統合後に終端担当が版数、派生manifestおよび公開結果を検収してから`adopt`する。全体検証とCIの成功は公開工程判定（`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`）に従う。許容した延期条件以外に不足、未達または証拠不足があればマージとAWI終端へ進まず、不足したAWIと条件または要求単位を`続行できない理由:`へ書いて返す。

`計画ファイル名一覧`は重複のないファイル名の順序付きJSON配列とし、計画を作成しなかったレーンでは`[]`を受領する。引き継ぎ記録の計画集合、対応AWIと保存状態に一致するか確認する。

## マージありの統合

版数更新を完成条件に持つAWIは、process-wiの1回の実行が1レーンでも複数レーンでも、前段の延期`adopt`の条件を適用する。

1. 専用worktreeがcleanであることを確認する。受領した`実行レビュー済みHEAD`と専用branchのHEADを、いずれも`git rev-parse --short=7 <revision>`で7文字以上の一意な短縮OIDへ正規化して文字列比較し、一致することを確認する。判定の入力はこの2つの短縮OIDとし、レビュー指摘管理表の内容と完全OIDはこの判定から外す。
2. 統合先worktreeの現在branchが統合先branchであり、別の書込主体とGitの中断状態が無いことを確認する。
3. 統合先branchの現在HEADの7文字以上の一意な短縮OIDと、`git merge-base <専用branch> <統合先branch>`で得たrebase前のベースOIDを取得する。
4. 専用branchのHEADが統合先branchの現在HEADの子孫である場合（`git merge-base --is-ancestor <統合先branchの現在HEAD> <専用branchのHEAD>`が終了コード0）は、rebaseせず手順10へ進む。
5. 子孫でない場合は、rebaseの前に、専用branchが統合先branchの現在HEADより先に持つ全commitが未pushであることを`agent-toolkit:commit`の`references/history-rewrite.md`「プッシュ済み判定」の手段で確認する。1件でもpush済みの場合はrebaseせず、そのcommitの短縮OIDを`続行できない理由:`へ書いて`needs_escalation`で返す。
6. 未pushを確認した場合は、rebase前の専用branchのHEADの7文字以上の一意な短縮OIDを`引き継ぎ記録先`へ記録する。書換えコマンドとは別の呼び出しで、専用worktreeを作業ディレクトリとして`agent-toolkit:commit`の`references/history-rewrite.md`「履歴確認の起動形」の`git log`を実行し、対象commitの状態を確認する。続けて同じworktreeで`git rebase <統合先branchの現在HEAD>`を実行し、専用branchを統合先branchの現在HEADの上へ載せ替える。rebaseの対象は専用worktree内の専用branchに限り、統合先branchと他のレーンの専用branchはそのまま保つ。
7. rebaseが競合で停止した場合は、`git rebase --abort`と`git rebase --continue`のいずれも自ら実行せず、rebaseを進行中のまま保持して`needs_escalation`で返す。`続行できない理由:`へ、競合したファイルのリポジトリ相対パス、専用worktreeの絶対パス、およびそのworktreeでrebaseが進行中であることを書く。競合の解消と再レビューの指示はメインが所有する。メインから競合の解消指示を受領した場合は、競合を解消して解消したパスだけをstageする。続けて`agent-toolkit:commit`の`references/history-rewrite.md`「履歴確認の起動形」の`git log`を単独で実行し、`git rebase --continue`でrebaseを完了させる。競合箇所、解消方針、変更内容、影響範囲を`atk review-table add`で実行レビューのレビュー指摘管理表へ1行登録し、同じ行へ`atk review-table respond`で解消内容を応答として記録する。その後、`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`「レビュー修正の履歴統合」が定める返却値を返す。統合指示を再び受領した場合は手順1から実施する。
8. rebaseが成功した場合は、`git range-diff <rebase前のベースOID>..<rebase前の専用branchのHEAD> <統合先branchの現在HEAD>..<rebase後の専用branchのHEAD>`を実行する。全commitが1対1で対応し、かつ内容が変化していないこと（各行の対応記号が`=`であること）を確認する。対応の欠落、追加、または内容の変化を観測した場合は手順9へ進まず、`git range-diff`の該当行を`続行できない理由:`へ書いて`needs_escalation`で返す。
9. 計画を持つ場合は計画ファイルの`## 検証`の`変更範囲の検証`行、計画なしの場合は引き継ぎ記録に確定した変更範囲の検証コマンドを、rebase後の専用branchのHEADで再実行し、終了コード0と、後掲「検証結果の警告の判定」で阻害に当たる警告が無いことを確認する。失敗がレビュー済みHEADでは再現せず、統合先との組合せだけで生じた場合は、失敗した検証が確かめる契約の送信側、受信側、実装およびテストを列挙したうえで、専用worktreeへ是正commitを記録する。変更範囲の検証を再実行して成功と阻害に当たる警告が無いことを確認し、commitと検証の証拠（警告の判定の根拠を含む）を`needs_escalation`でメインへ返す。メインの差分確認と再指示後に手順10へ進む。レビュー済みHEADでも再現する失敗または認可範囲外の変更を要する失敗は、コマンド、出力および続行できない事情を`続行できない理由:`へ書いて返す。各commitの内容の不変は、受領した`実行レビュー済みHEAD`との一致確認と手順8の`git range-diff`が担保する。
10. 対象リポジトリのプロジェクト規範に、統合後にだけ成立する検証があるか確認する。なければ手順11へ進む。ある場合はmanaged-tempの中に作業ディレクトリを確保する。標準出力と標準エラーの保存先を、その領域内の絶対パスとして`summary_policy`へ記す。規範が定めるコマンドを、専用worktreeを`cwd`として`agents_server`の`start`（`mode`は`shell`）へ渡して1回実行する。委譲先には両方を保存して必要な範囲を読ませ、終了状態、警告の有無、両保存先と要約を返させる。返却パスが渡した領域内に実在することを確認する。保存済みの全量から、終了コード0と、後掲「検証結果の警告の判定」で阻害に当たる警告が無いことを検収する。この検証は他のレーンの成果と合わせた状態でだけ成立するため、rebase前の変更範囲の検証では代替できない。専用branchのHEADは統合先branchの現在HEADの子孫であり、そのtreeはfast-forward後の統合先と同じになるため、fast-forwardの前に専用worktreeで実行する。統合先を変える前に失敗を確定すると、他のレーンが失敗した状態の統合先の上へ載ることを防げる。成立しない場合は統合先branchを変更せず、専用worktreeで是正commitを作成し、変更範囲の検証を再実行してから本手順をやり直す。
11. 統合先branchを専用branchへfast-forwardできることを確認し、`git -C <統合先worktreeの絶対パス> merge --ff-only <専用branch>`でfast-forwardマージする。別の作業ツリーからの`git push`でマージ先branchを更新しない。`receive.denyCurrentBranch`の省略時の値`refuse`が、チェックアウト中のbranchへのref更新を拒否するためである。この時点でもfast-forwardが成立しない場合は、merge commitとcherry-pickで独自解決せず`needs_escalation`で返す。
12. マージ後の統合先branchの7文字以上の一意な短縮OIDを取得する。

### 検証結果の警告の判定

手順9と10の検証後は、`agent-toolkit:check-execution`のSKILL.md「検証結果の診断と警告の判定」を読む。
基準版は手順3で取得した統合先branchの統合前HEADとする。
比較のために再実行が必要な場合は、統合先worktreeで該当コマンドだけを読み取り専用で実行し、統合先の追跡ファイルとbranchを保持する。

- 共通判定で阻害とした出力は手順9・10の失敗と同じく扱い、専用worktreeで是正するか`needs_escalation`で返す
- 処理が成立する既存警告と確定した場合は、意味、統合前の結果との対応と根拠を引き継ぎ記録へ残し、メインの受理を待たずに続行する
- 新規の警告や診断、比較不能、意味を確定できない結果を返す場合は、原因・影響または未確認の理由と不足する条件を`needs_escalation`へ添える

## マージなしの統合

実装commitを作成せず、統合先branchを統合開始時の状態のまま保つ。統合開始時の統合先branchの7文字以上の一意な短縮OIDを`統合後のHEAD`とする。

## 計画最終化

`計画ファイル名一覧: []`のレーンは計画を対象とする自動チェックと保存を省き、レーン稼働時間、統合結果およびAWI終端前の状態を引き継ぎ記録へ残す。返却する`保存した計画ファイル`は`[]`とする。以下の手順は計画を持つレーンだけへ、配列の順に各要素へ適用する。

各計画のメタ情報が示す対象リポジトリの専用worktreeが実在することを確認する。引き継ぎ記録が反映後の観測のみの再開と旧専用worktreeの回収を示し、統合区分が`マージなし`の場合は現在の専用worktreeを後続の操作場所とする。それ以外で回収済みなら計画メタ情報を書き換えず、回収順序の違反を`needs_escalation`で返す。引き継ぎ記録で保存済みと確定した計画は再保存しない。観測のみの再開で`atk plans checkout`から進捗を追記したバンドルは再保存する。未保存の計画で`~/.claude/plans`に対象バンドルが無い場合は、保存状態と所在を調べて引き継ぎ記録へ残し、必要なら`atk plans checkout <private-notes/plans/からの相対パス>`で取得する。対象の専用worktreeをcwdとして、各計画へ`atk run-script plan-progress --`で`## 進捗ログ`に統合区分、統合先branch、`統合後のHEAD`、対応する実行レビューの収束およびAWI終端前の状態を追記する。
同じ追記へそのレーンの稼働時間も記録する。値はレーン担当のsessionの開始時刻から統合の完了時刻までの経過時間とし、書式は`agent-toolkit:plan-mode`の`references/plan-file-standards.md`が定める。開始時刻は`atk agents list`の出力のうち`label`が`<レーン識別子>-exec`と一致する自sessionの`created_at`から取得する。`started_at`はturnごとに更新されるため起点に用いない。`created_at`を持たない行（旧版のagents_serverが書いた状態）では`started_at`を起点とし、秒数に続けて「（最後のturnの開始からの下限値）」と書く。
この記録はprocess-wiの次の実行の選定工程がレーン配分の見込みを導く入力になる。記録が無いと、見込みと実績の乖離がそのまま待ち時間として残る。rebaseを実行した場合は、rebase前後の専用branchの7文字以上の一意な短縮OIDの対応も同じ追記へ含める。
観測のみの再開で旧専用worktreeが回収済みの計画は、元の保存時にplan-checkを通過している。計画本文は凍結したまま進捗ログだけを追記したことを確認し、plan-checkの再実行を省く。それ以外は専用worktreeをcwdとして各計画へ`atk run-script plan-check -- --reject-migration-warnings --work-dir <計画メタ情報の対象リポジトリ> <計画ファイルの絶対パス>`を単独実行し、終了コード0と警告の不在を確認する。統合先worktreeと専用worktreeが異なる場合も、この2コマンドでは専用worktreeをcwdとして維持する。
続けて、`atk plans commit`が`~/.claude/plans`を解決する契約に従うcwdで`atk plans commit <計画ファイル名>`を計画ごとに単独実行し、成功と警告の不在を確認して、保存した計画名と結果を引き継ぎ記録へ直ちに残す。途中で失敗した場合は保存済みと未保存の計画を区別して返し、引き継ぎ記録から未保存分を再開する。全計画の保存が終わるまで`統合完了`を返さない。

## AWI行の終端

計画または計画なしの引き継ぎ記録に確定した採否と、受領した終端区分を適用する。採用した項目と充足済みの項目は`atk wi adopt`、不採用の項目は`atk wi reject`で終端する。adoptではAWIごとに実装差分を確かめ、adoptのcommit対応付け（`agent-toolkit:wi-standards`「状態と依存」の遷移表）に従う。複数commitのメモは`--note-file`へ記録する。開始時と統合時のHEADを全項目へ機械的に複製しない。プロジェクト固有の公開後の操作後へadoptを延期する項目は、AWIファイル名と対応する実装commitを対応付けて返し、状態変更は延期先の工程へ委ねる。`終端しない`と受領した項目は状態を変更せず、`adopted`と`rejected`のいずれにも含めない。`混在`の項目はメインが`inbox`へ戻す。観測のみの再開で残った項目は`processing`のまま残し、メインがセッション終了工程で扱う。

各状態変更コマンドは単独実行し、成功の報告、対象の完全識別子および警告の不在で完了を判定する。同じ状態を別コマンドで取得し直さない。`atk`の成功の報告を完了の根拠とし、報告と保存状態の不一致は`atk`側で是正するためである。
commitを記録する採否操作は`agent-toolkit:wi-standards`「状態と依存」の共通前提に従い、
統合後の実装commitを解決できる受領済みの統合先worktreeの絶対パスを`--target-repo`へ渡す。

## 出力

統合差分のエージェント向け文書の変更パスは、統合先worktreeで`atk run-script agent-doc-changes -- <手順3で取得した統合先branchの統合前HEAD> <統合後のHEAD>`を単独実行し、終了コード0で得た標準出力のJSON配列をそのまま返す。対象集合は同コマンドの判定に従う。プロジェクト側は`AGENTS.md`、`CLAUDE.md`、`.claude/rules/`、`.claude/skills/`の`SKILL.md`と`references/`、`.claude/agents/`を含む。配布物側は`agent-toolkit/rules/`、`agent-toolkit/skills/`の`SKILL.md`と`references/`、`agent-toolkit/agents/`、`agent-toolkit/share/`を含む。chezmoiの配布元である`.chezmoi-source/dot_claude/rules/`と`.chezmoi-source/dot_claude/skills/`（`.md.tmpl`を含む）も含む。通常コードと経緯記録は対象外とする。`マージなし`の統合では空配列とする。
同じレーンの統合を再び行う場合（統合指示の再受領、競合解消後の再統合など）は、前回までに返した`変更したエージェント向け文書`の要素と今回の出力の和集合を、重複を除いて返す。メインはこの値でレーン全体の規範変更を再取得するため、今回の統合前HEADから数えた差分だけを返すと、前回統合したエージェント向け文書が再取得の対象から外れる。そのため、`変更したエージェント向け文書`を返すたびに、その値を引き継ぎ記録先へ記録する。

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

続行不能時は`状態: needs_escalation`と`続行できない理由:`の2行だけを返す。自身が起動した外部プロセスの終了を確認してから終端する。想定外事象の追加行は`agent-toolkit/share/rules-subagent.md`に従う。

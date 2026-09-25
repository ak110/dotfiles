# レーン統合タスク

計画と実装を担当したレーン担当threadが、専用branchの統合、計画の最終化及びAWIの終端を行う。pushと専用worktreeの回収は行わず、呼び出し元が担う。

## 入力

```text
必須入力名: 統合区分,実行レビュー済みHEAD,統合先worktree,統合先branch,メイン計画ファイル名,AWI終端区分
```

`実行レビュー済みHEAD`は、`統合区分`が`マージあり`の場合は実行レビューが収束したラウンドのレビュー対象HEADの7文字以上の一意な短縮OID、`マージなし`の場合は`なし`を受領する。
固有の終端順序がある場合はAWIごとの対象と時機も受領する。`引き継ぎ記録先`はレーン担当の起動時に受領した値を継続し、統合指示からは受領しない。作業対象リポジトリではcommitとffマージを行えるが直接pushは行わず、直接pushは呼び出し元が別の工程で行う。キュー管理リポジトリの`atk plans commit`と`atk wi`は通常の公開を用いる。リポジトリの指定がない操作禁止とキュー管理リポジトリの通常公開の関係を一意に解釈できない場合は、対象リポジトリを呼び出し元へ確認する。通常の統合書込先は受領した統合先worktreeとする。統合先との組合せだけで生じた近接検証の失敗は、手順9に従って専用worktreeで是正する。
`マージあり`では`完成条件証拠`として収束した実行レビューが返したJSONの絶対パスを追加で受領する。統合開始時に読み、採用予定のAWIについて、要求固有の完成条件と`wi_conditions`の対応、元のユーザー発言の要求単位と`user_requirements`の対応、各行の判定と証拠の参照可能性を検査する。元のユーザー発言の要求単位は逐語引用、ユーザーコメント及びUWI回答から取得し、各単位の`達成`を採用終端の条件とする。統合時に`adopt`するAWIの要求固有の完成条件も全て`達成`を要する。延期`adopt`のAWIでは、受領した終端区分が後続工程と検収時機を明示する条件だけ、統合時の`未達`又は`証拠不足`を許容する。その他の条件は`達成`を要し、延期条件と後続工程の対応を確認できない場合はマージを保留する。延期対象は統合時に終端せず、`deferred_adopt_commits`で終端担当へ渡す。版数更新の完成条件は、全レーン統合後に終端担当が版数、派生manifest及び公開結果を検収してから`adopt`する。リポジトリ全体の検査とCIの成功も公開工程で判定する。許容した延期条件以外に不足、未達又は証拠不足があればマージとAWI終端へ進まず、不足したAWIと条件又は要求単位を`reason:`へ書いて返す。

## マージありの統合

1. 専用worktreeがcleanであることを確認する。受領した`実行レビュー済みHEAD`と専用branchのHEADを、いずれも`git rev-parse --short=7 <revision>`で7文字以上の一意な短縮OIDへ正規化して文字列比較し、一致することを確認する。判定の入力はこの2つの短縮OIDとし、指摘管理表の内容と完全OIDはこの判定から外す。
2. 統合先worktreeの現在branchが統合先branchであり、別の書込主体とGitの中断状態が無いことを確認する。
3. 統合先branchの現在HEADの7文字以上の一意な短縮OIDと、`git merge-base <専用branch> <統合先branch>`で得たrebase前のベースOIDを取得する。
4. 専用branchのHEADが統合先branchの現在HEADの子孫である場合（`git merge-base --is-ancestor <統合先branchの現在HEAD> <専用branchのHEAD>`が終了コード0）は、rebaseせず手順10へ進む。
5. 子孫でない場合は、rebaseの前に、専用branchが統合先branchの現在HEADより先に持つ全commitが未pushであることを`agent-toolkit:commit`の`references/history-rewrite.md`「プッシュ済み判定」の手段で確認する。1件でもpush済みの場合はrebaseせず、そのcommitの短縮OIDを`reason:`へ書いて`needs_escalation`で返す。
6. 未pushを確認した場合は、rebase前の専用branchのHEADの7文字以上の一意な短縮OIDを`引き継ぎ記録先`へ記録する。書換えコマンドとは別の呼び出しで、専用worktreeを作業ディレクトリとして`git log --oneline --decorate -n 20`を実行し、対象commitの状態を確認する。続けて同じworktreeで`git rebase <統合先branchの現在HEAD>`を実行し、専用branchを統合先branchの現在HEADの上へ載せ替える。rebaseの対象は専用worktree内の専用branchに限り、統合先branchと他のレーンの専用branchはそのまま保つ。
7. rebaseが競合で停止した場合は、`git rebase --abort`と`git rebase --continue`のいずれも自ら実行せず、rebaseを進行中のまま保持して`needs_escalation`で返す。`reason:`へ、競合したファイルのリポジトリ相対パス、専用worktreeの絶対パス、及びそのworktreeがrebase進行中である旨を書く。競合の解消と再レビューの指示はメインが所有する。
8. rebaseが成功した場合は、`git range-diff <rebase前のベースOID>..<rebase前の専用branchのHEAD> <統合先branchの現在HEAD>..<rebase後の専用branchのHEAD>`を実行する。全commitが1対1で対応し、かつ内容が変化していないこと（各行の対応記号が`=`であること）を確認する。対応の欠落、追加、又は内容の変化を観測した場合は手順9へ進まず、`git range-diff`の該当行を`reason:`へ書いて`needs_escalation`で返す。
9. 計画ファイルの`## 検証`の`近接検証`行のコマンドを、rebase後の専用branchのHEADで再実行し、終了コード0と警告の不在を確認する。失敗がレビュー済みHEADでは再現せず、統合先との組合せだけで生じた場合は、失敗契約の送信側、受信側、実装及び検体を列挙したうえで、専用worktreeへ是正commitを記録する。近接検証を再実行して成功と警告の不在を確認し、commitと検証の証拠を`needs_escalation`でメインへ返す。メインの差分確認と再指示後に手順10へ進む。レビュー済みHEADでも再現する失敗又は認可範囲外の変更を要する失敗は、コマンド、出力及び阻害条件を`reason:`へ書いて返す。各commitの内容の不変は、受領した`実行レビュー済みHEAD`との一致確認と手順8の`git range-diff`が担保する。
10. 統合先branchを専用branchへfast-forwardできることを確認し、fast-forwardマージする。この時点でもfast-forwardが成立しない場合は、merge commitとcherry-pickで独自解決せず`needs_escalation`で返す。
11. 対象リポジトリのプロジェクト規範に、統合後にだけ成立する検査があるか確認する。なければ手順12へ進む。ある場合は管理対象一時領域を確保する。標準出力と標準エラーの保存先を、その領域内の絶対パスとして`summary_policy`へ記す。規範が定めるコマンドを`agents_server`の`start_shell`へ渡して1回実行する。委譲先には両方を保存して必要な範囲を読ませ、終了状態、警告の有無、両保存先と要約を返させる。返却パスが渡した領域内に実在することを確認する。保存済みの全量から終了コード0と警告の不在を検収する。この検査は他のレーンの成果と合わせた状態でだけ成立するため、専用worktreeの近接検証では代替できない。成立しない場合は専用worktreeで是正commitを作成し、近接検証を再実行してから手順10をやり直す。
12. マージ後の統合先branchの7文字以上の一意な短縮OIDを取得する。

## マージなしの統合

実装commitを作成せず、統合先branchを統合開始時の状態のまま保つ。統合開始時の統合先branchの7文字以上の一意な短縮OIDを`merged_head`とする。

## 計画最終化

計画メタ情報が示す対象リポジトリの専用worktreeが実在することを確認する。回収済みなら計画メタ情報を書き換えず、回収順序の違反を`needs_escalation`で返す。計画作業rootに対象バンドルがなく保存済みの場合は、保存root相対パスを特定し、`atk plans checkout <保存root相対パス>`で取得する。同worktreeをcwdとして、`atk run-script plan-progress --`で計画ファイルの`## 進捗ログ`へ、統合区分、統合先branch、`merged_head`、実行レビューの収束及びAWI終端前の状態を追記する。
同じ追記へ当該レーンの稼働時間も記録する。値はレーン担当のsessionの開始時刻から統合の完了時刻までの経過時間とし、書式は`agent-toolkit:plan-mode`の計画ファイル基準が定める。
この記録は、次の処理回の選定工程がレーン配分の見込みを導く入力になる。記録が無いと、見込みと実測の乖離がそのまま待ち時間として残る。rebaseを実行した場合は、rebase前後の専用branchの7文字以上の一意な短縮OIDの対応も同じ追記へ含める。
同じ専用worktreeをcwdとして`atk run-script plan-check -- --reject-migration-warnings --work-dir <計画メタ情報の対象リポジトリ> <計画ファイルの絶対パス>`を単独実行し、終了コード0と警告の不在を確認する。統合先worktreeと専用worktreeが異なる場合も、この2コマンドでは専用worktreeをcwdとして維持する。
続けて、`atk plans commit`が作業計画rootを解決する契約に従うcwdで`atk plans commit <メイン計画ファイル名>`を単独実行し、成功の報告と警告の不在を確認する。

## AWI行の終端

要求単位の由来を再判定せず、受領した終端区分を適用する。採用した項目と充足済みの項目は`atk wi adopt`、不採用の項目は`atk wi reject`で終端する。adoptではAWIごとに実装差分を照合し、要求を反映したcommitの完全OIDを`--commit`へ渡す。複数commitに分かれる場合は全OIDと対応する要求単位を`--note-file`へ記録する。開始時と統合時のHEADを全項目へ機械的に複製しない。既存実装で充足済みで実装差分が無い項目は`--commit`を省き、根拠をメモへ残す。固有の終端工程後へadoptを延期する項目は、AWIファイル名と対応する実装commitを対応付けて返し、状態変更は延期先の工程へ委ねる。

各状態変更コマンドは単独実行し、成功の報告、対象の完全識別子及び警告の不在で完了を判定する。同じ状態を別コマンドで取得し直さない。

## 出力

統合差分から、`agent_toolkit._plan.structure.parsing.is_agent_doc_target_file()`が真を返すエージェント向け文書の変更パスを列挙する。対象集合には`AGENTS.md`、`CLAUDE.md`、`.claude/rules/`、`.claude/skills/`を含む。`agent-toolkit/rules/`、`agent-toolkit/skills/`、`agent-toolkit/share/`、`agent-toolkit/agents/`、hook関連文書も含む。通常コードと履歴文書は対象外とする。

変更パスをリポジトリ相対パスの重複なしJSON文字列配列として返す。該当しない場合は空配列とする。

次の形式だけを返す。

```text
統合完了
merged_head: <7文字以上の一意な短縮OID>
plan_committed: <メイン計画ファイル名>
adopted: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
rejected: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
deferred_adopt_commits: <[{"awi":"AWIファイル名","commit":"7文字以上の一意な短縮OID又は実装差分が無い場合の空文字列"}]形式のJSON配列。無い場合は[]>
agent_rule_changes: <["リポジトリ相対パス"]形式のJSON配列。無い場合は[]>
```

続行不能時は`status: needs_escalation`と`reason:`の2行だけを返す。自身が起動した外部プロセスの終了を確認してから終端する。想定外事象の追加行は`agent-toolkit/share/rules-subagent.md`に従う。

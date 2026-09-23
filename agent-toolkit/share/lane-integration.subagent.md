# レーン統合タスク

計画と実装を担当したレーン担当threadが、専用branchの統合、計画の最終化及びAWIの終端を行う。pushと専用worktreeの回収は行わず、呼び出し元が担う。

## 入力

```text
必須入力名: 統合区分,実行レビュー済みHEAD,統合先worktree,統合先branch,メイン計画ファイル名,AWI終端区分
```

`実行レビュー済みHEAD`は、`統合区分`が`マージあり`の場合は実行レビューが収束したラウンドのレビュー対象HEADの7文字以上の一意な短縮OID、`マージなし`の場合は`なし`を受領する。
固有の終端順序がある場合はAWIごとの対象と時機も受領する。`引き継ぎ記録先`はレーン担当の起動時に受領した値を継続し、統合指示からは受領しない。commitとffマージは実行できるがpushは行わず、pushは呼び出し元が別の工程で行う。書き込み先は受領した統合先worktreeとし、専用worktreeには計画の追記を除いて新しい実装変更を加えない。

## マージありの統合

1. 専用worktreeがcleanであることを確認する。受領した`実行レビュー済みHEAD`と専用branchのHEADを、いずれも`git rev-parse --short=7 <revision>`で7文字以上の一意な短縮OIDへ正規化して文字列比較し、一致することを確認する。判定の入力はこの2つの短縮OIDとし、指摘管理表の内容と完全OIDはこの判定から外す。
2. 統合先worktreeの現在branchが統合先branchであり、別の書込主体とGitの中断状態が無いことを確認する。
3. 統合先branchの現在HEADの7文字以上の一意な短縮OIDと、`git merge-base <専用branch> <統合先branch>`で得たrebase前のベースOIDを取得する。
4. 専用branchのHEADが統合先branchの現在HEADの子孫である場合（`git merge-base --is-ancestor <統合先branchの現在HEAD> <専用branchのHEAD>`が終了コード0）は、rebaseせず手順10へ進む。
5. 子孫でない場合は、rebaseの前に、専用branchが統合先branchの現在HEADより先に持つ全commitが未pushであることを`agent-toolkit:commit`の`references/history-rewrite.md`「プッシュ済み判定」の手段で確認する。1件でもpush済みの場合はrebaseせず、そのcommitの短縮OIDを`reason:`へ書いて`needs_escalation`で返す。
6. 未pushを確認した場合は、rebase前の専用branchのHEADの7文字以上の一意な短縮OIDを`引き継ぎ記録先`へ記録する。書換えコマンドとは別の呼び出しで、専用worktreeを作業ディレクトリとして`git log --oneline --decorate -n 20`を実行し、対象commitの状態を確認する。続けて同じworktreeで`git rebase <統合先branchの現在HEAD>`を実行し、専用branchを統合先branchの現在HEADの上へ載せ替える。rebaseの対象は専用worktree内の専用branchに限り、統合先branchと他のレーンの専用branchはそのまま保つ。
7. rebaseが競合で停止した場合は、`git rebase --abort`と`git rebase --continue`のいずれも自ら実行せず、rebaseを進行中のまま保持して`needs_escalation`で返す。`reason:`へ、競合したファイルのリポジトリ相対パス、専用worktreeの絶対パス、及びそのworktreeがrebase進行中である旨を書く。競合の解消と再レビューの指示はメインが所有する。
8. rebaseが成功した場合は、`git range-diff <rebase前のベースOID>..<rebase前の専用branchのHEAD> <統合先branchの現在HEAD>..<rebase後の専用branchのHEAD>`を実行する。全commitが1対1で対応し、かつ内容が変化していないこと（各行の対応記号が`=`であること）を確認する。対応の欠落、追加、又は内容の変化を観測した場合は手順9へ進まず、`git range-diff`の該当行を`reason:`へ書いて`needs_escalation`で返す。
9. 計画ファイルの`## 検証`の`近接検証`行のコマンドを、rebase後の専用branchのHEADで再実行し、終了コード0と警告の不在を確認する。成立しない場合は手順10へ進まず、実行したコマンドと観測した出力を`reason:`へ書いて`needs_escalation`で返す。実行レビューを再実施せず手順10へ進む。各commitの内容の不変は、受領した`実行レビュー済みHEAD`との一致確認と手順8の`git range-diff`が担保する。
10. 統合先branchを専用branchへfast-forwardできることを確認し、fast-forwardマージする。この時点でもfast-forwardが成立しない場合は、merge commitとcherry-pickで独自解決せず`needs_escalation`で返す。
11. 対象リポジトリのプロジェクト規範が、複数の書込主体の成果を統合した後にだけ成立する検査として定めるコマンドがある場合は、そのコマンドを`agents_server`の`start_shell`へ渡して1回実行し、終了コード0と警告の不在を確認する。この検査は他のレーンの成果と合わせた状態でだけ成立するため、専用worktreeの近接検証では代替できない。成立しない場合は、専用worktreeで是正commitを作成し、近接検証を再実行してから手順10をやり直す。この検査を定める記述が対象リポジトリのプロジェクト規範に無い場合は、本手順を実行せず手順12へ進む。
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

要求単位の由来を再判定せず、受領した終端区分を適用する。採用又は充足済みの項目は`atk wi adopt`、不採用の項目は`atk wi reject`で終端する。固有の終端工程後へadoptを延期する項目は、AWIファイル名と`merged_head`を対応付けて返し、状態変更は延期先の工程へ委ねる。

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
deferred_adopt_commits: <[{"awi":"AWIファイル名","commit":"7文字以上の一意な短縮OID"}]形式のJSON配列。無い場合は[]>
agent_rule_changes: <["リポジトリ相対パス"]形式のJSON配列。無い場合は[]>
```

続行不能時は`status: needs_escalation`と`reason:`の2行だけを返す。自身が起動した外部プロセスの終了を確認してから終端する。想定外事象の追加行は`agent-toolkit/share/rules-subagent.md`に従う。

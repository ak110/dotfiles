# レーン統合タスク

計画と実装を担当したレーン担当threadが、専用branchの統合、計画の最終化及びAWIの終端を行う。pushと専用worktreeの回収は行わない。

## 入力

```text
必須入力名: 統合区分,統合先worktree,統合先branch,メイン計画ファイル名,AWI終端区分
```

固有の終端順序がある場合はAWIごとの対象と時機も受領する。`引き継ぎ記録先`はレーン担当の起動時に受領した値を継続し、統合指示からは受領しない。commitとffマージは実行できるがpushは行わない。受領した統合先worktreeだけへ書き込み、専用worktreeには計画の追記を除いて新しい実装変更を加えない。

## マージありの統合

1. 専用worktreeがcleanであり、専用branchのHEADが実行レビュー済みHEADと一致することを確認する。
2. 統合先worktreeの現在branchが統合先branchであり、別の書込主体とGitの中断状態が無いことを確認する。
3. 統合先branchを専用branchへfast-forwardできることを確認し、fast-forwardマージする。非fast-forward又は競合を観測した場合は、rebase、merge commit又はcherry-pickで独自解決せず`needs_escalation`で返す。
4. マージ後の統合先branchの7文字以上の一意な短縮OIDを取得する。

## マージなしの統合

実装commitを作成せず、統合先branchを変更しない。統合開始時の統合先branchの7文字以上の一意な短縮OIDを`merged_head`とする。

## 計画最終化

計画ファイルの`## 進捗ログ`へ、統合区分、統合先branch、`merged_head`、実行レビューの収束及びAWI終端前の状態を追記する。
`${CLAUDE_PLUGIN_ROOT}/skills/plan-mode/scripts/check_plan_file.py --reject-migration-warnings <計画ファイルの絶対パス>`を計画ファイル基準が定める起動形で単独実行し、終了コード0と警告の不在を確認する。
続けて`atk plans commit <メイン計画ファイル名>`を単独実行し、成功の報告と警告の不在を確認する。

## AWI行の終端

人間由来の不採用範囲について確認済みであることを、受領した終端区分から確認する。採用又は充足済みの項目は`atk wi adopt`、不採用の項目は`atk wi reject`で終端する。固有の終端工程後へadoptを延期する項目はここで操作せず、AWIファイル名と`merged_head`を対応付けて返す。

各状態変更コマンドは単独実行し、成功の報告、対象の完全識別子及び警告の不在で完了を判定する。同じ状態を別コマンドで取得し直さない。

## 出力

次の形式だけを返す。

```text
統合完了
merged_head: <7文字以上の一意な短縮OID>
plan_committed: <メイン計画ファイル名>
adopted: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
rejected: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
deferred_adopt_commits: <AWIファイル名と7文字以上の一意な短縮OIDの対応。無い場合は「なし」>
```

続行不能時は`status: needs_escalation`と`reason:`の2行だけを返す。自身が起動した外部プロセスの終了を確認してから終端する。想定外事象の追加行は`agent-toolkit/share/rules-subagent.md`に従う。

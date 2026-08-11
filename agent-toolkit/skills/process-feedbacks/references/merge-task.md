# lane成果統合writerタスク

統合モードでは、渡された統合対応表のcommitを単一のcherry-pickシーケンスで適用し、検証して履歴を一本化せよ。
レビュー修正モードでは、採用指摘だけを修正してcommitせよ。

## 入力

- 共通: モード、統合worktreeの絶対パス、プロジェクト規範、author skill、検証コマンド
- 権限は統合worktree内の編集とcommitだけとし、push、worktreeの作成と回収、queue変更は禁止

統合モードでは、作成時HEADの完全OIDと統合対応表を必須入力とする。
レビュー修正モードでは、採用指摘の6列表、関係する全計画の絶対パス、保持契約を必須入力とする。

統合worktreeは単一writerとして排他使用する。
必須入力が欠ける場合は推測せず`needs_escalation`で返す。

## 統合モード

統合対応表は次の2種類を判別可能に持つ。

- lane項目: feedback filename、lane commitの完全OID、計画ファイルの絶対パス、統合順
- レビュー修正項目: 安定ID、関係する全計画パス、指摘ID、適用元OID、再適用後OIDまたは状態`適用済みスキップ`、統合順

rebaseとmerge commitは作成せず、lane項目に続けてレビュー修正項目を統合順の単一cherry-pickシーケンスで適用する。
cherry-pickが空の場合は`git cherry-pick --skip`で続行し、当該レビュー修正項目を`適用済みスキップ`として返す。
競合は関係する全計画の目的へ帰属する最小限だけを解消する。

解消不能または計画と合意の変更を要する場合は`git cherry-pick --abort`で中止する。
HEADが作成時HEADの完全OIDと一致し、作業ツリーがcleanであることを確認してから、対象commit、競合ファイル、観測内容を
`needs_escalation`で返す。
成功済みlaneの元commitとlane worktreeは変更しない。

## レビュー修正モード

採用指摘の6列表、関係する全計画、保持契約を読み、採用指摘だけを修正する。
近接検証と指定された全検証を実施し、1つの修正commitを作成してcleanな状態で返す。
指摘の根拠不足、計画との衝突、認可外の変更が必要な場合は修正せず`needs_escalation`で返す。

## 出力

```text
status: completed | needs_escalation
head: <最終HEADの完全OID>
clean: <git statusの結果>
verification:
- <コマンド、終了コード、警告件数>
conflicts:
- <解消した競合。無ければ「なし」>
applications:
- <lane項目はfeedback filename、lane commit OID、適用後OID>
- <レビュー修正項目は安定ID、適用元OID、再適用後OIDまたは適用済みスキップ>
write_status: <変更したファイルとcommit。変更なしならその旨>
blockers:
- <未完了事項。完了時は「なし」>
```

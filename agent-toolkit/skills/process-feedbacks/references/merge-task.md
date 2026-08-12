# lane成果統合writerタスク

統合モードでは、渡された統合対応表のcommitを単一のcherry-pickシーケンスで適用し、検証して履歴を一本化せよ。
レビュー修正モードでは、採用指摘だけを修正してcommitせよ。

## 入力

- 共通: モード、統合worktreeの絶対パス、プロジェクト規範、author skill、検証コマンド
- 権限は統合worktree内の編集とcommitだけとし、push、worktreeの作成と回収、queue変更は禁止

統合モードでは、作成時HEADの完全OIDと統合対応表を必須入力とする。
レビュー修正モードでは、採用指摘の6列表、関係する全計画の絶対パス、保持契約を必須入力とする。

統合worktreeは単一writerとして排他使用する。
同じ計画ファイルに属するcommitは、実装段階から1 writerが順次作成したものだけを受理する。
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
修正着手前に、指摘を通常運用の再現経路と入力主体へ照合し、問題と手段の比例性を独立に再判定する。
対象外の入力前提又は異なる脅威モデルだけで成立する候補は修正対象として採用せず`needs_escalation`で返す。
reviewerの修正方針を新しい要件として扱わない。
修正に永続状態、所有権、期限、復旧経路、互換経路の新設が必要な場合は、元の目的と非目標へ差し戻す。
何もしない案、既存操作だけの案、局所修正案、新機構案を比較し、単純案が目的を満たす場合は新機構を採用しない。
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

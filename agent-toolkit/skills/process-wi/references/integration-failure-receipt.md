# レーン統合の失敗の受領

本書は、`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`に従いレーン担当を起動したメインエージェントの、統合失敗の受領を扱う。
対象は統合先との組み合わせによる検証失敗への是正コミットと、merge競合で必要となった判断である。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 統合の返却で警告の判定と根拠を受け取る時 | `../../../share/lane-integration-subagent-operation.md` | 判断 | `delegation-result`、`read:agent-toolkit:*/share/lane-integration-subagent-operation.md` |
| 対応完了を受けて再統合を指示する時 | `../../../share/exec.parent.md`、`../../../share/exec-parent-integration.md` | | |
| 対応完了のレビュー修正を判定する時 | `../../../share/exec-review-parent-correction.md` | | |
| 同一スレッドを継続できず新規のレーン担当を起動する時（操作対象外: ツールと結び付かない判断） | `../../delegation/SKILL.md` | | |

是正コミットを受け取った場合は、失敗した検証が対象とする契約（送信側・受信側・実装・テスト）の差分と、再検証の終了コードを確認する。
警告については、レーン担当が`../../../share/lane-integration-subagent-operation.md`「検証結果の警告の判定」で行った判定と根拠を受け入れる。
処理の成立に影響しない既存の警告のみを理由として再度の受理確認を求めることは避ける。
確認済みのHEADを明記して同一スレッドへ統合の再開を指示し、契約外の差分や検証不足が認められる場合は、不足箇所を伝えて修正を求める。

merge競合の判断を受け取った場合は、次の手順で対処する。
レーン担当が確定できる解消を一律に止めず、必要な判断と、解消差分の独立文脈レビューを区別する。

1. 同一のレーン担当スレッドへ競合の解消を指示する。
   別の修正担当を新規に起動することは避ける。
2. レーン担当は`agent-toolkit:process-wi`の`references/lane-integration-failure.md`「mergeの競合」に従い、競合解消、merge commitとWIの対応記録、限定検証と解消差分の独立文脈レビューを同じthreadで完了する。
3. 新しいHEADの受理済みレビューとmanifestを保持し、`../../../share/exec-parent-integration.md`の統合前検収に従って再開する。
   既存実装OIDの書換えや旧新対応表の受渡しは要求しない。

同一スレッドを継続できない場合は、`../../delegation/SKILL.md`「継続と新規起動」に従い、新規のレーン担当へ`再開位置`として`~/.claude/plans`配下にある計画ファイルの絶対パスを渡す。

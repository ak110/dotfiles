# WIの調査・起草・保存の共通手順：投入

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../review-session/references/session-records-conversation.md` |
| 依頼の`再発の経過を問う要求`が`該当`の場合に加え、起草者が逐語本文・趣旨から失敗・誤り・指摘の反復、頻度、悪化、いつからか守られなくなったことへの言及と判断した要求で、原因を確定する前 | `agent-basics:bugfix/references/root-cause-severity-quality.md`と`agent-basics:bugfix/references/root-cause-scenarios-recurrence.md`を全文読む |
| 原因分析の書式を使う前 | `agent-toolkit:wi-standards/references/analysis-records.md` |
| 原因分析の処置を記録する前 | `agent-basics:bugfix/references/root-cause-actions-hierarchy.md` |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` |
| 保存する本文の共通品質を判定する前 | `agent-toolkit:delegation/references/writing.md` |
| エージェント向け文書を追加・変更するAWIを起草する前 | `agent-basics:writing-standards/references/agent-documents-additions.md` |

## 投入

1. 付随型エージェントは、同一スレッド内で`保存開始`と検収済みの要求識別子を受け取った対象集合を保存する。
   専念型エージェントは、`drafting-workflow-review-route.md`「直接起草と独立した検収の選択」で確定した検収を完了した対象集合を保存する。
   保存直前に原稿の現行本文を確認し、`drafting-completion-duplicates.md`「重複判定」が再照会を要求する場合はその結果も確認する。
   検収完了後に変更が生じていた場合は保存せずに修正へ戻る。
   先行する項目を後続の項目よりも先に保存し、確定した正式ファイル名を対応付ける。
   検収完了後の差分が、検収済みの参照関係に基づいて先行項目の未確定名を正式ファイル名へ置換したのみである場合は、作成主体が置換後の本文と依存関係の指定を先行項目の保存結果と比較し、完全に一致していれば同一の検収結果として保存へ進む。
   参照先、依存関係の必要性、要求内容のいずれかに変更が及ぶ差分がある場合は、修正と検収のプロセスへ戻す。
   ユーザー固有の判断事項に関する回答待ちがある場合は、保存指示を受けた場合であっても回答が得られるまで保存を保留する。
   投入は要求ごとに1回のみ実行する。
   `ファイル名`を受け取った要求については新規投入せず、`atk wi show`で取得した現行本文を基に改訂した上で、`atk wi edit --body-file`を用いて保存する。
   警告やエラーを検出した場合は、同一手順で問題を解消して再投入を試み、自力で修復できない要求については「続行できない理由」を報告した上で、他の要求の投入作業を継続する。

2. 既存項目の更新に伴って保留（hold）した項目について、専念型では作成主体が保存本文の一致確認が成功した後に解除（unhold）まで行い、付随型では委譲元が保存結果の検収を完了した後に解除する。
   ユーザーの回答待ちや起草・保存の失敗が発生している間は、保留状態を維持する。
   既存項目を変更せずに再利用する場合や取り下げる場合は、状態管理の規約に従って必要な更新と採否を確定し、未解決の懸念が残る間は保留を維持する。
   誤って投入した項目の削除やholdからの終了処理は、states-and-dependencies.md「保留と本文修正」「終了」に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| WIの調査・起草・保存の共通手順：直接起草と独立した検収の選択の基準を適用する前 | `drafting-workflow-review-route.md` |

# レビューイー側心得：公開可能性の検証

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 指摘前の判断記録と比較する前 | `exec-review-recording.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/exec-review-recording.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |
| 調査の停滞や修正による症状・回避処理の増加に対処する前 | `../../bugfix/references/root-cause-scenarios-recurrence.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/root-cause-scenarios-recurrence.md`、`../../bugfix/references/root-cause-remediation.md`、`../../bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-recurrence.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |

## 公開可能性の検証

**8.** 解消と公開可能性を確かめるため、次の項目を検証する。

- 採用した各是正要求等について、問題の再現条件と違反規定を修正後の成果物へ適用し、解消を確認する。
  あわせて、変更全体が元の目的、明示要件、公開契約、維持・除外対象、実績のある成功手順を維持していることを確かめる
- 是正を確定する前に、修正によって新しく記述した内容に対しても、対象の作成規範と該当するレビュー限定基準を適用して点検する（再レビューを省略する修正でも同様に行う）。
  点検では、確度、因果関係、情報の出所、例示の開放性などを確認する。
  推測を事実として書いた記述、未検証のまま一律に帰属させた記述、エージェント自身の判断をユーザー由来に見せかけた記述、ユーザーの発話範囲を勝手に限定したり確度を外したりした記述は除外する
- レビュー限定基準に基づく指摘は、作成時に必読とされていなかったことだけを理由に却下せず、`review-only-criteria.md`「指摘と採否」に沿って原文、適用条件、現物から適切に採否を判断する。
  基準の名前を挙げるだけでは修正の認可にはならない
- 対象の選択条件や解決条件を変更した場合は、判定に使われるすべての入力とすべての分岐を確認し、対象集合に過不足がないこと、および変更しない入力に対する結果が変わらないことを確かめる。
  追加または削除した条件を方式全体に当てはめ、局所の修正によって前提が変わる利用側、コメント、状態についても、再提出前の確認対象に含める
- 直近の関連テスト、lint、format、型チェック、生成物の同期、および呼び出し文脈で定められた全体検証のうち該当するものを実行する。
  失敗、警告、検証不足、新たな問題が残っておらず、直ちに公開しても障害を起こさないと説明できない場合は確定を避け、不足している点を委譲元へ報告する

## 方式見直しと再レビュー

**9.** 修正を収束させるため、次の基準で方式と再レビューの要否を決める。
第3ラウンド以降も未解決なら全ラウンドの指摘と応答を読み、局所修正の前に`agent-basics:bugfix`の「反復した指摘の原因判定」と`agent-basics:writing-standards`の「問題と手段の比例性」で方式の維持・縮小・置換を比べる。
維持なら方式に起因しない独立した欠陥の根拠、縮小・置換なら認可内の根拠を残す。
方式の置換直後は開始時点からの全差分を再レビューする。
停滞や症状の転化を認めた場合は、第3ラウンドを待たず`../../bugfix/references/root-cause-scenarios-recurrence.md`「同一症状の修正反復」を適用する。
次のいずれかに該当する修正は、再レビューを実施する。

- 根拠の取得を待っている状態
- 修正に関連する要求が未達成、または根拠不足であることの再判定
- 修正との関係性を確定できない箇所
- 会話履歴が失われた状態からの再開
- 通常の要件や仕様に影響する修正、および判断に迷う修正

これらに該当しない場合は、以下のいずれかを満たし、公開可能性の検証と採否・根拠の記録を完了していれば、再レビューを省略できる。

- 意味、動作、契約を変更しない機械的な修正のみである
- 詳細や実装に関する指摘への対応にとどまり、公開契約、外部から見える挙動、実装方針を変更しない
- プロジェクトが定めている低リスク段階に該当する

低リスク段階においては、作業開始時点からの全差分を確認する。

**10.** 次の担当が判定できるよう、採否・根拠と修正・検証結果を応答に記録し、必要な再レビューへ提出する。
 追加発話や新たな指摘は手順1へ戻って受領し、根拠と採否を再確認する。
 応答後も元の未完了作業を続ける

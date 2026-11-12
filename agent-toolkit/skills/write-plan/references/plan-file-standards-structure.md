# 計画ファイル作成基準：計画ファイルの構成

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-remediation.md` |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-severity.md` |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-actions.md` |
| 参照基準の適用前 | `agent-basics:bugfix/references/root-cause-prevention.md` |
| 参照基準の適用前 | `../../../share/exec-review-responses.md` |
| 参照基準の適用前 | `../../process-wi/SKILL.md` |

## 計画ファイルの構成

起草や更新の前に現行の作業契約を確認し、`share/work-contract.md`「工程境界での要求と成果の確認」に従って、計画の対象と維持する対象が当初の目的と現在の要求に一致するかを確かめる。
要求が変わったら作業契約へ反映し、計画凍結後は既存のレビュー指摘管理表と`review_contract`の原本を保持する。

H1の主題に続けて、次のH2見出しを順に置く。

1. `## 概要`
2. `## 実施内容`
3. `## 要件・外部仕様`
4. `## 恒久化・リファクタリング`
5. `## 変更履歴`
6. `## 検証`
7. `## 公開工程`
8. `## 進捗ログ`

内部方式は要件直後の任意H2`## 設計・実装方針`へ記録できる。
旧8節も読み取る。
任意名のH3見出しは`## 要件・外部仕様`、`## 設計・実装方針`と`## 変更履歴`の配下に置き、H4以下は使わない。
事実・数値・要約は1箇所に集約し節名で参照する（努力目標。矛盾防止）。
規範の適用範囲は文書パス・節名で示す。

関連WIはファイル名・1行要約を記し、確定済みの目的・要件・原因分析・範囲・方式・成立根拠を参照する。
共通参照は読込表に従い指定箇所・ユーザー記入欄を読み、固有本文と採否・計画を確定する。
完成状態は目的・反映内容・原因分析・補足条件から導く。
任意節・引用位置の省略だけで再起草・再調査しない。
計画で変更対象の分解・外部仕様・受入シナリオ・テスト・検証コマンド・恒久化・リファクタリング・公開工程を具体化する。
WIの再導出は`関連WI: なし`・対応節欠落・現物との乖離確認時に限り、影響項目だけに行う。
完成条件と手段の必要性を現在の目的・方針で判定することは再導出に当たらず、`plan-file-standards-scope.md`「実施内容」に従って常に行う。
乖離の事実・結論を`## 変更履歴`へ記録する。
人間由来の要求範囲変更は`../../wi-standards/references/wi-standards-origin.md`「由来と承認」、原因分析の乖離は`plan-file-standards-verification.md`「バグ調査」で扱う。
次の基準への不適合も、原因分析の乖離に含む。
母集団は`agent-basics:bugfix/references/root-cause-remediation.md`、QとC・Dは`agent-basics:bugfix/references/root-cause-severity.md`を適用する。
適用レイヤーは`agent-basics:bugfix/references/root-cause-actions.md`、完了判定は`agent-basics:bugfix/references/root-cause-prevention.md`に従う。

キューにない素材の原文とユーザーの回答全文は計画外入力の原本として保持し、調査、起草、初回レビュー、再レビューの各工程へ原文の参照を渡す。
計画本文へは全文を転記しない。
変更履歴に記録するユーザー発言は同節の書式に従う。

実装着手前の指摘対応・要件追加・方針変更は各節へ統合し、最終結論を残して経緯を`## 変更履歴`へ書く。
`ユーザー指示`・`人間由来のWI`の`実施内容`欄は、`../../../share/exec-review-responses.md`「安全な修正」に従って原文を保ち、修正案を別行へ加える。
実装着手後は計画ファイルを変更しない（以下「計画凍結」）。
実装中に確定した要件変更と、記述の不整合に採った解釈はレビュー指摘管理表に記録する。
実行工程で直すと実行レビューの指摘になり、不要な往復が生じるためである。
例外は`## 進捗ログ`への追記だけとする。
凍結後に検証条件を訂正するときは、`plan-file-standards-verification.md`「検証と公開工程」の訂正済み検証表に反映する。

## 概要と計画メタ情報

全体像・目的・対象範囲の順に簡潔に書き、変更時は全体を書き直す。
`../../wi-standards/references/drafting-completion-evidence.md`「既存証拠の再利用」に従う。

直下のH3見出しは`### 計画メタ情報`だけとし、項目と順序は`plan-file-standards-template.md`「初回起草の雛形」に従う。
コロンはASCIIの`:`とし、後に半角スペースを1つ置く。
`関連WI`と`作業種別`以外の値はバッククォートで囲む。
計画ファイル（バグ）を作る場合だけ、末尾に``- 計画ファイル（バグ）: `~/.claude/plans/__PLAN_STEM__.bugs.md` ``を加える。

- `作業種別`は`バグ対応`か`通常変更`とする。
  バグ対応の要求単位が1件でもあれば`バグ対応`とし、計画ファイル（バグ）にはバグ対応の要求単位の項目だけを置く。
  `## 検証`の`変更範囲の検証`は全要求単位の変更対象を覆う
- `関連WI`には入力のAWIとUWIのファイル名を1件1行で書き、コロンに続けて1行要約を添える。
  入力WIがなければ`- 関連WI: なし`とする
- `対象リポジトリ`は、計画構造の自動チェックツールへ渡す`--work-dir`のGitルートと同じ値にする（`agent-toolkit:process-wi`のレーンでは専用worktreeの絶対パス）

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| AWIとUWIの共通規範の基準を適用する前 | `../../wi-standards/SKILL.md` |
| 計画ファイル作成基準：初回起草の雛形の基準を適用する前 | `plan-file-standards-template.md` |
| 計画ファイル作成基準：検証と公開工程の基準を適用する前 | `plan-file-standards-verification.md` |

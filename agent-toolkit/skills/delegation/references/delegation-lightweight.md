# 委譲：explore・shellの起動

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲プロンプトや返却の文章を作成・検収する前 | `quality-standards.md` |
| explore・shellの入力、権限、受領を確定する前 | `base-contract.md`、`mandatory-rules.md`、`sending.md`、`receiving.md` |
| 作業全体の分割を判定する前 | `routing.md` |
| 委譲先のモデルを選ぶ前 | `process-model-settings.md` |
| 完了通知の処理、ならびに待機や停滞の判定を行う時 | `waiting-and-monitoring.md` |
| 結果本文の保存期間と回収手順を決める前 | `agent-toolkit:managed-temp/SKILL.md` |

## explore・shellの起動

個別の調査やコマンド実行を自身で行うか、explore・shellへ委譲するかは、agents_serverのserver instructionsに記載されている採算の目安に基づいて判断する。
`routing.md`の全文読込は、作業全体の分割を判定する際に行う。
両モードの起動から受領までに満たすべき必須の要件は本節で定める。
括弧内の参照先は詳細情報の所在を示すものであり、実際の読込義務は前述の読込表の指定に従う。

1. 全体点検する際は、`routing-investigation.md`「コンテキスト消費が大きい調査の切り出し」に基づき、起動・初回読込・圧縮後再読・検収を含む総費用と分担の利点を比較する。
   公開操作、共通定義、呼び出し元、必要な情報量から担当範囲を定め、全体の管理と統合を委譲元が担う。

2. 起動前に対象、読取・書込の権限、完了条件、返却すべき観測値を確定する。
   委譲先が読み込む既存ファイルの絶対パスの実在、比較基準、実行する版を確認する。
   分量の多い調査では読取ツールの範囲指定を渡し、出力の切り詰めや上限到達を網羅性の根拠として扱わない（base-contract.md「起動前の確認」「分離実行の受領物」）。

3. 作業を分割する場合は`routing-decisions.md`「要否判定」を用い、モデルを選択する場合は`process-model-settings.md`「工程別モデル設定」に従う。
   明示的な指定がない場合は工程別の設定値を使用し、WIにモデル指定がある場合はそれを優先する。
   受領時には、指定値、起動候補、観測された実際の値を区別して扱う。

4. exploreは読取専用とする。
   shellの書込先は、生成物も含めて他の担当と重複しない範囲に限定し、同一担当の交代は先行する担当の終了後に実施する。
   同一worktreeでの書込の分担、作業ツリー全体へ影響する操作の直列化、別worktreeへ分離する条件は`mandatory-rules.md`「作業ツリーの保護」に従う。
   軽量起動では自動配送されないプロジェクト固有の指示や、コマンドの入力境界、終了状態、標準出力・標準エラー出力の保持を確実に引き渡す（base-contract.md「起動前の確認」、sending.md「権限と成果物の取り決め」）。

5. 入力は命令1文と必要な値（元の要求、対象、完了条件、返却形式、基準記録）にとどめ、すでに配布済みの制約、経緯、推測、既存記録の転記は含めない。
   自身で裏付けをとっていない値は未検証情報として明示する。
   exploreは読取専用として結果を返し、後続の入力には`atk agents wait`が保存した`agent_message_path`を直接指定する（base-contract.md「入力の適格性と追送」、sending.md「委譲プロンプトの構成」「未検証情報の取り扱い」）。

6. 各モードの入力項目や機能は、公開されている`start`の説明とスキーマで確認し、`cwd`に作業ディレクトリの絶対パスを渡す。
   exploreでは`prompt`を、shellでは`command`と`summary_policy`を使用する。
   スキルの手順を必要とする作業は`task`または`delegate`に委ねる（sending.md「起動と観測」）。

7. 返却された`session_id`を保持し、`atk agents wait`を用いて終了状態と結果本文を受け取る。
   待機の実行形式、終了コード、バックグラウンド化、通知後の受領手順は、読込表にある`waiting-and-monitoring.md`に従い、ホスト別の待機手順は後述の「軽量起動のホスト別待機」に従う。

8. 終了後に対象ファイルの差分、実行結果、診断情報を確認し、返却形式ならびに対象・権限・完了条件との整合性を検収する。
   件数、不在、網羅性は、省略のない取得結果に基づいて判定し、事実の主張は実物の根拠で裏付ける。
   受領前の段階で得られた情報のみで完了とみなさず、不足がある場合は同一担当へ差し戻す（receiving.md「返却形式の適合確認」「終了と報告内容の確認」、sending.md「権限と成果物の取り決め」）。

   結果本文の再保存は行わず、後続処理が参照する`agent_message_path`や待機実行の保存成果物は、参照が完了するまで保持する。
   保存期間と回収の手順は`agent-toolkit:managed-temp`に従う。

### 軽量起動のホスト別待機

Codexは`codex-runtime-waiting.md`「agents_serverの二層待機」、Claude Codeは`claude-code-runtime-waiting.md`「待機中の定期再確認と背景転換」に従う。
軽量起動にも同じ識別子保持、回収、定期再確認の設定・再利用・削除条件を適用する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| Claude Codeでの委譲の実装：検証コマンドの実行形態の基準を適用する前 | `claude-code-runtime-waiting.md` |
| Codexでの委譲方法：agents_serverの二層待機の基準を適用する前 | `codex-runtime-waiting.md` |
| 委譲の要否判定と委譲単位の確定：要否判定の基準を適用する前 | `routing-decisions.md` |
| 委譲の要否判定と委譲単位の確定：コンテキスト消費が大きい調査の切り出しの基準を適用する前 | `routing-investigation.md` |

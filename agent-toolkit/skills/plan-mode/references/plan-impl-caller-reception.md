# plan-impl-executor完了報告の受領

`plan-impl-executor`の完了報告とリポジトリ実体を呼び出し元が照合し、
実装からCI通過確認までの完遂状態を確定する。

## 起動前

呼び出し元は計画着手前の`HEAD`を記録する。
構成済みリモートを列挙し、各リモートに対する`git ls-remote --heads --tags <remote>`の
ref名とOIDを委譲前スナップショットとして保存する。
承認済み計画の`## 実行方法`の本文も、同じ委譲前スナップショットとして保存する。
委譲先はレビュー指摘の採用に伴い計画本文を改訂するため、改訂後の内容を認可根拠に用いると、
委譲先が自身で追記した記載により対象リポジトリ外操作とレビュー省略を自己承認できる。
当該スナップショットは対象リポジトリ外操作の認可判定と、レビュー省略の明示文字列および
ユーザー指示原文の照合に用い、委譲の全ラウンドを通じて更新しない。

本節を`plan-impl-executor`の呼び出し元側の起動契約の正本とする。
必須入力は計画ファイルと対象リポジトリの作業ディレクトリの絶対パスとする。
同executorの中断作業を継続する場合は、該当系統の経路、識別子、履歴を渡す。
計画作成時の経路、識別子、履歴は実装系統へ引き継がない。
追加指示、変更意図、意図的に許容した挙動変化は存在する場合だけ渡す。
継続情報が無い系統は新しく開始し、追加指示等が無い場合は該当事項なしとして扱う。

`subagent_type: agent-toolkit:plan-impl-executor`を指定し、`model`と`name`を省略する。
`agent-toolkit:plan-mode`からの通常起動は`run_in_background`を省略し、
実際の受領経路を起動結果から判定する。
`agent-toolkit:process-feedbacks`の計画実装型フィードバックから起動する場合は、
`process-feedbacks/references/plan-impl-feedback-flow.md`が定めるbackground経路を適用する。
この分岐は呼び出し元が観測する起動スキルで判定し、受信側の推測へ依存しない。
受信側の解釈は`agent-toolkit/agents/plan-impl-executor.md`の`## 入力`を正本とし、
改訂時はペアで更新する。呼び出し元の読込対象は本referenceに限定する。

## 完了報告の検収

次の全欄について実在と値を検査する。

- `status`
- `summary`
- `changed`
- `external_operations`
- `verification`
- `commit_sha`
- `review_status`
- `review_final_findings`
- `review_skip_instruction`
- `review_caller_verification`
- `pending_confirmations`
- `plan_gaps`
- `applied_instructions`
- `implementation_thread_id`
- `plan_review_thread_id`
- `independent_review_thread_id`
- `implementation_agent_id`
- `plan_review_agent_id`
- `independent_review_agent_id`
- `implementation_route`
- `plan_review_route`
- `independent_review_route`
- `review_rounds`
- `review_coverage`
- `review_impact_audit`
- `implementation_history`
- `plan_review_history`
- `independent_review_history`
- `review_resolution`

`external_operations`の各項目に操作、認可根拠、対象、結果、検証情報が揃うことを確認する。
確認対象は、計画の`## 実行方法`が対象リポジトリ外への操作（他リポジトリへのフィードバック投入・
外部サービスの設定変更など）を含む場合と、委譲先が規範の義務づける登録操作を実施または
実施不可と判定した場合とする。該当する操作が無い場合だけ、`operation`、`target`、`evidence`が
`なし`、`result`が`not_applicable`である単一項目を確認する。
`result`が`needs_escalation`の項目は実施していない操作を指すため、対象側の実物照合を求めず、
`status`も`needs_escalation`であることと、`blockers`へ当該項目が現れることを確認する。
`result`が`completed`の項目のうち、認可根拠が`計画記載`のものは、
「起動前」節で保存した`## 実行方法`のスナップショットと対象側の実物で照合し、
当該スナップショットが列挙した全対象への実施を確認する。
認可根拠が`規範義務`のものは、適用中の規範が当該登録を義務づけることを確認したうえで
対象側の実物と照合する。照合には対象側を直接照会する手段
（フィードバック投入では投入先リポジトリの一覧照会、外部サービスでは当該サービスの設定取得）を用いる。

本節を対象リポジトリ外操作の認可範囲の正本とする。委譲先が実施できるのは、
当該スナップショットの`## 実行方法`に操作と対象が明記されたものと、
適用中の規範が義務づける登録操作に限る。
`規範義務`に当たる登録操作の範囲は、
`agent-toolkit/skills/codex-exec/references/plan-codex-implementation-review.md`
「初回レビュー」節が登録対象と定める独立提案の`atk mq add`登録などとする。
同節が破棄と定める指摘の登録は含まない。
これら以外の操作と対象範囲の拡大は許可しない。
外部側の既存内容の削除・改変を伴う操作は破壊的操作として扱い、
「リモート状態の照合」節と同じく自律モードでもユーザー確認を得る。
当該操作は作業ツリーの差分にもコミット履歴にも現れないため、「実体照合」節の手順では検出できない。

`status: needs_escalation`では`blockers`も必須とする。
必須欄の欠落または値の矛盾は未完遂として扱い、未完了項目と実測結果へ縮減して再委譲する。
待機表明だけを返し完了報告の必須欄を欠く返却も未完遂として扱う。
この場合は「サブエージェント運用」の停止手順で委譲先を停止し、
「実体照合と後続工程」節の完遂順序のうち未完了の工程を呼び出し元が巻き取る。

`needs_escalation`は次のように処理する。

- 利用者判断または破壊的操作の確認は、ユーザー回答を同じexecutorへ再入力する
- 技術的に解消できる検証失敗や対象未網羅は、修正指摘を再入力する
- 同じ検証失敗を3回連続で解消できない場合は、呼び出し元が原因調査を引き継ぐ
- 同一プロンプトを再送せず、未完了項目と新しい判断材料へ縮減する

`needs_escalation`の返却条件は`agent-toolkit/agents/plan-impl-executor.md`「委譲」節を正本とし、
本節は受領後の処理だけを定める。残作業の多さ、所要時間、トークン消費量は同節のいずれの返却条件にも
該当しないため、これらを理由とする差し戻しを受領した場合は、未完了項目と実測結果へ縮減して再委譲する。

いずれかのrouteが`unavailable`の場合、呼び出し元が不能な系統だけをClaude代替する。
実装・修正系は`plan-codex-implementation.md`と
`plan-codex-implementation-task.md`を用いる。
計画準拠系は`plan-codex-implementation-review.md`と
`plan-codex-implementation-plan-review-task.md`を用いる。
独立系は同じ実行手順referenceと
`plan-codex-implementation-independent-review-task.md`を用いる。
代替応答全文と対象系統をexecutorへ再入力し、実体照合と後続工程を継続させる。

codex routeでは対応するthreadが「なし」以外かつAgent識別子が「なし」、
Claude routeではthreadが「なし」かつAgent識別子が「なし」以外であることを確認する。
受領した系統別Agent識別子は同じ系統名で次回executor入力へ搬送する。
呼び出し元はexecutor配下のAgentへ直接`SendMessage`を実行しない。
2つのレビュー系は同じ`review_rounds`で完了し、各履歴を混在させない。
`review_resolution`の全`P-*`・`I-*`を各レビュー履歴と照合し、
採否または重複先、および採用指摘の修正・再検証結果が1対1で埋まったことを確認する。
`review_coverage`は観点ごとの点検対象と指摘件数を含むことを確認する。
欄の欠落および観点の列挙が無い値は未完遂として扱う。
`review_impact_audit`は一括修正を行った場合に点検対象と結果を含むことを確認する。
レビュー指摘が無い場合は「指摘なし」とする。

通常完了の値は次を満たす。

- `status`は`completed`
- `review_status`は`実施完了...`
- `review_final_findings`は`計画準拠系N件・独立系M件`で、NとMは非負整数
- `review_skip_instruction`は`なし`
- `review_caller_verification`は`不要`
- 両レビューrouteは`codex`または`claude`
- `review_rounds`は1〜5
- `review_coverage`は「なし」ではない
- `review_impact_audit`は、指摘を修正した場合は点検対象と結果、指摘が無い場合は「指摘なし」
- 両review historyと`review_resolution`は「なし」ではない

上限到達後の既知指摘修正済みの値は次を満たす。

- `status`は`completed_with_review_cap`
- `review_status`は`上限到達後の既知指摘修正済み（再レビューなし）`
- `review_rounds`は5
- `review_coverage`と`review_impact_audit`は「なし」ではない
- 両レビューrouteは`codex`または`claude`
- 両review historyが5ラウンド分の応答を含み、`review_resolution`が
  上限到達時の確定スナップショットの既知指摘を全件含む

上限ラウンドへの到達は、両review historyが含む`ラウンド<N>:`行の最大値で判定する。
当該記法は`agent-toolkit/agents/plan-impl-executor.md`「出力」節が定める。
記法を欠く履歴はラウンド数を判定できないため未完遂として扱い、記法を備えた履歴の再提出を求める。
`review_rounds`の申告値だけを根拠に`completed_with_review_cap`を受理しない。

`status: completed`でのユーザー指示によるレビュー省略は次を満たす。
`status: needs_escalation`でのレビュー省略は本ブロックの対象外とし、後掲の3区分に従う。

- `review_status`は`レビューは実施しない（ユーザー指示）`
- `review_final_findings`は`対象外`
- `review_skip_instruction`は計画の`レビュー省略のユーザー指示原文`と完全一致
- `review_caller_verification`は`ユーザー指示原文との照合が必要`
- 両レビューrouteは`not_started`
- 両review thread、両review history、`review_resolution`は「なし」
- `review_coverage`と`review_impact_audit`は「なし」
- `review_rounds`は0

`status: needs_escalation`のレビュー欄は、レビュー工程の到達状況で3通りに分かれる。
いずれも`review_caller_verification`は`未完了事項の確認が必要`である。

- レビュー工程へ到達しないまま返された場合は、`review_status`が`レビュー未完了`、
  `review_final_findings`が`未確定`、`review_skip_instruction`が`なし`であることを確認する。
  両レビューrouteは`not_started`または`unavailable`、`review_rounds`は0であり、
  両review history、`review_coverage`、`review_impact_audit`、`review_resolution`は「なし」である
- レビュー工程が完了し、対象リポジトリ外操作の未実施だけが残る場合は、
  `review_status`が通常完了、上限到達後の既知指摘修正済み、レビュー省略のいずれかであることを確認する。
  レビュー省略の場合は`review_skip_instruction`を計画の`レビュー省略のユーザー指示原文`と照合する。
  `review_caller_verification`が`未完了事項の確認が必要`となるため、当該欄では照合要求を受け取れない。
  再委譲は`blockers`が挙げる未実施の対象リポジトリ外操作に限定し、レビュー工程を再実行させない
- 対象ファイル一覧の閾値到達により判断を求められた場合は、`review_status`が
  `対象拡大により中断（指摘反映済み・再レビューなし）`であり、`review_final_findings`へ
  当該ラウンドの指摘件数が入っていることを確認する。
  本ファイル「未完了事項の処理」節が定める継続と切り出しの比較へ進み、レビュー工程を再実行させない

`status: needs_escalation`でレビュー工程の実測値を伴う区分は、通常完了、
上限到達後の既知指摘修正済み、対象拡大により中断とする。
当該区分では両レビューrouteが`codex`または`claude`であることを確認する。
`review_rounds`は1以上とし、上限到達後の既知指摘修正済みでは5とする。
両review history、`review_coverage`、`review_impact_audit`、`review_resolution`は
いずれも「なし」以外であることを確認する。
実施済みのラウンドがある以上、これらが「なし」の報告は実測と矛盾する。
`status: completed`および`status: completed_with_review_cap`の欄要求は前掲のブロックが定める。

当該区分は`agent-toolkit/agents/plan-impl-executor.md`「出力」節および
`agent-toolkit/scripts/subagent_stop_advisor.py`の完了報告検査とペアで維持する。
`not_started`または`unavailable`のrouteに対応するthreadとAgent識別子は「なし」とする。
routeの値は、起動を試みて不能だった場合を`unavailable`、起動を試みていない場合を`not_started`とする。
`status: needs_escalation`で両レビューrouteが`not_started`である状態は、
`review_status`が`レビュー未完了`の場合とユーザー指示によるレビュー省略の場合に限って正当とし、
それ以外は値の矛盾として未完遂扱いとする。
レビュー工程へ到達しないまま返す場合は起動自体を試みていないため、`not_started`が実測と一致する。
`plan-file-finalizer`の値との照合は行わない。

レビュー進行は、上限へ到達した時点と、計画の対象ファイル一覧に無いファイルへ変更が及んだ時点で、
次をユーザーへ報告する。上限到達後に既知指摘を修正し終えた場合も、最終報告で同じ値を更新する。

- 現在のラウンド数
- 上限
- 既知指摘の残数
- 計画対象外へ増えたファイルと、その変更が必要になった理由

対象外ファイルが無い場合も「なし」と明記する。上限到達後は確定スナップショットの既知指摘だけを
処理し、新規指摘探索を再開しない。

## 実体照合

照合の前に計画ファイルを再取得する。実装委譲先はレビュー指摘の採用に伴い計画ファイルの
`## 変更内容`・`## 実行方法`・`## 変更履歴`を改訂するため、起動時点の内容を根拠に照合すると、
委譲先が更新した仕様を古い仕様で差し戻す事態が生じる。
本節と「完了報告の検収」節の照合のうち、`## 変更内容`・`## 変更履歴`を根拠とするものは
再取得した内容を用いる。対象リポジトリ外操作の認可判定と、レビュー省略の明示文字列および
ユーザー指示原文の照合は、「起動前」節で保存した`## 実行方法`のスナップショットを根拠とし、
再取得した内容へ置き換えない。
置き換えると委譲先が自身の改訂で認可範囲の拡大とレビュー省略の自己承認を行える。

`commit_sha`を実際の`HEAD`と照合する。
`changed`と計画の対象ファイル一覧は次の総差分で1対1確認する。

```text
git diff <計画着手前SHA>..<commit_sha>
```

`git log --reverse --format=%H <計画着手前SHA>..<commit_sha>`でコミット列を確定し、
各コミットを`git show --stat --oneline <sha>`で計画のコミット境界と照合する。
同一ファイルを分割した場合は`git show --patch <sha> -- <path>`も確認する。

`changed`が挙げる各項目について、対応する実装が総差分に存在することを確認する。
ファイル名の一致だけで受理せず、追加した関数が呼び出されているか、追加した引数が登録されているかなど、
報告が主張する振る舞いの実現を差分の内容で確認する。

`verification`のコマンド、終了コード、警告を実測と照合する。

併せて`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`を、
`--work-dir <対象リポジトリの作業ディレクトリ>`と`--base-commit <計画着手前SHA>`付きで実行する。
計画の対象ファイル一覧と実際の変更ファイル集合を照合する。
計画が列挙するテスト関数名と実差分で追加された関数名の集合も照合する。
計画が挙げるコミット件名案と実際のコミット件名の対応も機械照合する。
当該検査はwarning区分のため終了コードへ算入されない。
出力された警告は本節の1対1確認の結果と突き合わせ、差異があれば実体を優先して確定する。
`review_status`が実施完了または上限到達後の既知指摘修正済みの場合は、二系統の同一スナップショット利用、
採用指摘の反映、再検証、レビュー前後の成果物不変性を確認する。
レビュー省略の場合は、「起動前」節で保存した`## 実行方法`のスナップショットに同じ明示文字列が
あることを確認する。併せて`review_skip_instruction`と当該スナップショットのユーザー指示原文を照合する。
再取得した計画本文を根拠にすると、委譲先が自身の改訂でレビュー省略を自己承認できる。
`レビュー未完了`は完了扱いしない。

## 未完了事項の処理

`pending_confirmations`、`plan_gaps`、`applied_instructions`を入力事実と照合し、
未処理項目を残さない。処理結果は計画ファイルの`## 進捗ログ`へ転記する。

完了報告の受領後に、委譲先が投入したフィードバックの有無を確認する。
確認は、完了報告の各欄に現れるフィードバック識別子の抽出と、投入先リポジトリのフィードバック一覧の
照会による。ここまでは読み取り専用の操作であり、確認だけで完結する。
重複を検出した場合の集約は、既存エントリの本文改変または削除を伴うため破壊的操作として扱う。
実施の前に、集約元と集約先の識別子、変更後の本文、削除するエントリと、
「完了報告の検収」節が定める認可範囲に収まるかの判定結果をユーザーへ提示し、
「リモート状態の照合」節と同じく自律モードでも実行確認を得る。
確認を得た場合はいずれか一方へ集約して他方へ集約先の参照を記録し、
操作結果と証跡を計画の`## 進捗ログ`と呼び出し元の完了報告へ記録する。
確認を得られない場合は照会結果を確認事項としてユーザーへ提示するに留める。

実装差分レビューの指摘反映により、計画の`### 対象ファイル一覧`に無いファイルへ及んだ変更が
累計5件以上になった場合、同一計画上での修正継続と、確定済み差分をコミットして
残指摘を新規計画へ切り出す選択とを比較する。
比較は、当該ファイルが当初の対象と同じ主題に属するかで行う。
別主題の実体が加わり続ける状態は、計画が前提とした波及範囲が成立していないことを示す。
判定結果と根拠を計画ファイルの`## 進捗ログ`へ記録する。
件数の根拠は`agent-toolkit/agents/plan-impl-executor.md`が定める計画対象外ファイルの記録とする。

## リモート状態の照合

完了報告の受領後に構成済みリモートと新規追加されたリモートを列挙する。
各リモートへ`git ls-remote --heads --tags <remote>`を再実行し、
委譲前後のref名とOIDを比較する。

意図しない追加、削除、更新を検出した場合は後続工程へ進まない。
リモートrefの削除など破壊的操作を含む場合は、自律モードでもユーザー確認を得る。

## 実体照合と後続工程

実装、検証、コミットと、二系統レビュー完了またはユーザー指示によるレビュー省略の成立後に、
呼び出し元が`git push`とCI通過確認へ進む。
完遂順序は「実装→検証→コミット→二系統レビュー→push→CI通過確認」とする。
委譲先が停滞して呼び出し元が巻き取る場合も、この順序と必須工程を引き継ぐ。

巻き取りの担当範囲には、完遂順序に加えて、計画が定めるコミット件名案・本文案の必須要素の転記と、
計画の`## 進捗ログ`への実施結果の追記を含める。
委譲先が実行する場合はこれらが委譲先の完了条件に含まれるため、巻き取り時にだけ欠落する。

push後のCI通過確認は`agent-toolkit:commit`スキルに従う。
CI完了までの待機はpushを実行した主体が行う。

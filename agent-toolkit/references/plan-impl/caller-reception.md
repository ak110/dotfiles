# plan-impl-executor呼び出し元の起動前準備・完了報告の受領口

`agent-toolkit/agents/plan-impl-executor.md`から参照されるSSOTとする。
本ファイルは`plan-impl-executor`起動前の準備と完了報告の受領後の手順を定める。
呼び出し時に渡す引数は`plan-impl-executor.md`のdescriptionに従う。
引数は計画ファイルの絶対パス、プロジェクトルートの絶対パス、追加指示（任意）とする。

起動前の準備: 呼び出し元はAgentツールで`agent-toolkit:plan-impl-executor`を起動する直前に
`git rev-parse HEAD`を実行する。
結果を計画着手前SHAとして記録する（手順2の照合に使う）。

呼び出し元は`plan-impl-executor`の完了報告を受領した後、次の手順を実施する。

0. 報告本文の書式適合性を検査する。
   検査対象欄は`agent-toolkit/agents/plan-impl-executor.md`「出力」節が定義する主要欄とする。
   主要欄は`status:`・`summary:`・`changed:`・`verification:`・`commit_sha:`・`review_handoff:`・
   `pending_confirmations:`・`plan_gaps:`を指す。
   `status: needs_escalation`の場合は`blockers:`欄も必須欄として検査する。
   いずれかを明示形式で含まない場合は、`status: completed`表示があっても未完遂扱いとして再委譲へ回す。
   書式不備の完了報告は`blockers`欄も欠落する可能性があるため、
   手順1の`needs_escalation`分岐評価を経ず、技術的に解消可能な実装不備の再委譲へ直行する。
1. `status: completed`を確認する。`needs_escalation`の場合は`blockers`欄の内容で分岐する。
   - ユーザー判断・破壊的操作の確認を要する内容: `AskUserQuestion`でユーザーへ確認する。
     確認結果に応じて、縮減した新規`agent-toolkit:plan-impl-executor`起動プロンプト（元計画ファイルパス・
     未完了項目・確認結果を明記）で再委譲する
   - 技術的に解消可能な実装不備（検証失敗・対象ファイル未網羅等）: `AskUserQuestion`を経由しない。
     縮減した新規`agent-toolkit:plan-impl-executor`起動プロンプト（元計画ファイルパス・未完了項目・修正指摘を
     明記）で再委譲する
   - いずれの分岐でも同一プロンプトでの再委譲は禁止する
   - `needs_escalation`受領時（並列委譲時を含む）は次工程（レビュー結果の確認・後続計画）へ進む前に完遂する。
     ブロック要因が担当範囲外への依存であれば当該依存を計画ファイル本文へ補完する。
     その後、縮減した新規起動プロンプトで再委譲するか呼び出し元が直接実装して完遂する
   - named background subagentの完了判定はSendMessageによる問い合わせに依存せず、
     `git log`・`git status`・作業ツリー・commit SHAなどの観測可能事象で先に判定する。
     `idle_notification`受信時も同様に観測事実で判定し、SendMessageは観測事実で判定不能な場合に限定する
    （実質ハングを避けるため）。CI通過確認等の外部プロセス完了待ちのポーリングは本項の対象外とする。
     ただし、呼び出し元が定める切替条件下で当該named background subagentから
     別の`plan-impl-executor`起動へ引き継ぐ場合は例外とする。
     この引き継ぎ時の完了判定は完了報告本文の受領を必須とする。
     観測可能な事象単独では判定しない。
    （切替条件の詳細は`agent-toolkit/skills/process-feedbacks/references/plan-impl-feedback-flow.md`
     「混在時の並行制御」節に従う）
2. `changed`欄と計画ファイル`## 変更内容`を照合し、`git diff <計画着手前SHA>..<commit_sha>`の
   実差分で1対1確認する。完了報告の受領時点で作業ツリーはコミット済みでcleanなため、
   作業ツリー差分ではなくコミット範囲差分を照合対象とする

3. `pending_confirmations`欄が非空の場合、各項目について次の観点で点検し、
   結果をユーザー可視の応答へ1件1行で報告してから次工程へ進む
   - 内部実装に閉じる乖離か、公開インターフェースに波及する乖離か
   - 計画本文の趣旨と整合するか
   - 内部実装に閉じる場合は`AskUserQuestion`を省略し進捗ログ転記のみを実施する
   - 公開インターフェース波及時は続く手順4で`AskUserQuestion`を発行する
4. 手順3で公開インターフェース波及と判定した項目がある場合、当該項目を`AskUserQuestion`でユーザーへ提示する。
   回答に応じた追修正が必要な場合は縮減した起動プロンプトで新規`agent-toolkit:plan-impl-executor`を再起動する
5. `review_handoff`欄が「実施完了」形式の場合、`plan-impl-executor`側でレビューと指摘反映を完了済みのため、呼び出し元はレビューを起動せず、指摘反映済み状態のまま次工程へ進む。
   「レビューは実施しない」の場合も同様に次工程へ進む
   - `plan-impl-executor`側でレビュー実施完了を経ずに呼び出し元へ返却された例外的ケース（`needs_escalation`分岐等）は手順1の再委譲ルートで解消する
6. `pending_confirmations`欄・`plan_gaps`欄の内容を計画ファイル`## 進捗ログ`へ転記する。
   後続の振り返り工程（`agent-toolkit:session-review`）は当該進捗ログを既存の観察源
   「計画ファイル進捗ログ」経由で参照する（本節側から新規の受信専用欄へは送らない）

呼び出し元の完遂順序は手順0〜6全体を通じて「実装→検証→コミット→レビュー→push→CI通過確認」とし、
いずれの工程も状態表明で早期返却しない。CI通過確認の具体手順は`agent-toolkit:commit`スキル
「push後のCI通過確認」節に従い、run未登録・失敗検知時も観測を継続する
（外部プロセス完了待ちは本ファイル手順1末尾バレットの対象外）。

非同期処理の待機表明を含む完了報告の判定は、上記手順0〜6のいずれとも独立に適用し、
`agent-toolkit/rules/03-claude-code.md`「サブエージェントの活用」節に従う。

呼び出し元は本ファイルを参照し、固有差分（起動タイミング・追加の確認事項）のみを自スキル側へ記述する。

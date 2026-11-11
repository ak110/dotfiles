# AWIのレーン処理：同一主題の追加指示

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 基準資料を選択・取得する時 | `agent-toolkit:delegation/references/mandatory-rules.md` |
| 選定前、対象がGitHub上にある場合 | `review-audit-launch.md` |
| 基準資料を選択・取得する時 | `../../delegation/SKILL.md` |
| 基準資料を選択・取得する時 | `../../../share/exec-review-responses.md` |
| 基準資料を選択・取得する時 | `../../delegation/references/base-contract.md` |
| `agent-toolkit:process-wi all-repos`の統括から、リポジトリ担当として取得済み候補を受領した時 | `all-repos-orchestration.md` |
| メインが対象選定を開始する前（実行順2）と、処理対象WIを追加する前 | `selection-procedure.md` |
| 選定YAMLを生成または消費する前 | `selection-format.md` |
| 専用worktreeとレーンを作成する前（実行順3） | `run-lanes.md` |
| レーン担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md` |
| 不良またはユーザーの是正要求を受け取った時と、ユーザーが時機の指示として「即時対応」や同義の表現を使った時 | `immediate-fix.md` |
| 公開工程を開始する前（実行順7） | `finish-session.md` |
| 公開担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md` |

## 同一主題の追加指示

1. 既存のレーン担当が詳しい文脈を把握している主題については、メイン自身が本文を読み直したり追加調査や回答作成を行ったりする前に、`../../delegation/SKILL.md`「継続と新規起動」に従って該当レーン担当へ技術調査と判断を委ねる。
   メインは認可の管理、全体調整、および検収を担う。
   メイン自身が調査して文脈を持つ主題は、委譲先へ渡し直さずにメインが修正まで行ってよい。
   書込先は次のとおりとし、書込主体の交代は`../../delegation/references/mandatory-rules.md`「作業ツリーの保護」に従う。

   - レーンに割り当てていない主題: メインが別のworktreeで修正し、主作業ツリーへの統合は他のレーンと同じく`../../../share/exec-parent-integration.md`「統合の許可」の条件で行う。
     最小限の直接修正の判定と手順は`immediate-fix.md`に従う。
   - レーンの主題: レーン担当へ追加指示として返すのを基本とする。
     レーン担当の終了を確認した後は、メインがそのレーンの専用worktreeを引き取ってよい。
     引き取ったら`exec.parent.md`「入力」の`書込担当一覧`を更新し、統合は同書「統合の指示と受領」の検収条件をメイン自身がCLIを実行する場合として適用する。

2. 本スキルの実行中にユーザーから追加指示があった場合は、既存の処理条件と比較して採否を決めた差分のみを処理条件に反映する。
   比較と採否は`../../../share/exec-review-responses.md`「指摘発生時の扱い」に従う。
   範囲や論理関係の解釈は`../../user-confirmation-and-report/references/user-utterance-incorporation.md`「原文の保持と取り込み」に従う。
   公開工程に入る前に届いた直接的な実装要求も、同じ主題である限り本スキルの処理を継続するための追加条件として扱う。
   なお、公開工程の開始後に届いた指示は`immediate-fix.md`「対象外の不良と是正要求」に従って処理する。
   主題が継続しているかどうかは元の要求・現物・既存の認可から自ら判断し、ユーザー自身しか決められない目的・範囲・認可が残っている場合に限って変更前に確認する。

3. 追加の指示によって既存の要件が変わる場合は、変更前の要件に基づいて作業ツリーの外へ作成済みの成果物（キュー項目、IssueやMRの本文など）を確認し、終了・改訂・取り消しなどの対応方針を決めて追送指示に含める。
   技術的な判断や既存の認可内で完結する処置は自ら確定して報告する。
   作業ツリーの状態に関係なく編集できる外部成果物はメインが中継に合わせて編集し、作業ツリー内の差分についてはレーン担当が計画や実装を通じて追随させる（前段でメインが専用worktreeを引き取った場合はメインが追随させる）。

4. 追送指示を出す際は、`agent-toolkit:delegation`の`references/base-contract.md`にある入力の適格性ルールを適用し、採用した要件差分と外部成果物の処置内容だけを渡す。
   コミットの分割方法や検証手順、pushの判断など、レーン担当の規範で決める手段は含めず、該当する`lanes.md`「実行順」の工程へ戻る。

## 即時対応

判定と具体的な手順は`immediate-fix.md`「即時対応」に従う。
担当・基点・時機は同書「レーンの変更範囲へ加える修正の認可」で選び、統合後の独立修正と元WIの未達を区別する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| AWIのレーン処理の基準を適用する前 | `lanes.md` |

---
name: process-wi
description: >
  対象リポジトリのAWIを取得し、レーンへ分けて計画、実装、レビュー、統合および公開を完遂するときに起動する。
---

# AWIのレーン処理

選定時に確定したAWIをレーンごとに分割し、各レーンで同じ担当スレッドが一貫して計画（必要な場合）から統合までを担当する。メインエージェントは選定、ユーザー判断の中継、公開工程、およびセッション終了を担う。レーンの数にかかわらず、各レーン担当は`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`に従って、計画から独立した実行レビューの調整・統合までを進める。複数レーンが存在する場合は、メインがレーン間の書き込み競合や依存関係を調停し、主作業ツリーへの統合を1レーンずつ許可する。
AWIとUWIの共通契約は`../wi-standards/SKILL.md`に、確認の要否と手順は`agent-toolkit:user-confirmation-and-report`に従う。なお、本スキルの実行中は自律モードで動作する。
`agent-toolkit:process-wi-all-repos`からリポジトリ担当として取得済み候補を受領した場合は、`references/all-repos-orchestration.md`の受渡し契約を確認し、対象worktreeで後述の実行順を進める。

レーン処理を進めるには、キュー、対象リポジトリ、プラグイン全体の参照資料、Gitおよび`atk`を実行できるCLI、担当エージェントの起動と終了結果を受け取る手段が必要となる。これらがそろわない環境では、提示されたWIから要求事項、依存関係、確認事項を整理した下書きを作成して返す。キューの状態更新、実装、検証、統合、公開といった作業は、必要な機能がそろっているClaude CodeまたはCodexで実行する。下書きを作成する際は[文章の品質基準](references/quality-standards.md)に従う。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/single-lane-execution.md` | 判断 | `read:agent-toolkit:*/share/single-lane-execution.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/workflow-phases.md` | 判断 | `read:agent-toolkit:*/share/workflow-phases.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../commit/references/publish.md#冒頭` | 判断 | `section:agent-toolkit:*/skills/commit/references/publish.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../plan-mode/SKILL.md#「計画ファイルの作成要否」` | 判断 | `section:agent-toolkit:*/skills/plan-mode/SKILL.md#「計画ファイルの作成要否」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../delegation/references/mandatory-rules.md#「作業ツリーの保護」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/mandatory-rules.md#「作業ツリーの保護」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/review-loop-coordination.md` | 判断 | `read:agent-toolkit:*/share/review-loop-coordination.md` |
| 選定前、対象がGitHub上にある場合 | `references/review-audit-launch.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/review-audit-launch.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../delegation/SKILL.md#「継続と新規起動」` | 判断 | `section:agent-toolkit:*/skills/delegation/SKILL.md#「継続と新規起動」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../review-standards/references/reviewee.md#「指摘発生時の扱い」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/reviewee.md#「指摘発生時の扱い」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../user-confirmation-and-report/references/user-utterance.md#「原文の保持と取り込み」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md#「原文の保持と取り込み」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../delegation/references/base-contract.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/base-contract.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../bugfix/references/root-cause-analysis.md#「QとC・Dの区別」`、`agent-basics:bugfix/references/root-cause-remediation.md#「類似見直しと再発防止の完了」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/root-cause-analysis.md#「QとC・Dの区別」`、`section:agent-basics:*/skills/bugfix/references/root-cause-remediation.md#「類似見直しと再発防止の完了」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../share/exec-review.parent.md#「返却に含まれる既存不良の受領」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「返却に含まれる既存不良の受領」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| `process-wi-all-repos`のリポジトリ担当として取得済み候補を受領した時 | `references/all-repos-orchestration.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md` | |
| メインが対象選定を開始する前（実行順2）と、処理対象WIを追加する前 | `references/selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` | |
| 選定YAMLを生成または消費する前 | `references/selection-format.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format.md` | |
| 専用worktreeとレーンを作成する前（実行順3） | `references/run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` | |
| レーン担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` | |
| 不良またはユーザーの是正要求を受け取った時 | `references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` | |
| 公開工程を開始する前（実行順7） | `references/finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` | |
| 公開担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md` | 判断 | `read:agent-toolkit:*/share/session-termination.parent.md` | |

## 用語

WIの作成、計画、実行、実行レビューの責務と引き渡し手順は`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

- **メイン**: `agent-toolkit/rules/01-agent.md`に定めるメインエージェントの略称
- **選定工程、レーン工程、公開工程**: 本スキルにおける3つの主要工程。詳細な規定はそれぞれ`references/selection-procedure.md`、`references/run-lanes.md`、`references/finish-session.md`にある。公開工程の定義は`agent-toolkit:commit`の`references/publish.md`冒頭にあり、本スキルでは公開担当が受け持つ
- **処理対象WI**: メインが選定時に確定した、1回の実行で処理するAWIの集合。処理の進行中にユーザーが明示的に追加したAWIも含む
- **レーン**: 処理対象を割り当てる作業単位。それぞれ1つの専用ブランチと専用worktree（git worktree）を持つ
- **レーン担当**: 各レーンの計画、実装、レビュー指摘への修正、履歴統合、主作業ツリーへの統合を同一スレッドで担当するエージェント。担当種別にはレーン担当、レビュー修正担当、CI修正担当の3種類がある（区分は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`の操作区分。所在参照）
- **主作業ツリー**: マージ先ブランチをチェックアウトしている作業ツリー。常駐起動では、process-loopが対象リポジトリごとに準備して統括へ渡した作業ツリーを指す。統括自身の起動cwdとは区別する
- **公開担当**: 公開工程（push、CI、検証失敗時の修正、プロジェクト固有の公開後操作、延期adoptなど）を担当する委譲先エージェント。委譲範囲は`references/finish-session.md`「公開担当の起動と並行工程」に準拠する
- **即時対応**: 作業を進めるために必要な最小限の修正をその場で行い、抜本的な対応は次のセッション用のAWIへ引き継ぐ手順

## 不変条件

- 計画ファイルの作成要否は、`agent-toolkit:plan-mode`「計画ファイルの作成要否」に従って判定する。作成が必要なレーンでは実装前に起草し、処理中に追加されたAWIは確定済みの計画とは分けて追加計画として配置する。計画作成を省くレーンでは、WIに記載された要求と完成条件を実装およびレビューの判断基準とする
- 計画自体の事前レビュー工程は設けず、実装完了後に各計画について最終実装HEADの状態で、要件および外部仕様の水準に沿った実行レビューを行う
- 専用worktreeへの書き込み権限はレーン担当が保持し（メインが「同一主題の追加指示」に従って引き取った場合を除く）、主作業ツリーへの書き込みは統合のタイミングに限定する。担当範囲の一部をサブエージェントへ委譲する場合は、`agent-toolkit:delegation`の`references/mandatory-rules.md`「作業ツリーの保護」に従い、同一の専用worktree内での分担や、別ブランチ・別worktreeの内部グループを適切に使い分ける。計画の策定、検収、コミット対応の記録、統合、メインへの結果返却はレーン担当自身が責任を持つ
- ユーザーに由来する要求の全部または一部を不採用とする場合は、事前にユーザーへの確認またはUWIの回答を得てから、該当項目の採否を確定する
- 選定時に確定した処理対象WIを一貫して最後まで処理し、ready一覧を途中で再取得しない。例外的に追加できるのは、ユーザーが明示的に追加した項目や、処理中に届いた回答済みUWIとその元項目に限る。その場合の検収と割り当ては`references/selection-procedure.md`「処理対象WIの追加」および`references/run-lanes.md`「処理中に確定した必須是正レーン」に従い、元の選定結果へ追記した上でレーンへ渡す
- 対象リポジトリへpushできるのは、公開工程を担当する公開担当だけとする。前回の公開完了後に同一セッション内で新たな成果がベースブランチへ統合された場合は、追加差分を入力として公開担当を再起動し、セッション終了前に最新HEADのpushとCIの通過を検収する（`references/finish-session.md`「公開状態の検収と再起動」）
- 計画ファイルとレビュー指摘管理表は、それらを参照するすべての工程が完了した後にのみ片付ける。各レーンの専用ブランチおよび専用worktreeは統合完了後も維持し、全工程と外部プロセスが終了した段階で、所有登録とGit状態を再確認した上で回収する
- メインの作業ディレクトリが実行環境の隔離worktree（Claude Codeの`EnterWorktree`など）の内部にある場合は、隔離を解除してから次の処理へ進む。これは、専用worktreeの作成や回収によって対象リポジトリのGit共通ディレクトリが変更されるためである。なお、`atk wi process-loop`が準備したworktreeは主作業ツリーそのものであるため、解除の対象外とする

## 実行順

1. `## 直前の同期結果の検分`および`references/selection-procedure.md`「処理対象の決定」の開始時CI点検に従って、このセッションでAWIの処理を最後まで完遂できるか判定する。手動起動と常駐の統括からの受領のいずれでも、実装に入る前に公開対象とベースブランチのCI状態を確認する。
2. `## 自動コードレビュー監査`に従って監査の要否を判定し、必要な監査を起動する。その終了を待たずに`references/selection-procedure.md`へ進み、対象ディレクトリ（cwd）のメインエージェント自身が対象を選定し、対象集合を確定した直後に処理を開始する。統括から受領したリポジトリ担当は初回候補を利用する。意味判断と取得済み候補を`pick-wi-check`へ渡し、定型項目の生成・確認・保存を1回で済ませる。選定作業を別の担当者へ委譲しない。監査結果が一部候補の判断に必要な場合は、その判断だけを待機し、独立した候補の選定と開始可能なレーンの起動を進める。
3. `references/run-lanes.md`に従い、選定結果の「段階」と「先行レーン」の情報に基づいて`atk lane create`に対象を渡し、準備されたリソースを受け取ってレーン担当を起動する。後続のレーンは、先行レーンの統合と書き込み主体の解放を確認した後に、現行HEADを起点として作成する。Claude Codeでは、各段階の最初の起動がすべて完了した後に、共通の会話圧縮予約手順へ進む。
4. 各レーンは単一担当の実行契約のもとで、計画、実装、独立した実行レビューの調整、および指摘への対応を進める。メインエージェントはユーザー判断の通知を中継し、レーン間の書き込み競合や依存関係を調停する。
5. 初期選定で複数レーンが存在する場合は、`統合準備完了`を返したレーンに対して、`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の許可」に従い1レーンずつ`統合許可`を出す。各レーンは`atk lane integrate`を実行して、統合から計画の最終確定、AWIの終了、選定結果への統合済み記録までを完了させる。メインは返却された`統合完了`を、同資料「統合の指示と受領」の受領条件に沿って検収する。
6. 処理中に追加された処理対象WIも`references/run-lanes.md`に従って既存レーンへ割り当て、同じレーン担当スレッド内で修正を収束させて統合する。
7. すべてのレーンが終了し、起動した監査の処置も確定した段階で、`references/finish-session.md`に従って公開とセッション終了を完遂する。

## 直前の同期結果の検分

リポジトリ間の同期が失敗した状態のまま着手すると、AWIの処理が途中で成立しなくなる恐れがある。そのため選定を始める前に、`atk config show`で表示される`state_dir`直下の`sync-report.json`を確認する。

- ファイルが存在しないか、`status`が`succeeded`であれば、そのまま次の工程へ進む
- `status`が`failed`、あるいは`post_apply`の`failed_steps`が1件以上ある場合は、その失敗内容を踏まえてこのセッションで処理を最後まで進められるか判断する。問題なく処理できると判断した場合は、その根拠を報告して作業を進める。処理の完遂が難しい場合は作業に着手せず、`atk wi process-loop abort`でprocess-loopへ中断を要請し、失敗したステップと理由を報告してセッションを終了する

## 自動コードレビュー監査

対象リポジトリがGitHub上にある場合は、選定を始める前に読込表の監査契約に従って未判定（pending）の有無を確認し、必要な監査を起動する。
監査の終了と追加WI投入の完了は、独立した既存候補の選定・レーン起動と並行して待つ。
同じprocess-wiの実行へ追加するWIは、保存結果の正式ファイル名を受領した直後に`references/selection-procedure.md`「処理対象WIの追加」へ渡す。
監査を省略する条件、候補0件時の受領、公開前に確定する処置は監査契約に従う。

## 同一主題の追加指示

既存のレーン担当が詳しい文脈を把握している主題については、メイン自身が本文を読み直したり追加調査や回答作成を行ったりする前に、`agent-toolkit:delegation`「継続と新規起動」に従って該当レーン担当へ技術調査と判断を委ねる。メインは認可の管理、全体調整、および検収を担う。メイン自身が調査して文脈を持つ主題は、委譲先へ渡し直さずにメインが修正まで行ってよい。書込先は次のとおりとし、書込主体の交代は`../delegation/references/mandatory-rules.md`「作業ツリーの保護」に従う。

- レーンに割り当てていない主題: メインが別のworktreeで修正し、主作業ツリーへの統合は他のレーンと同じく`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の許可」の直列条件で行う。現在の作業を進めるための最小限の修正は`references/immediate-fix.md`「即時対応の手順」に従う
- レーンの主題: レーン担当へ追加指示として返すのを基本とする。レーン担当の終了を確認した後は、メインがそのレーンの専用worktreeを引き取ってよい。引き取ったら`exec.parent.md`「入力」の`書込担当一覧`を更新し、統合は同書「統合の指示と受領」の検収条件をメイン自身がCLIを実行する場合として適用する

本スキルの実行中にユーザーから追加指示があった場合は、既存の処理条件と比較して採否を決めた差分のみを処理条件に反映する。比較と採否は`agent-toolkit:review-standards`の`references/reviewee.md`「指摘発生時の扱い」に従う。範囲や論理関係の解釈は`agent-toolkit:user-confirmation-and-report`の`references/user-utterance.md`「原文の保持と取り込み」に従う。公開工程に入る前に届いた直接的な実装要求も、同じ主題である限り本スキルの処理を継続するための追加条件として扱う。なお、公開工程の開始後に届いた指示は`references/immediate-fix.md`「対象外の不良と是正要求」に従って処理する。主題が継続しているかどうかは元の要求・現物・既存の認可から自ら判断し、ユーザー自身しか決められない目的・範囲・認可が残っている場合に限って変更前に確認する。

追加の指示によって既存の要件が変わる場合は、変更前の要件に基づいて作業ツリーの外へ作成済みの成果物（キュー項目、IssueやMRの本文など）を確認し、終了・改訂・取り消しなどの対応方針を決めて追送指示に含める。技術的な判断や既存の認可内で完結する処置は自ら確定して報告する。作業ツリーの状態に関係なく編集できる外部成果物はメインが中継に合わせて編集し、作業ツリー内の差分についてはレーン担当が計画や実装を通じて追随させる（前段でメインが専用worktreeを引き取った場合はメインが追随させる）。

追送指示を出す際は、`agent-toolkit:delegation`の`references/base-contract.md`にある入力の適格性ルールを適用し、採用した要件差分と外部成果物の処置内容だけを渡す。コミットの分割方法や検証手順、pushの判断など、レーン担当の規範で自律的に決めるべき手段は指示に含めない。

## 即時対応

即時対応では、現在または次のセッションの進行に不可欠な最小限の修正をその場で施し、テストの追加、独立した文脈でのレビュー、根本原因の分析、類似箇所の見直し、再発防止策の策定といった正式な対応は次のセッション用AWIとして登録する。暫定修正であっても安全に利用できることを検証で確かめ、未検証のまま成果として扱わない。なお、登録した正式対応用のAWIは今回の選定対象には追加しない。
選定済み作業に必要な調査と正式な処置は同じ処理内で完遂する。即時対応は未実施の正式分析、類似見直しと恒久対策を次回AWIへ渡す例外である。
即時対応の要否は、残る工程や次のセッションで処理の停止・遮断、誤った判断や成果物の作成、手戻りが発生するかどうかで判断する。進行を妨げない独立した改善事項は通常のAWIへ引き継ぐ。ユーザーから改善の即時実施などタイミングの指定があった場合は、その指示を優先する。
今回の作業自体で混入させた不具合や、初期選定のWIが本来求めている正式対応は、元の要求と完成条件に沿って同一セッション内で完了させる。即時対応の枠組みを口実にして、初期選定された作業範囲を縮小しない。
実行レビューから返された差分範囲外の既存不良には本節の基準を適用せず、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`「返却に含まれる既存不良の受領」に沿って対応する（ただし残る工程を停止・遮断する不良は、同節の例外として本節で扱う）。

具体的な手順、レーンの変更範囲に修正を加える際の認可、および対象外とした不良や是正要求の扱いについては、`references/immediate-fix.md`の定めに従う。

## 終了

工程の区切りそのものを確認の理由にしない。確認要否の判定に基づいて、ユーザーの判断が真に必要な事項だけを確認待ちとし、その判断を待たずに進められる工程はそのまま継続する。回答がすぐに得られない確認事項は、`agent-toolkit:user-confirmation-and-report`の手順に従ってUWIへ退避させ、`processing`状態の元項目を`atk wi hold --state=processing <元項目のファイル名>`で保留にする。
作業完了の報告は`agent-toolkit:completion-report`に従って行い、本スキルの工程で作成したコミットを公開した上で報告する。

### 公開工程の承認スコープ

環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`が`1`として起動された場合は自動常駐起動（`atk wi process-loop`が子セッション向けに設定）とみなし、それ以外を手動起動とする。この判別は選定工程で1回だけ行う。

手動起動の場合、確定したAWIの完成条件に明記されているrelease、tag、push、PRまたはMRの作成、配布といった操作について、記載された操作内容、対象、影響範囲に限り承認スコープとして保持する。一方、自動常駐起動ではこれらを保持しない。すでに承認されている範囲の操作はそのまま実行し、新たに具体化したことで範囲が変わる部分だけをユーザーへ確認する。なお、破壊的な操作をする前の事前説明は維持する。

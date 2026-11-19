# 設計記録の索引

機構の目的、構造の理由、知識境界と却下した代替案を、主題別の本文から選んで読むための索引である。
ユーザーが確定した方針・意向は[concepts.md](concepts.md)、障害の経緯は[incidents.md](incidents.md)が扱う。
実行時に適用する規範は現行のルールファイルとスキルが定め、設計記録はその背景と構造の理由を保持する。
既存の主題の記録は対応する本文の節へ反映し、反映後もその節が1つの主題に答え、置き換えた記述が現行の記述と区別される構成に保つ。
独立した新しい主題は主題別の本文へ置き、本索引の対応表へ行を追加する。
各節の見出しと機構の詳細は主題別の本文で読み、本索引には節の一覧や本文を複製しない。

| 主題 | 対象と役割 | 記録本文 |
| --- | --- | --- |
| 品質基準と作業運用の分離 | agent-basicsの品質スキルとagent-toolkitの運用、独立導入とホスト別依存、互換参照を扱う | [design-agent-basics.md](design-agent-basics.md) |
| WIキューと要求本文 | WIは依頼・確認を記録する項目。AWIはエージェントが処理する作業項目で、キューは項目の状態と順序を管理する。キューの状態・一覧・ユーザーコメント、AWI本文の起草の分担、一括取り込みと原文保持、原因分析節の保存前の確認を扱う | [WIキューと実行準備](design-wi-queue.md)、[キュー状態と一覧出力](design-wi-listing.md)、[WIの共通処理と一覧の限定条件](design-wi-common.md)、[ユーザーコメントと選択肢](design-wi-user-inputs.md)、[起草の責務分担と直接起草の選択](design-wi-drafting-responsibilities.md)、[既存AWI更新と調査の受領検収](design-wi-drafting-reception.md)、[一括取り込みと原文保持](design-wi-import.md)、[原因分析節と保存前の確認](design-wi-cause-validation.md) |
| 継続処理の起動・隔離・制御 | process-loopは作業項目の処理を繰り返し起動する機構。worktreeは担当ごとのGit作業ツリー。オーケストレーターの選択、worktree隔離、会話IDによる識別、中断要求と追加指示、Codexセッションの終了を扱う | [オーケストレーター選択](design-process-loop-orchestrator.md)、[process-loopのモジュール構成](design-process-loop-modules.md)、[開始前の活動猶予とUWI回答待ち](design-process-loop-start-gate.md)、[登録全体の統括と対象別所有](design-process-loop-ownership.md)、[process-loopのworktree隔離](design-process-loop-worktrees.md)、[会話IDによる識別と中断要求](design-process-loop-control.md)、[Codexセッションの終了](design-process-loop-termination.md)、[次の起動への追加指示](design-process-loop-instructions.md) |
| 委譲サーバーの公開ツールと構造 | agents_serverは別のエージェントへ作業を委譲するサーバー。MCPはツールの呼び出し口、backendは委譲先を動かす実装。backend構成、公開ツールの契約、結果の保留と自動再開、起動時の指示、Antigravity CLI backend、起動ツールの統合を扱う | [backend構成と起動](design-agents-server-backend.md)、[MCPの診断ログ](design-agents-server-diagnostics.md)、[プロトコルと起動環境](design-agents-server-environment.md)、[起動候補と観測identity](design-agents-server-connections.md)、[Claudeのキャッシュと起動設定](design-agents-server-claude-options.md)、[Claude委譲先のプラグイン再読込](design-agents-server-plugin-reload.md)、[Codex委譲先の週次利用枠](design-agents-server-codex-reset.md)、[公開ツールの起動契約](design-agents-server-start.md)、[識別子と親復帰時の子孫復旧](design-agents-server-identity-recovery.md)、[継続要求の配送](design-agents-server-send-message.md)、[中断と資源解放](design-agents-server-stop.md)、[レーン回収と資源解放](design-agents-server-lane-cleanup.md)、[待機上限](design-agents-server-wait-timeout.md)、[軽量起動と継続不能](design-agents-server-lightweight.md)、[結果保留の状態と寿命](design-agents-server-hold-state.md)、[背景完了と自動再開](design-agents-server-background-resume.md)、[再開状態の棚卸し](design-agents-server-resume-simplification.md)、[所有者不在の復旧と子thread](design-agents-server-ownership.md)、[結果受領時の案内](design-agents-server-result-guidance.md)、[委譲先への指示と規範配送](design-agents-server-instructions.md)、[文章化と委譲手段の選択](design-agents-server-delegation.md)、[人向けの一覧表示](design-agents-server-list.md)、[Antigravity CLI backend](design-agents-server-antigravity.md)、[OpenCode CLI backend](design-agents-server-opencode.md)、[記録の表示と起動ツールの統合](design-agents-server-logs-launch-tools.md)、[実装配置と共有状態の非同期境界](design-agents-server-modules.md) |
| 委譲管理の共有と世代移譲 | stdio転送と共有サービス、会話context、受付・状態writer・raw資源の所有、初回移行の制約を扱う | [design-agents-server-service.md](design-agents-server-service.md) |
| 委譲先の稼働状態・起動診断・通知 | agents_serverで起動した会話の状態を把握する機構。statuslineはその状態を表示する欄。状態ファイルと終了時の結果の回収、statuslineの表示、API失敗と利用上限の待機、起動失敗の観測、上り通知を扱う | [状態ファイルの構造と寿命](design-agents-runtime-state.md)、[補助保存と表示の寿命](design-agents-runtime-persistence.md)、[終了時の結果の回収と待機ロック](design-agents-runtime-results.md)、[statuslineの表示](design-agents-runtime-statusline.md)、[接続選択とAPI残高不足](design-agents-runtime-connections.md)、[API失敗と利用上限の待機](design-agents-runtime-usage-wait.md)、[引き継ぎ起動とCodexの再開](design-agents-runtime-handoff-resume.md)、[起動失敗の観測と無人起動](design-agents-runtime-preflight.md)、[agents_serverの上り通知](design-agents-runtime-notifications.md) |
| 現行の目的・要求と残工程 | 追加指示を現行の作業契約へ統合し、工程境界の再読と委譲先の記録を接続する | [design-work-contract.md](design-work-contract.md) |
| 委譲の継続・待機・成果受渡し | 別のエージェントへ渡した作業を継続し、終了を待ち、結果を受け取る手順。大出力の分離実行、多段工程の引き継ぎ記録、工程別モデル設定を含む | [design-delegation.md](design-delegation.md) |
| 担当の責務と工程間の知識境界 | 作業の選定・計画・実装・レビューの担当間で、入力と結果を渡す境界。レーンは並行して作業する担当単位。委譲の共通契約、AWI処理の工程と選定、工程別モデル設定、待機と停滞の観測、セッションの終了、工程名の体系を扱う | [委譲の共通契約](design-delegation-contract.md)、[セルフチェックと担当間の知識境界](design-delegation-responsibilities.md)、[委譲プロンプトと役割文書](design-delegation-inputs.md)、[プロセス所有と即時通知](design-delegation-process-notification.md)、[AWI処理の工程と選定](design-selection-workflow.md)、[選定の書込範囲と入力解決](design-selection-paths.md)、[上流投入とレーン工程](design-selection-upstream-lanes.md)、[レーンの継続と要求の由来](design-selection-origin.md)、[レーン配分の上限と単独稼働](design-lane-allocation.md)、[処理中の処理対象WIの追加](design-wi-addition.md)、[反映後の観測と再開](design-post-release-observation.md)、[処理中の不良への即時対応](design-workflow-remediation.md)、[工程別モデル設定と待機](design-workflow-models-waiting.md)、[レーン資源の統合操作](design-lane-integration.md)、[セッションの終了](design-workflow-termination.md) |
| ホスト間の規範配置と適用境界 | Claude Code・Codexなどの実行ホストへ、エージェントの指示を配置して適用する仕組み。pluginの生成物と導入、認証情報の扱い、ユーザーとの認識合わせ、規範の集約先と参照方向を扱う | [常時規範とplugin manifest](design-hosts-rules-manifests.md)、[Codexの起動管理と更新job](design-hosts-codex-runtime.md)、[plugin導入と実体の回収](design-hosts-plugin-lifecycle.md)、[ルールの配置と配送](design-hosts-rules-delivery.md)、[認証情報とユーザーとの認識合わせ](design-hosts-authorization.md)、[規範の集約先と参照方向](design-hosts-document-ownership.md) |
| 振り返り・知見集約 | review-sessionは作業終了時の振り返り。候補の抽出、完了報告との関係、採用済み対策の効果確認、AWIなどから得た知見の集約を扱う | [振り返りの起動と失敗回復](design-session-review-startup.md)、[通読資料と費用の集計](design-session-review-materials.md)、[振り返り準備と会話の流れ](design-session-review-preparation.md)、[人間入力と候補抽出](design-session-review-candidates.md)、[正常な否定結果と失敗署名](design-session-review-failures.md)、[記録の解決と集合照会](design-session-review-records.md)、[必須候補の評価と分析](design-session-review-analysis.md)、[比較材料と未確認範囲](design-session-review-comparison.md)、[証拠抽出の由来分離と記録ID](design-session-review-provenance.md)、[証拠抽出スクリプトの構成](design-session-review-extraction.md)、[定義本文の取得と起動準備](design-session-review-definition-reads.md)、[手動の週次費用指標](design-cost-metrics.md)、[完了報告と振り返り](design-session-review-completion.md)、[採用済み対策の効果確認と知見集約](design-session-review-effects.md) |
| 原因分析・レビュー監査 | 廃止した原因分析の表構成の経緯と、自動コードレビュー監査の判定済み記録を扱う | [design-analysis-audit.md](design-analysis-audit.md) |
| 計画と実行レビューへの受渡し | 作業項目から計画を作成し、実装結果が要件に沿うか確認する実行レビューへ渡す工程。review_contractとレビュー指摘管理表、レビュー修正の履歴統合、複数レーンの統合、計画ファイルの構成、WI実装commitの対応を扱う | [計画と実行レビューの分担](design-planning-review.md)、[レビュー契約と指摘管理表](design-planning-review-contract.md)、[付帯作業の所有、WI実装commitの対応とレビュー修正の履歴統合](design-planning-commit-mapping.md)、[複数レーンの統合と版数更新](design-planning-integration.md)、[計画の作成要否と成果物基準](design-planning-criteria.md)、[レーンの計画確認](design-planning-lane-check.md)、[計画ファイルの領域構成と保存](design-planning-storage.md) |
| 検収根拠とレビュー担当間の知識境界 | 完成条件を満たす証拠を保存し、実行レビューと画面の使いやすさを確認するレビューへ渡す仕組み。レビュー担当とレビューイーの知識境界、完成条件証拠と証拠検査を扱う | [直接消費側探索とレビューの知識境界](design-review-boundaries.md)、[レビューイーの採否判断](design-reviewee-decisions.md)、[ユーザビリティと完成条件の判定](design-review-acceptance.md) |
| フックの責務・通知・セッション状態 | hookはエージェントの操作前後などに自動で起動する処理。責務境界、遮断・警告の存廃判定、Stopフック、通知の整形、複数ホスト向けの配布、イベントごとの個別設計、進捗のセルフチェック通知、セッション状態の共有を扱う | [フックの責務境界と判定の集約](design-hooks-responsibility.md)、[Bashコマンドの解析とheredoc遮断](design-hooks-shell-parsing.md)、[遮断・警告フックの存廃判定](design-hooks-eligibility.md)、[Stopの共通ハンドラー](design-hooks-stop-handler.md)、[Stopの通知と入力待ち判定](design-hooks-stop-notices.md)、[終了工程の証拠](design-hooks-termination-evidence.md)、[通知の整形と画面への写し](design-hooks-notices.md)、[複数ホストの入力変換と出力契約](design-hooks-host-adapters.md)、[ユーザー発話への注入と注記](design-hooks-user-prompt.md)、[進捗のセルフチェック通知](design-hooks-progress.md)、[commitと委譲手段の選択の支援](design-hooks-pretooluse.md)、[読込宣言の解析と資料解決](design-hooks-read-declarations.md)、[本文の取得観測と確認前の拒否](design-hooks-read-acquisition.md)、[コマンド時間と退避出力の観測](design-hooks-posttooluse.md)、[会話圧縮と利用上限後の再開予約](design-hooks-compaction-resume.md)、[定期再確認の装着と回収](design-hooks-periodic-recheck.md)、[ユーザーへの本文送達](design-hooks-send-to-user.md)、[フックごとのホスト対応範囲](design-hooks-host-support.md)、[セッション状態と記録解決](design-hooks-session-state.md)、[通知仕様の追随と読込宣言の回復](design-hooks-declaration-recovery.md) |
| 一時成果物とキューリポジトリの回復 | managed-tempは一時成果物の管理領域。MQはWIキューの管理層で、項目を保存するGitリポジトリの分岐回復を扱う | [design-storage.md](design-storage.md)、[保持実体の共有とレーン資源の回収](design-storage-shared-resources.md) |
| Windowsの昇格と状態の権限 | 同じユーザーの昇格したプロセスと非昇格のプロセスが共有する状態ディレクトリの作成と旧版のACLの修復、昇格した`update-dotfiles`での管理サービスの扱い、sshから非昇格で実行する`ssh-unelevated`を扱う | [design-windows-permissions.md](design-windows-permissions.md) |
| branch・CI所有権・公開・終了 | developは開発用、masterはリリース用のGit branch。CIは変更の自動検証。CIの実処理所有権、rulesetとマージ後の同期、公開と検証、公開状態の観測を含む公開工程を扱う | [developとmasterのbranch・リリース設計](design-release-branches.md)、[CIの実処理所有権](design-release-ci-ownership.md)、[rulesetとマージ後の同期](design-release-merge.md)、[公開工程と並行工程](design-release-workflow.md)、[公開工程の検証](design-release-verification.md)、[公開状態の観測](design-release-observation.md)、[公開の認可と短絡](design-release-authorization.md) |
| CLIの入力・結果・公開境界 | atkはagent-toolkitのコマンド。CLIはコマンドラインからの呼び出し口。引数の拒否、結果行と出力の内容、次の操作、長い出力の保存、`atk run-skill`を扱う | [公開出力とworktreeの選択](design-cli-launch.md)、[有限終了コマンドとWI投入の監督](design-cli-execution.md)、[サブコマンド登録と引数拒否](design-cli-arguments.md)、[結果行と出力に含める情報](design-cli-output.md)、[失敗と警告の次の操作](design-cli-next-action.md)、[長い出力の自動保存](design-cli-auto-save.md)、[commitと計画検証の公開境界](design-cli-public-boundaries.md)、[定期実行からのスキル起動](design-cli-run-skill.md) |
| 画面統合とセッション・計画一覧 | atk serveは作業状況や計画をブラウザーで確認する画面を提供するコマンド。画面統合、計画ファイル画面、SSEと接続維持、サーバーの停止、WI画面、セッション画面を扱う | [画面統合](design-serve-shell.md)、[計画ファイル画面](design-serve-plans.md)、[SSEとサーバー停止](design-serve-connections.md)、[WI画面](design-serve-wi.md)、[WI画面の応答性と回答](design-serve-wi-response.md)、[セッション一覧と識別](design-serve-session-list.md)、[セッションの親子判定と見出し](design-serve-session-identity.md)、[セッションの推定費用](design-serve-session-cost.md)、[セッションの自動更新と表示分類](design-serve-session-updates.md) |
| パッケージと検証環境の配置 | agent-toolkitのPythonパッケージとテスト環境の配置。pyfltrは整形・静的解析・テストをまとめて実行するツール | [パッケージ構成と起動層](design-packages-layout.md)、[pyfltrのチェック設定](design-packages-check-settings.md)、[テストの実行環境の隔離](design-packages-test-isolation.md)、[確かめる対象の近くへのテスト配置](design-packages-test-placement.md)、[効率化の仕組みの棚卸し（2026年11月）](design-packages-inventory-202611.md) |

## 効率化の仕組みの棚卸し

2026年11月10日に、効率化のために加えた仕組みの便益、不具合と回避手段を見直した。
各主題の本文末尾に、目的と導入・拡張時期、不具合の件数と所要、実測した効果、既存の回避手段、存廃の判定を記録する。
57行の導入・拡張の由来を確認し、方式の要求・選択と、結果の要求への具体化を分けて各行に記録した。
委譲サーバー、CLI、フック、AWI処理の工程の4主題は、主題別の本文に無い由来、実測、判定と今も判断に使う条件だけを、仕組みごとに対応する主題別の本文の該当箇所か末尾へ移した。
この4主題の導入時期は、主題別の本文とGitの履歴から確かめる。
初期要求や個別の拡張を特定できなかった範囲は未確定として残す。
ユーザーが要求・選択した手段は、その由来を保ち、同条件の測定で決めるか維持とする。
現行の方式と既に撤去した方式を分け、既存の機能・データ・資源の保護は保つ。

対象は本索引の設計記録、2026年9月以後の効率化に関わる変更履歴、全状態の作業項目から抽出した候補である。
履歴の件名と観測記録を起点にした探索であり、過去の全不具合の件数を確定した統計ではない。
同じ事象の重複、機構群に属する報告と個別機能への帰属、停止時間と修正の作業時間を分ける。
独立した件数や修正の所要を確定できない行は不明と記す。
過去記録と他担当の報告による値は、今回の再計測と区別する。
作業項目の原本はリポジトリ外にあり、引継ぎが必要な変更だけ保存済みの項目名を記す。

同じ入力・並行条件の全体所要が不明な値は、効果が無いことの証明には使わない。
各主題の本文で実測を記さない仕組みは、同じ代表入力・並行条件で仕組みの有無を比べた全体所要が未測定である。
判定の「単純化」には、以前に独自の方式を撤去して標準の手段へまとめた変更の維持も含む。
ユーザーが方式を指定していない現行の補助機構で、実測便益を示せず不具合の報告がある場合は、単純化または撤去の実施結果・独立した引継ぎ先へ対応付ける。
ユーザーが要求・選択した方式は維持するか、同条件の比較測定で変更を決める。
限定区間の対照、費用モデル、異なる入力の前後値は、全体所要の対照から分ける。
安全性や入力検証のために必要な機能は、時間短縮の補助と区別して維持する。
レーンの並行数とpytestのworker数は今回変更しない。

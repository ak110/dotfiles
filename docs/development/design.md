# 設計記録の索引

機構の目的、構造の理由、知識境界と却下した代替案を、主題別の本文から選んで読むための索引である。
利用者が確定した方針・意向は[concepts.md](concepts.md)、障害の経緯は[incidents.md](incidents.md)が扱う。
実行時に適用する規範は現行のルールファイルとスキルが定め、設計記録はその背景と構造の理由を保持する。
既存の主題の記録は対応する本文の節へ追記する。独立した新しい主題は主題別の本文へ置き、本索引の対応表と各節へのリンク付き見出しを追加する。
機構の詳細は主題別の本文に置き、本索引には本文を複製しない。

| 主題 | 対象と役割 | 記録本文 |
| --- | --- | --- |
| WIキューと要求本文 | WIは依頼・確認を記録する項目。AWIはエージェントが処理する作業項目で、キューは項目の状態と順序を管理する | [design-wi.md](design-wi.md) |
| 継続処理の起動・隔離・制御 | process-loopは作業項目の処理を繰り返し起動する機構。レーンは並行して作業する担当単位、worktreeは担当ごとのGit作業ツリー | [design-process-loop.md](design-process-loop.md) |
| 委譲サーバーの公開ツールと構造 | agents_serverは別のエージェントへ作業を委譲するサーバー。MCPはツールの呼び出し口、backendは委譲先を動かす実装 | [design-agents-server.md](design-agents-server.md) |
| 委譲先の稼働状態・起動診断・通知 | agents_serverで起動した会話の状態を把握する機構。statuslineはその状態を表示する欄 | [design-agents-runtime.md](design-agents-runtime.md) |
| 委譲の継続・待機・成果受渡し | 別のエージェントへ渡した作業を継続し、終了を待ち、結果を受け取る手順 | [design-delegation.md](design-delegation.md) |
| 担当の責務と工程間の知識境界 | 作業の選定・計画・実装・レビューの担当間で、入力と結果を渡す境界 | [design-workflow-boundaries.md](design-workflow-boundaries.md) |
| ホスト間の規範配置と適用境界 | Claude Code・Codexなどの実行ホストへ、エージェントの指示を配置して適用する仕組み | [design-hosts.md](design-hosts.md) |
| 振り返り・知見集約・原因分析・監査 | session-reviewは作業終了時の振り返り。AWIなどから得た知見や欠陥の原因を次の作業へ残す工程 | [design-session-review.md](design-session-review.md) |
| 計画と実行レビューへの受渡し | 作業項目から計画を作成し、実装結果が要件に沿うか確認する実行レビューへ渡す工程 | [design-planning.md](design-planning.md) |
| 検収根拠とレビュー担当間の知識境界 | 完成条件を満たす証拠を保存し、実行レビューと画面の使いやすさを確認するレビューへ渡す仕組み | [design-review-evidence.md](design-review-evidence.md) |
| フックの責務・通知・セッション状態 | hookはエージェントの操作前後などに自動で起動する処理。通知や操作の警告・遮断を担う | [design-hooks.md](design-hooks.md) |
| 一時成果物とキューリポジトリの回復 | managed-tempは一時成果物の管理領域。MQはWIキューの管理層で、項目を保存するGitリポジトリの分岐回復を扱う | [design-storage.md](design-storage.md) |
| branch・CI所有権・公開・終端 | developは開発用、masterはリリース用のGit branch。CIは変更の自動検証で、公開と作業終了に関わる | [design-release.md](design-release.md) |
| CLIの入力・結果・公開境界 | atkはagent-toolkitのコマンド。CLIはコマンドラインからの呼び出し口で、引数と結果の契約を扱う | [design-cli.md](design-cli.md) |
| 画面統合と計画一覧の対象判定 | atk serveは作業状況や計画をブラウザーで確認する画面を提供するコマンド | [design-serve.md](design-serve.md) |
| パッケージと検証環境の配置 | agent-toolkitのPythonパッケージとテスト環境の配置。pyfltrは整形・静的解析・テストをまとめて実行するツール | [design-packages.md](design-packages.md) |

## [WIキューと実行準備](design-wi.md#wiキューと実行準備)

### [キュー状態と公開一覧](design-wi.md#キュー状態と公開一覧)

### [一覧出力](design-wi.md#一覧出力)

### [ユーザーコメントの由来と編集境界](design-wi.md#ユーザーコメントの由来と編集境界)

## [process-loopのオーケストレーター選択](design-process-loop.md#process-loopのオーケストレーター選択)

## [agents_server MCPによる委譲の仕組み](design-agents-server.md#agents_server-mcpによる委譲の仕組み)

## [Antigravity CLI backendの用途限定の追加](design-agents-server.md#antigravity-cli-backendの用途限定の追加)

## [レーン配分の上限と単独稼働の扱い](design-process-loop.md#レーン配分の上限と単独稼働の扱い)

## [処理中の処理対象WIの追加と反映後の観測](design-process-loop.md#処理中の処理対象wiの追加と反映後の観測)

## [公開出力から導出できる項目の撤去](design-cli.md#公開出力から導出できる項目の撤去)

## [dotfilesのatkで実行するworktree](design-cli.md#dotfilesのatkで実行するworktree)

## [agents_server sessionのstatusline表示](design-agents-runtime.md#agents_server-sessionのstatusline表示)

## [大出力コマンドの分離実行](design-delegation.md#大出力コマンドの分離実行)

## [委譲継続の設計意図](design-delegation.md#委譲継続の設計意図)

## [多段工程の委譲の引き継ぎ記録](design-delegation.md#多段工程の委譲の引き継ぎ記録)

## [委譲先セッションの結果待機](design-delegation.md#委譲先セッションの結果待機)

## [AWI本文起草の分離](design-wi.md#awi本文起草の分離)

## [直接消費側探索の証跡](design-review-evidence.md#直接消費側探索の証跡)

## [委譲先の起動失敗を待機側が観測できる状態](design-agents-runtime.md#委譲先の起動失敗を待機側が観測できる状態)

## [委譲先の作業ディレクトリでのプラグイン起動の事前確認](design-agents-runtime.md#委譲先の作業ディレクトリでのプラグイン起動の事前確認)

## [agents_serverの起動ツールの統合](design-agents-server.md#agents_serverの起動ツールの統合)

## [無人セッションと人間の操作を前提とする機構](design-agents-runtime.md#無人セッションと人間の操作を前提とする機構)

## [agents_serverの上り通知の仕組み](design-agents-runtime.md#agents_serverの上り通知の仕組み)

## [process-loopのworktree隔離](design-process-loop.md#process-loopのworktree隔離)

## [process-loopの会話IDによる識別](design-process-loop.md#process-loopの会話idによる識別)

## [process-loopの中断要求](design-process-loop.md#process-loopの中断要求)

## [process-loopへの1セッション限定の追加指示](design-process-loop.md#process-loopへの1セッション限定の追加指示)

## [一括取り込みと原文保持](design-wi.md#一括取り込みと原文保持)

### [WI本文の改行契約](design-wi.md#wi本文の改行契約)

## [委譲と知識境界](design-workflow-boundaries.md#委譲と知識境界)

## [Claude CodeとCodexの規範配置](design-hosts.md#claude-codeとcodexの規範配置)

## [session-reviewと完了報告](design-session-review.md#session-reviewと完了報告)

## [工程名の体系](design-workflow-boundaries.md#工程名の体系)

## [計画と実行レビュー](design-planning.md#計画と実行レビュー)

## [計画の作成要否と成果物基準の一本化](design-planning.md#計画の作成要否と成果物基準の一本化)

## [レーンの計画確認](design-planning.md#レーンの計画確認)

## [レビュー担当とレビューイーの知識境界](design-review-evidence.md#レビュー担当とレビューイーの知識境界)

## [フックの責務境界](design-hooks.md#フックの責務境界)

### [warn・block判定の全件確認（2026年9月26日）](design-hooks.md#warnblock判定の全件確認2026年9月26日)

### [事前に防ぐ型と遮断後に対処する型（2026年9月29日）](design-hooks.md#事前に防ぐ型と遮断後に対処する型2026年9月29日)

### [委譲手段の選択と待機の装着の手掛かり（2026年10月1日）](design-hooks.md#委譲手段の選択と待機の装着の手掛かり2026年10月1日)

### [hook出力契約の自動チェック](design-hooks.md#hook出力契約の自動チェック)

## [ユーザーとの認識合わせ](design-hosts.md#ユーザーとの認識合わせ)

## [セッション状態の共有](design-hooks.md#セッション状態の共有)

## [AWI由来の知見の集約](design-session-review.md#awi由来の知見の集約)

## [managed-temp](design-storage.md#managed-temp)

## [MQ管理リポジトリの分岐回復](design-storage.md#mq管理リポジトリの分岐回復)

## [developとmasterのbranch・リリース設計](design-release.md#developとmasterのbranchリリース設計)

### [CIの実処理所有権](design-release.md#ciの実処理所有権)

## [終端工程](design-release.md#終端工程)

## [計画ファイルの領域構成と重複排除](design-planning.md#計画ファイルの領域構成と重複排除)

## [バグ調査の原因分析機構](design-session-review.md#バグ調査の原因分析機構)

## [自動コードレビュー監査の判定済み記録](design-session-review.md#自動コードレビュー監査の判定済み記録)

## [atkサブコマンドの引数拒否形式](design-cli.md#atkサブコマンドの引数拒否形式)

## [atkサブコマンドの実行結果出力](design-cli.md#atkサブコマンドの実行結果出力)

## [エージェント環境での長い出力の自動保存](design-cli.md#エージェント環境での長い出力の自動保存)

## [atk commitとsetup-projectの公開境界](design-cli.md#atk-commitとsetup-projectの公開境界)

## [規範の集約先と参照方向](design-hosts.md#規範の集約先と参照方向)

## [場面別手順と外部投稿前レビュー](design-hosts.md#場面別手順と外部投稿前レビュー)

## [atk serveの画面統合](design-serve.md#atk-serveの画面統合)

### [計画一覧の対象判定](design-serve.md#計画一覧の対象判定)

## [agent-toolkit/agent_toolkit/のパッケージ構成](design-packages.md#agent-toolkitagent_toolkitのパッケージ構成)

## [pyfltrのsubproject分割とチェック設定の置き場所](design-packages.md#pyfltrのsubproject分割とチェック設定の置き場所)

## [テストの実行環境の隔離](design-packages.md#テストの実行環境の隔離)

## [確かめる対象の近くへのテスト配置](design-packages.md#確かめる対象の近くへのテスト配置)

## [通知本文の仕様とエージェント向け文書の長さの追随検出](design-hooks.md#通知本文の仕様とエージェント向け文書の長さの追随検出)

## [WIの前提鮮度](design-wi.md#wiの前提鮮度)

## [ユーザビリティレビューとWI完成条件の受渡し](design-review-evidence.md#ユーザビリティレビューとwi完成条件の受渡し)

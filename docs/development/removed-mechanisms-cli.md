# 撤去した仕組み: CLIとスクリプト

## CLIとスクリプト

| 働き | 撤去 | 理由 | 方針の所在 | 再導入の条件 |
| --- | --- | --- | --- | --- |
| 上流developの変化を検知して`update-dotfiles`を自動実行し、稼働中のセッションを理由に延期した更新も再試行する | 2026-11-08 | 別ホストで開発していた頃の名残であり、ユーザーが廃止を指示した。 | 手動の`update-dotfiles`とprocess-loopの待機中の更新を維持する。旧`dotfiles-autoupdate`の撤去は`pytools/_internal/setup_tmpfiles_clean.py`が担う。 | 撤去理由を解消できることを示した場合 |
| ヘルプの同一セッション内観測を実行と質問本文の前提として遮断する | 2026-09-09、09-16、commit: 89212c829、7f7bdbc2c | 通常用途の入力形式は手順への明記で目的を満たせた。 | 手順で確定した受理形式を使い、未確定の形式だけを確認する。docs/development/concepts-overengineering.md「過剰設計の抑制」 | 撤去理由を解消できることを示した場合 |
| 旧API設定キーの組合せで接続方式と使用順を決める | 2026-10-29、commit: 34e02b90e | 接続種別と役割別の使用順を明示する設定へ置き換える判断による。 | 認証の所在と役割別・エンジン別の使用順を分ける。docs/development/concepts-connections.md「委譲全般と利用上限」 | 撤去理由を解消できることを示した場合 |
| 引数で定期再確認の出力を選ぶ（旧atk wait-schedule） | 2026-10-25、commit: 20ddd9a9b | 独自の入力手順を各担当が学び直す費用を減らす判断による。 | 公開情報からメインのcron式と共通本文を引数なしで返す。docs/development/concepts-waiting.md「待機と観測」 | 撤去理由を解消できることを示した場合 |
| 取得主体が`--output-file`などで保存先を指定し、WI取得結果や長い出力を手動保存する | 2026-10-04、commit: 348986870 | 呼び出し側で保存先を組み立てる工程が長いコマンドと重複保存を生んだ。 | 長い出力の保存はatkが所有し、保存先と行数を返す。docs/development/concepts-workflows-lanes.md「process-wiとレーン」、docs/development/concepts-workflows.md「`atk`の出力と操作」、docs/development/design-cli-auto-save.md「エージェント環境での長い出力の自動保存」 | 撤去理由を解消できることを示した場合 |
| Codexホストの待機手段を`atk agents-wait`と定める義務 | 2026-09-08の事例、commit: dd9a18f96 | Codexでは状態ディレクトリを解決できず、待機の代替として成立しなかった。 | 実行ホストで使える公開待機と状態照会を使う。docs/development/incidents-agents-server.md「agents_serverと委譲基盤の不全」 | 撤去理由を解消できることを示した場合 |
| owner・host識別子の不足を補うため、CLIが全状態ルートを走査する | 2026-09-22の事例、commit: 130ba7ce5 | ownerとhostの未配送を全ルート走査で補い、保存先と観測先の対応が崩れていた。 | 公開応答と確認済みaliasで同じ会話へ接続する。docs/development/incidents-agents-server.md「agents_serverと委譲基盤の不全」 | 撤去理由を解消できることを示した場合 |
| user scope移行のため、管理対象pluginのproject scope登録を自動除去する | 2026-10-25の事例、commit: c406272ef | 意図的なproject scope登録まで古い残骸と扱い、共有設定を書き換えていた。 | 移行時の前提を現行の登録意図と照合する。docs/development/incidents-distribution.md「配布と更新に伴う不整合」 | 撤去理由を解消できることを示した場合 |
| 実行レビューの完成条件証拠を専用確認コマンドで検査する | 2026-10-25、commit: 20ddd9a9b | 専用入力の学習・保守費用が便益を上回り、構文検査も根拠の意味を保証しなかった。 | 条件固有の観測と所在を返却で受領し、根拠不足を差し戻す。docs/development/incidents-review-evidence.md「実行レビューと証拠検査の欠陥」、docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| 証拠抽出器のオプションが可変個数の値を取り込む | 2026-08の事例、commit: 05f10dbad | 可変個数の値が後続位置引数を取り込み、対象未指定でも成功を返していた。 | 入力の所属を一意にし、必須入力の欠落を非0で返す。docs/development/incidents-judgment.md「誤判定と検証不足」 | 撤去理由を解消できることを示した場合 |
| backendごとに環境変数から通知ルートを独立解決する | 2026-10-03、commit: b99d9fd4f | backend独自の環境解決がManagerの保存先と通知先を分けていた。 | 通知rootはManagerが確定した値を渡す。docs/development/design-agents-runtime-state.md「状態ファイルの構造と寿命」 | 撤去理由を解消できることを示した場合 |
| ランチャーを`execve`でMCPサーバー自身へ置換する起動方式 | 2026-10-20、commit: 538cf93ff | サーバー自身ではSIGKILL等による停止後の終了状態を記録できなかった。 | 既存ランチャーが子の終了と標準エラーを観測する。docs/development/design-agents-server-backend.md「backend構成と起動」 | 撤去理由を解消できることを示した場合 |
| CLI・MCPの一覧や詳細で停滞判定済みの`stalled`を返す | 2026-09-18、commit: 18b537780 | 同じ応答の活動経過秒と閾値だけで再現できる派生値だった。 | 一次情報を公開し、停滞の判断は受け手に残す。docs/development/design-cli-launch.md「公開出力から導出できる項目の撤去」 | 撤去理由を解消できることを示した場合 |
| コマンド・両出力・Git状態・終了状態を専用JSONに保存し再取得する | 2026-10-25、commit: 20ddd9a9b | 標準実行で得られる観測に専用JSONの学習と付替え工程を重ねていた。 | 有限実行の観測はハーネスの標準手段を使う。docs/development/design-cli-execution.md「有限終了コマンドの出力と終了状態の保持」、docs/development/design-storage.md「保存物の配置」 | 撤去理由を解消できることを示した場合 |
| `atk wi`の出力末尾へ未回答UWIを通知する | 2026-09-30、commit: f62cd4ddb | 確認先がserveへ移って読み手を失い、出力選別と警告の見落としを誘った。 | 出力は受信者の次の判断に使う情報へ絞る。docs/development/design-cli-output.md「atkの出力に含める情報」 | 撤去理由を解消できることを示した場合 |
| 文字位置と継続位置JSONで本文を分割取得する | 2026-10-25、commit: 20ddd9a9b | 標準読取で分割でき、継続位置JSONを扱う専用手順の学習費用が便益を上回った。 | 保存先と行数から標準手段で分割取得する。docs/development/design-cli-auto-save.md「エージェント環境での長い出力の自動保存」 | 撤去理由を解消できることを示した場合 |
| 計画の検証行を専用処理で列挙・実行・保存する | 2026-10-25、commit: 20ddd9a9b | 標準実行で同じ検証ができ、専用の生成・保存形式を保つ費用が大きかった。 | 計画の検証コマンドをハーネスで直接実行する。docs/development/design-cli-public-boundaries.md「計画検証と検索記録」、docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| Codex向けに外部marketplace登録・外部plugin導入・取得元検証を行う | 2026-08-30、commit: 327922025 | Codex側には外部marketplace導入と取得元検証の対応先が無かった。 | local pluginの導入経路を使う。docs/development/design-hosts-plugin-lifecycle.md「Codexのlocal plugin導入」 | 撤去理由を解消できることを示した場合 |
| 一時領域を公開の独自コマンドと専用モジュールで操作する | 2026-10-25、commit: 20ddd9a9b | 一時領域の独自操作を各担当が学び直す費用を除く判断による。 | セッションの一時領域を標準コマンドで扱う。docs/development/design-packages-layout.md「agent-toolkit/agent_toolkit/のパッケージ構成」、docs/development/design-storage.md「managed-temp」 | 撤去理由を解消できることを示した場合 |
| mcp依存を上書きして特定系列へ強制解決する | 2026-10-09、commit: afef233fc | mcpの上書きが2系要求まで1系へ置き換え、lock後のimportを失敗させた。 | 依存の両立判定は通常のuv解決へ戻す。docs/development/design-packages-check-settings.md「pyfltrのsubproject分割とチェック設定の置き場所」 | 撤去理由を解消できることを示した場合 |
| 専用スクリプトでレビュー条項を生成・構造確認する | 2026-10-25、commit: 20ddd9a9b | 標準の比較と対応記録で同じ判断を得られ、専用入力JSONの学習費用が大きかった。 | レビュー条項を既存の計画節と開始時点の実体から生成する。docs/development/design-planning-review-contract.md「`review_contract`とレビュー指摘管理表」、docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| 履歴書換え後のレビュー影響を専用スクリプトで比較する | 2026-10-25、commit: 20ddd9a9b | 標準Git比較と既存対応記録で同じ判断を得られた。 | 履歴書換え後の影響は標準比較で判断する。docs/development/design-planning-review-contract.md「`review_contract`とレビュー指摘管理表」、docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| `--orchestrator`・`--model`を設定と併存させて統括モデルを選ぶCLI方式 | 2026-08-19、commit: 1237f8e4d | CLI引数と設定の優先関係が複雑になった。 | 統括モデルは設定から選ぶ。docs/development/design-process-loop-orchestrator.md「process-loopのオーケストレーター選択」 | 撤去理由を解消できることを示した場合 |
| 単一リポジトリ起動・`all-repos`サブコマンド・`--target-repo`で常駐対象を選ぶ旧起動方式 | 2026-10-29、commit: e20b4cca6 | 登録全体を1つの統括から扱う起動方式へ移す判断による。 | 常駐cwdでprocess-wi all-reposを1つ起動する。docs/development/design-process-loop-ownership.md「登録全体の統括と対象別所有」 | 撤去理由を解消できることを示した場合 |
| 未判定の検証記録を専用スクリプトで生成・更新・照会する | 2026-10-25、commit: 20ddd9a9b | 標準結果と既存返却で代替でき、専用形式の学習・保守費用が便益を上回った。 | 検証結果は標準実行と既存の引き継ぎ記録へ残す。docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| 読者別探索の比較資料を専用スクリプトで準備する | 2026-10-25、commit: 20ddd9a9b | Git参照と標準比較で資料を渡せ、専用準備形式の維持費用が大きかった。 | 比較資料はrepository・base・head・pathsで渡す。docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| 再発防止観点の入力を専用スクリプトで準備する | 2026-10-25、commit: 20ddd9a9b | 標準検索の和集合で必要な観点資料を列挙できた。 | 観点見出しを持つ資料を既存検索で列挙する。docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| レーン成果の受領を専用スクリプトで照合する | 2026-10-25、commit: 20ddd9a9b | 既存返却と公開状態で受領を照合できた。 | branch・採用状態・保存計画の実在から受領を確かめる。docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| WI原稿の引用取込・検証・比較資料生成を専用処理で一括準備する | 2026-10-25、commit: 20ddd9a9b | 既存の引用確認とWI保存のdry-runで準備できた。 | 原稿・出所・成立性の証拠を独立検収へ渡す。docs/development/design-review-acceptance.md「完成条件の判定と受領」 | 撤去理由を解消できることを示した場合 |
| セッション詳細APIのイベントを件数上限で切り詰める | 2026-10-07、commit: 43a8b5217 | イベント件数制限がサーバーの全文解析量を減らさず、必要な履歴を欠落させた。 | 詳細APIは全イベントを返し、初期表示は段階描画で抑える。docs/development/design-serve-session-updates.md「自動更新と右ペイン」 | 撤去理由を解消できることを示した場合 |
| 用途指定のusageで役割と工程を対応付ける | 2026-10-22の工程別設定変更の記録内、commit: 7454e5938 | 役割と工程の対応をrole frontmatterへ集約する構成へ移した。 | 役割文書が工程の対応を所有する。docs/development/design-workflow-models-waiting.md「工程別モデル設定とCI修正」 | 撤去理由を解消できることを示した場合 |
| 段位と用途別engine順を別々の保存キーで組み合わせてモデルを選ぶ | 2026-10-22、commit: 7454e5938 | 工程ごとの直接候補列を指定する明示要求で置き換えた。 | 工程ごとの候補列からengine・model・effortを解決する。docs/development/design-workflow-models-waiting.md「工程別モデル設定とCI修正」 | 撤去理由を解消できることを示した場合 |
| 旧旧4キーから段位制へ移す旧設定移行処理 | 2026-10-23、commit: 7454e5938 | 工程別設定への移行に合わせて旧旧形式の移行処理を撤去した。 | 現行の旧段位値から工程別候補列へ一括移行する。docs/development/design-workflow-models-waiting.md「工程別モデル設定とCI修正」 | 撤去理由を解消できることを示した場合 |
| 旧環境変数を互換入力として解決する | 2026-10-23、commit: 7454e5938 | 旧環境変数の互換解決を工程別の明示設定へ置き換えた。 | 廃止変数には対応する工程別変数を案内する。docs/development/design-workflow-models-waiting.md「工程別モデル設定とCI修正」 | 撤去理由を解消できることを示した場合 |

# 工程別モデル設定と待機の設計記録

[設計記録の索引](design.md)と[担当の責務と工程間の知識境界の設計記録](design-workflow-boundaries.md)から参照する主題別の記録である。

## 工程別モデル設定とCI修正

レーン担当の計画・実装・変更範囲検証・レビュー修正とCI修正はexecuteへ割り当てる。
公開担当のCI修正もexecuteを使う。
確定済みの単発コーディングや中規模走査はchores、定型走査・軽微な作業・コマンド実行はchores_fastへ分け、判断の所有者が統合する。
実行レビューはreview、小規模レビューはreview_fast、最上位はorchestrate、文章起草はwriteを使う。

config.pyが7工程の直接候補列を所有し、各値を(engine, model, effort)へ分解して既存の系列解決と候補選択へ渡す。
段位と用途別engine順を独立に持つ方式は2026年10月22日の明示要求で置き換える。
agy・OpenCodeを必要な工程へ直接指定でき、solをexecuteとorchestrateの予備に置けるためである。
プリセットはwrite以外の6工程の組を保存する。
初期値と5プリセットの対応はガイドの「工程別モデル設定」にある。
show・getは設定値を表示し、Codex系列名の最新IDへの解決は起動時に行う。

役割と工程はrole frontmatterが所有し、usageは撤去する。
TASK_MODEL_TYPESは旧形式の役割文書の読取互換に限る。
自由本文のdelegateは工程名か候補列を必須とし、explore・shellはchores_fast、writeはwriteを既定値にする。

移行は旧段位・engine順の保存値を6工程へ一括合成し、同居する古い工程値を上書きして廃止キーを除去する。
段位制導入前の残骸が残っている実設定の観測に基づき、新キーの存在だけでは移行済みとみなさない。
旧旧4キーから段位制への移行と旧環境変数の互換解決は撤去し、廃止環境変数には対応する工程別変数を案内する。

## 待機と停滞の観測

Codexの`running`は終了未観測の状態であり、補助観測値から停滞へ変換しない。
待機報告の観測値は書込主体と対象範囲を伴う。
作業ツリー全体値は並行更新を含む補助情報としてだけ扱う。
ホスト観測手段は一覧掲載と実際の呼び出し成功を別に検証する。
`ListAgents`が不在か、呼び出しを拒否された場合は、保持済みのID、完了通知、成果物観測と自身が起動して識別子を保持したプロセスの生存を`fallback`で照らし合わせる。
プロセス名の一致だけから生存状態や対象を推定しない。
複数委譲先の能力比較は全対象への同一機械照会が成立した場合だけ確定し、成立しない場合は未検証を返す。
同一turn内の補助観測だけを理由とする状態照会は反復させず、完了通知の不着、次の定時turn、終了statusの確認でだけ再照会する。
この扱いは`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`が定める。
Claude Codeの委譲・背景処理の待機は、機械的な完了通知で待機を解除し、`CronCreate`、`CronList`、`CronDelete`が実行主体へ公開される場合だけ定期再確認を併用する。
`agents_server`の`start`ではagent-toolkitのmodが一覧取得・作成・削除の能力を確認して定期再確認を作成する。
modが無い環境では必要なCronツールを使えるメインが作成する。
定期再確認は`atk wait-schedule`が出力したcron式を変更せずに1件だけ作成し、役割標識とtask IDを一覧と比較しながら再利用する。
共通promptのtaskは全待機対象の終了と結果回収後にmodが自動回収し、追加義務付きのtaskはセッション終了時の回収へ渡す。
mod自身の削除能力が無い場合は作成を見送る。
`ScheduleWakeup`は`/loop`専用であり、一般の委譲待機へ広げない。
定期起動を完了・停滞の証拠にせず、シェルの`sleep`や背景タイマーを追加しない。
`claude-code-runtime.md`はClaude Codeの能力とCron所有を、`waiting-and-monitoring.md`は完了通知を優先する再待機を基準として保持する。
`agent-toolkit/agent_toolkit/_hooks/background_tasks.py`はCron作成後も継続中とするStop判定を基準として保持する。
Stopは既存の非同期待機判定を同じ会話の`turn.complete`へ1回限りで渡す。
modは判定を消費して一覧から所有taskを再解決するため、待機対象の別台帳を持たずに作成・保持・回収を管理できる。
取得不能な判定は回収の根拠から外し、削除の拒否・失敗はtaskを保持して同じ会話へ理由と次の操作を届ける。

委譲元が`atk agents wait`で委譲先の結果を待つ設計は`design-delegation.md`「委譲先セッションの結果待機」にある。

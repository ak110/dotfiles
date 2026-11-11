# オーケストレーター選択の設計記録

[設計記録の索引](design.md)と[継続処理の起動・隔離・制御の設計記録](design-process-loop.md)から参照する主題別の記録である。

## process-loopのオーケストレーター選択

`atk wi process-loop`の常駐セッションで使用するengine・model・effortは、工程別モデル設定の候補列で決定する。
候補列は工程別の`orchestrate_model`が直接持つ。
各値は`agent-toolkit/agent_toolkit/_atk/config.py`の`resolve_mutable_setting`が、環境変数`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`、保存値、未設定時の値の順に取得する。
未設定時の候補列は、`codex-balanced`プリセットと同じ`claude:opus[1m]/medium,codex:sol/medium`である。

`agent-toolkit/agent_toolkit/_atk/wi/process_loop.py`の`cmd_process_loop`は開始時に`process_loop_session.py`の`resolve_orchestrator_specs()`で設定値を検証する。
さらに子セッションを起動する前ごとに同関数で候補列を読み直し、`select_available_orchestrator`が先頭の候補から可用性を判定して最初に利用できる候補で起動する。
このため常駐中に`atk config set`で保存した変更は、次の子セッションの起動前に反映される。
空でない環境変数がある間は、保存値を変えても環境変数の値が実効値になる。
候補列を子セッションの起動前ごとに読み直すため、可用性とCodex系列名の解決は起動時点の利用枠とモデル一覧で判定され、設定の変更も常駐プロセスを再起動せずに次の子セッションへ届く。

CLI引数の`--orchestrator`・`--model`を設定と併存させる案は、設定との優先関係を複雑化するため
ユーザー合意で廃止した。

Codexの`astra`・`sol`・`terra`・`luna`は設定へ系列名として保存する。
完全IDへの解決は`resolve_orchestrator_specs()`内の`resolve_model_candidates("orchestrate")`が子セッションの起動前ごとに行い、App Serverの`model/list`を最終ページまで取得して表示対象の同系列の最新版へ解決する。
委譲起動は保持中のApp Server接続を使い、設定CLIは短命の接続を所有する。
選んだ完全IDが指定effortを受理しない場合は理由を返し、別系列または旧版へ暗黙に置換しない。
明示された完全IDは固定し、engine側の可用性判定が返す診断を候補とともに示す。
Claude候補の可用性判定は構造化出力（`stream-json`）で起動し、Weekly limitか5時間の利用上限による拒否を出力の利用枠情報から判定する。
この拒否では次の候補へ進まず、解除予定時刻（不明なら300秒後）まで待って同じ候補を判定し直し、待機の種類と解除予定時刻を端末とprocess-loopのログ（`usage_limit_wait`）へ出力する（ユーザー指示）。
本作業のセッションが利用上限で終わった場合も、次の反復の可用性判定が同じ待機へ入る。
モデルを試行起動して系列の最新版を推測する案は、実行費用を生み、利用可能なモデル一覧との対応も保証できないため採用しない。
複数の起動処理が同じ判断を持たないよう、一覧取得と系列解決の知識境界を`agent-toolkit/agent_toolkit/_common/codex_models.py`に置く。

review・choresの初期値ではCodex候補に`sol/medium`を使う。
`luna/xhigh`を前二者へ残す案は、ユーザーが速度と精度の釣り合いを見直したため採用しない。
これらのモデル名は運用中に変わる設定値であり、テストは工程別候補列の保存と実行時解決の接続を確認する。
2026年10月27日に`gpt-6.1-terra`（Computer use向け）を避けるため`sol/low`へ置き換えた。
しかし2026年10月22〜29日のCodexの記録では、`sol/low`の実行レビュー担当は最初の応答10件のすべてで点検を残したまま返り、`sol/medium`は4件すべてが完了した。
役割文書を持たない作業の途中終了は、`sol/low`が58件中26件、`sol/medium`が381件中21件だった。
途中終了は出力や文脈の上限によるものではなく、差分9ファイルの小さなレーンでも起きたため、レーンの規模で選び分ける案は採らない。
ユーザーは2026年10月29日の確認で`sol/medium`への変更を承認した。
候補を変える前の確認（`agent-toolkit/skills/delegation/references/process-model-settings.md`「工程別モデル設定」）をこの変更へ適用した。
2026年10月29日、保存済みの設定を`codex:sol/medium,claude:sonnet[1m]/medium`へ更新したうえで、この変更自身の実行レビュー担当を`model_type`を省略して起動した。
起動候補は`gpt-6.1-sol`のeffort `medium`で、最初の応答は返却形式どおりの完了（状態`completed`、未解決の指摘0件、全条件の判定行あり）だった。
2026年10月29日、ユーザーはreviewのCodex候補を`terra/medium`へ戻すよう指示した。
同日、保存済みの設定を`codex:terra/medium,claude:sonnet[1m]/medium`へ変えて実行レビュー担当を起動したところ、起動候補`terra/medium`の最初の応答と1回目の継続の応答はどちらも未完了で返り、2回目の継続で完了した。
この観測を受けたユーザーの同日の指示で、reviewの初期値は`sol/medium`のまま据え置いた。

Codexの可用性判定で利用上限を観測した場合は、次の候補を先に試す。
全候補が失敗し、そのうちに利用上限の失敗がある場合だけ最も早い解除予定まで待ち、先頭から判定し直す。
解除予定が不明なら300秒後に再確認し、待機の回数と総時間を打ち切らない。
上限判定は共通層がロールアウト記録から行い、選択と待機はorchestratorが持つ。

可用性判定は応答内容を使わず、engine単位の起動の成否と利用上限だけを観測する。
そのためchores_fast_model・review_fast_modelで設定した該当engineの候補（候補がなければClaudeはhaiku、Codexはluna）とeffort `low`で起動する（2026年10月18日、ユーザーの確認回答「最安モデル+軽量」）。
Claudeはセッションを保存せず、ユーザーとプロジェクトの設定・ツール・MCP・スキルを読まず、最小のシステムプロンプトで起動し、process-loopの接続設定の`--settings`は保つ。
`--bare`はOAuth認証を読まないため使わない。
Claudeの別名はそのまま渡して最新版へ解決させる。
Codexの系列名はChatGPT認証で系列名のままでは拒否されるため、既存の系列解決で完全IDへ解決し、1回の候補選択の中ではengineごとに1回だけ解決する。
Codexはユーザー設定とrulesを読まずに起動する。
利用上限の判定はrolloutを読むため、rolloutを残さない`--ephemeral`は`--json`の出力だけで判定できることを確かめるまで使わない。
候補モデル固有の利用上限とeffortの無視はこの起動形では判定しない。
ユーザーはモデル固有の制限は現状まれで、検出のための複雑化を避けるよう求めたため、代わりの検出や本作業の起動失敗時の再選択も加えない。

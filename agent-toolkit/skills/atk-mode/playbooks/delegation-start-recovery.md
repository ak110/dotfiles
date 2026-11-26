# 委譲の起動と応答を失った時の回復

目的は、起動結果を受け取れない時にも、同じ作業を重複して始めずに元の依頼を追跡することである。

## Codexのagents_server起動

1. `agents_server`の`start`はネイティブの委譲とは別の仕組みで、`model_type`からengine、model、effortを解決する（`agent-toolkit:atk-mode/references/model-settings.md`「工程別モデル設定」）。
2. `engine=claude`の指定は`spawn_agent`へ置き換えず、`agents_server`から起動し、候補列を渡す場合は手順1の資料に従って上書きの区分と根拠を付ける。
   指定のengineを実行する手段が無い時は、未完了として返すか、続行できない理由を報告する。
3. 起動の入力（通常の起動の`whole_contract_path`を含む）と依頼文は、`agent-toolkit:atk-mode/playbooks/delegation.md`に従う。
   起動後のturnが可用性のエラーで終わった時は、`agent-toolkit:atk-mode/playbooks/delegation-recovery.md`の引き継ぎ起動に従う。
4. 新規起動では、送信前にUUID v4を生成・保持し、`start_submit`へ`request_id`と、同期の`start`へ渡すはずの`arguments`（絶対`cwd`を含む）を指定する。
   引き継ぎの`handoff_from`には従来の`start`を使う。
5. 受付応答を受け取れなかった場合も、まず保持した`request_id`を`start_status`へ渡す。
   `not_found`なら同じIDと同じ引数で`start_submit`を再提示できる。
   `pending`なら短い照会を間隔を空けて繰り返す。
   `started`なら返った`session_id`、`root_session_id`（ある場合）、`handoff_record_path`（ある場合）を保持して終了待機に進む。
   `failed`なら返った診断で未起動の理由を調べる。
   `unknown`なら成否が未確定なので別のIDへ替えず、管理サービスの診断と起動済みセッションの記録を調べる。

## Claude CodeのAgent起動

`Agent`ツールを使う条件は`agent-toolkit:atk-mode/playbooks/delegation.md`「実行手段」が定める（`agents_server`が標準である）。

1. モデル、effort、バックグラウンドでの実行、省略時の値は、起動の時に公開されているスキーマとagent定義のfrontmatterで決める。
   `Agent`の起動が通信タイムアウトしたら、起動結果と稼働観測で依頼の受理を確かめる。
   agent ID・稼働・完了通知のいずれかで起動した担当を確認できたら、起動し直さず同じ担当を観測する。
   起動結果が未受理を明示し、拒否や利用上限など別の制約がなければ、依頼と起動条件を変えずに一度だけ再試行する。
   IDが返らないだけでは未受理とみなさず、受理が判定できないか再試行も失敗したら、起動側の診断に戻る。
2. 非対話の起動でターンを終えて通知を待つのは、通知の後に同じセッションが再開されると実行環境が保証する場合に限り、それ以外は同期の起動と公開された待機の手段で回収してfinishまで終える。
   通常の`atk run-skill`はホストのバックグラウンド実行設定を継承し、[一括振り返り](review-session-batch.md#起動)ではClaudeの子に`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`を渡してフォアグラウンドで回収する。
3. `agent-toolkit:atk-mode/playbooks/delegation.md`「会話を引き継ぐ委譲」でforkを選び、fork modeが有効なら`subagent_type: "fork"`で起動する。
   forkは親のモデルと会話を引き継いでバックグラウンドで動き、通常と同じ完了通知で返り、親からの1階層で完結する。
   fork modeは対話のセッションで有効、`-p`の非対話の実行とAgent SDKで無効であり、無効なら`agents_server`で新しく起動する。
4. 計画ファイルの作成をplan modeの下で委譲した時は、サンドボックスのパスへ書かれた内容をメインが検収してから正規のパス（`~/.claude/plans/`の中）へ反映する。
   正規のパスのファイルの削除と移動は、メインだけが行う。

## 会話を引き継ぐ起動

ホスト標準のforkは、次の全てを満たす時に`agents_server`の代わりに選ぶ。

- 委譲先が、委譲元の受けた指示、読んだ資料、調査の結果の大半を要し、新しく起動すると同じ背景を依頼文へ書き直すことになる。
- 委譲元と同じモデルとeffortで作業を完了でき、上位のモデルが担う判断は元の担当が持つ。
- 工程別のモデル設定のキーが割り当てられた工程でも、役割文書を渡す委譲でもない。
- 独立文脈レビュー（実行レビューなど、成果物の作成者と文脈を共有しない委譲先が先入観を持たずに行う判定）でもない。

forkの入力は、作業の対象、役割の分担、完了の条件、報告の様式に限り、会話で共有済みの規範と背景は省く。
Claude Codeでは`Agent`ツールの`subagent_type: "fork"`で起動し、Codexでは`fork_turns`を省くか`"all"`にし、どちらもモデルとeffortを上書きしない。

## Codex CLIの非対話の直接起動

通常の委譲が使えず、実行環境の制約と認可の範囲で非対話のCodex CLIを直接使う場合に限る。
依頼文を引数だけで渡す時は、追加の入力を待たないよう標準入力を閉じる（Bashの例: `codex exec "<依頼文>" < /dev/null`）。
標準入力で依頼文を渡す時は付けない。

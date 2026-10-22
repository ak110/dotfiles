# 登録リポジトリの中継と担当への受渡し

`atk wi process-loop all-repos`は登録したリポジトリの集合を取得し、各対象cwdへ通常の委譲として処理を中継する。
リポジトリ担当はそのcwdの規範を読み、取得済み候補から通常のprocess-wiを完遂する。
対象の選定・実装・Git操作・prepareとプロジェクト固有の判断は対象cwdの担当が持つ。
ホームで動く常駐プログラムは、担当の起動・所有・完遂判定・続行指示と返却の記録だけを行う。
ユーザーとの受渡しと、メインが担う判断・報告は担当自身が行う（「担当の役割」）。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-procedure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `selection-format.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `run-lanes.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `finish-session.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session.md` |

## 担当の役割

担当は対象リポジトリのprocess-wiのメインであり、自身のセッションが自身と配下の担当の記録の最上位である。
agents_serverの通常委譲として起動されるため委譲先向けの配送を受けるが、委譲元は常駐プログラムであり、返却・通知・判断の委任を受け取る主体はいない。

- メインと最上位セッションへ限定した条文を適用する。公開後の`agent-toolkit:completion-report`（作業完了報告、`agent-toolkit:session-review`による振り返り、報告用UWI）、WIの投入、UWIによる確認、`気付いた改善点:`の行の振り返りへの取り込みを自ら行う
- メイン向け規範は配送されないため、着手時に`${CLAUDE_PLUGIN_ROOT}/share/rules-main.md`と実行ホストの`rules-main.claude-code.md`または`rules-main.codex.md`を全文読んで適用する
- 委譲元への返却・通知・判断の委任を定める条文（`share/rules-subagent.md`の確認事項の即時通知と標識行の引継ぎ、`agent-toolkit/rules/01-agent.md`「完遂と先送り」の委譲先による返却など）は使わない。ユーザーだけが決める値はUWIで確認し、担当範囲外の既存不良と他リポジトリへの依頼はWIへ投入し、改善点は振り返りで扱う
- 配下の担当（レーン担当、実行レビュー担当、終端担当、WI投入担当など）に対しては通常の委譲元として起動・受領・検収する
- 常駐プログラムへの最終返却は記録用であり、対象、担当session、公開commit、CI結果、未完了工程と再開先を書く。続行できない場合は`続行できない理由:`の行で示す（常駐プログラムはこの行の有無で所有を解放する）
- `agent-toolkit:completion-report`の報告後の終端工程である`atk agents-exit-session`は実行せず、最終返却でターンを終える。同コマンドは祖先の対話CLI本体へ終了を要求するため、常駐プログラムが返却を受け取る前に担当を止め得る

## 登録集合と開始

対象は`atk config`の`process_loop_repositories`へ保存したローカルworktreeの絶対パスのJSON配列とする。
通常のcwd単一対象起動と登録外リポジトリへのWI投入は独立して使える。
空配列なら常駐側は待機し、不正JSON・解決不能・読取不能の場合は診断に示す登録先を直してから開始する。
同じoriginは登録順の先頭へまとめ、Git共通ディレクトリとworktreeを対応付ける。
登録変更は次の取得へ反映し、進行中の担当の処理対象WIは維持する。

一括取得は`atk wi list --configured-repos`で行える。
常駐側は同じ登録解決とキューの一括取得から、対象別の候補本文・依存・鮮度・回答済みUWI・readinessを得る。
担当の起動対象はWI候補または監査の開始条件があるリポジトリに限る。
活動猶予と、未回答UWIによる開始の抑止は、常駐側が起動集合全体に対して同時に判定する。
開始前の更新と自己再起動は全体で1回だけ行い、監査・CI・worktree準備と失敗・再開の状態は対象別に保持する。

## 取得済み候補の受領

常駐側は各対象cwdで通常の`delegate`を起動し、JSONの`target_repo`・`worktree`・`common_dir`で対象を示す。
`candidates`は初回取得済み候補、`readiness`は対象別の判定、`lane_limit`は対象別のレーン上限を表す。
`ownership_record`・`ownership_token`は取得済みの処理所有、`previous`は前回の成果と再開情報、`worktree_name`は隔離指定を表す。
担当は常駐側が渡した値と取得済み所有をそのまま使う。
`lane_session_id`は対象別のレーン所有を示し、作成・再開・終了時回収の各操作の`--session-id`へ同じ値を渡す。

担当は初期一覧の再取得を省き、`candidates`を初回候補として使う。
候補の`state`と、`readiness.ready`・`readiness.internal_dependency_waits`などの名前集合から通常の着手可否へ対応付ける。
候補レコードの`ready`と`blocked_reason`は同じreadinessから生成した対象別の判定であり、待機理由を判断するときも受領したreadinessの診断を使う。
一覧の`filename`と`staleness`を持つ受領レコードを、自セッションのmanaged-tempへJSON配列として保存し、`pick-wi-check`の`--candidate-file`へ渡す。
受領済みの本文と回答・依存情報を使い、不足する本文や依存先だけを通常のキューCLIで補う。
回答済みUWIの元項目の扱い、状態遷移、選定完了後の状態確認は[selection-procedure.md](selection-procedure.md)に従う。
選定YAMLは対象別に保存し、上限はその対象の候補数Nから求める`floor((N+9)/10)`とする。
複数対象の総件数をNへ置き換えず、初回情報と追加後の上限の扱いは[selection-format.md](selection-format.md)に従う。

担当が隔離指定を受けた場合は、対象のWorktreeCreate/Removeと準備・撤去の対応を含む[run-lanes.md](run-lanes.md)の資源操作を使う。
作成hookの返却先とGit登録・branch・共通ディレクトリ・所有の対応を確かめる。
設定済みhookの失敗では診断と途中資源を保持し、同じhookの再開へ渡す。
通常Git作成への暗黙の切替は禁止する。
hookが無い対象は固定配置と対象固有のprepareを使い、反復でも同じ配置を再利用する。
初期選定が1レーンの場合は、対象cwdのレーン担当が単一担当の実行契約でレビューの調整まで続ける。常駐プログラムはこの調整に関与しない。準備・統合・回収は同じ公開操作を使い、統合は[run-lanes.md](run-lanes.md)と`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`へ接続する。
公開とCIの検収は[finish-session.md](finish-session.md)へ渡す。

## 所有、制御と再開

処理所有は通常起動とall-reposで共有し、同じoriginのWIとwriterを1担当に保つ。
短い排他で所有記録と生存を検収し、長い処理や回答待ちでは担当sessionと登録記録で所有を保持する。
別所有者の終了後は候補を取り直し、異常終了では未統合成果を保持したまま既存の再開へ渡す。
1担当の失敗・所有待ちの間も、開始済みの別対象担当は処理を続ける。

常駐側は担当の終端後、起動時の候補にinbox・processingが残るか、対象リポジトリにprocessingが残るか、`lane_session_id`に未統合レーンが残るかで完遂を判定する。
未完了または待機期限で確定した保留結果には、同じsessionへ残るWI名とレーンを添えて続行を指示する。
担当は続行の指示を受けたら残りの工程を再開する。
常駐側は未完了の終端のたびに、対象リポジトリのinbox・processingにあるWIの名前と状態、`lane_session_id`のレーン登録ごとの統合済みか否かとbranchのHEADを記録する。
この記録が直前の終端の記録と2回続けて等しい場合と、返却に`続行できない理由:`の行がある場合は、続行を指示せずに所有を未完了として解放し、再開情報を次の反復へ保持して今回の待機を閉じる。
出力停止の検知と待機の失敗も常駐側で扱い、停止を確認できた担当の所有だけを解放する。
停止を確認できない担当の所有は保持し、以後の反復と常駐の再起動後も担当sessionの生存を確かめてから起動を判定する。

常駐側は所有を解放するたび（完遂、続行できない返却、無進捗の打切り、停止を確認した担当）に、その担当の`lane_session_id`のレーン資源を`atk lane delete`と同じ条件で回収する。
続行を指示した担当と、停止を確認できず所有を保持した担当の資源は回収しない。
未統合・未commit・使用中などの条件に当たる資源は保持され、次の反復の再開で同じ`lane_session_id`が担当へ渡る。
保持の理由と`atk lane delete --session-id <lane_session_id>`による再回収の操作は、常駐側の端末出力とprocess-loopのログに残る。

続行できない返却と無進捗の打切りの後は、その担当の起動時と終了時の両方にあった起動入力（候補WIと対処を要する診断の本文・着手可否の区分、起動時の未判定Dependabotアラート）を、このホストの無進捗記録へ残す。
同じ起動入力の間、常駐側はその対象の担当を起動せず、見送りと起動を再開する変化を端末とprocess-loopのログへ出力する。
起動を再開する変化は、新しいWIの到着、既存WIの本文・状態・依存・冷却期限の変更、着手可否の区分の変化、ユーザーコメントやUWI回答の記入、新しい未判定アラートである。
担当の稼働中に加わったWIは記録に含めず、次の反復で起動の対象にする。
`--resume`と`--auto-resume`の対象は記録によらず起動する。
このホストで処理できない候補を担当が`続行できない理由:`で返した場合も、同じWIを処理できる別ホストの起動は止まらない。

`abort`・`status`・`instruct`などの制御はOSアカウント単位で使う。
`instruct`は次のall-reposの起動集合へ1回だけ渡し、各担当は自分の対象に関わる内容を適用する。
all-reposの再開対象は、記録されたall-repos担当のsession IDに限る。
担当の返却は対象ごとの確認済み工程・残工程・再開先とともに保持し、続行できない理由と正常完了を分けて受領する。

# 登録リポジトリの中継と担当への受渡し

`atk wi process-loop all-repos`は登録集合の取得と対象cwdの通常委譲への中継を担う。
リポジトリ担当はそのcwdの規範を読み、取得済み候補から通常のprocess-wiを完遂する。
対象の選定・実装・Git操作・prepareとプロジェクト固有の判断は対象cwdの担当が持つ。
ホームで動くオーケストレーターは担当の結果とユーザーとの受渡しだけを扱う。

## 登録集合と開始

対象は`atk config`の`process_loop_repositories`へ保存したローカルworktreeの絶対パスのJSON配列とする。
通常のcwd単一対象起動と登録外リポジトリへのWI投入は独立して使える。
空配列は待機し、不正JSON・解決不能・読取不能では診断に示す登録先を直してから開始する。
同じoriginは登録順の先頭へまとめ、Git共通ディレクトリとworktreeを対応付ける。
登録変更は次の取得へ反映し、進行中の担当の処理対象WIは維持する。

一括取得は`atk wi list --configured-repos`で行える。
常駐側は同じ登録解決とキューの一括取得から、対象別の候補本文・依存・鮮度・回答済みUWI・readinessを得る。
担当の起動対象はWI候補または監査の開始条件があるリポジトリに限る。
活動猶予と未回答UWIの開始抑止は常駐側が起動集合全体へ同時に評価する。
開始前更新と自己再起動は全体で1回、監査・CI・worktree準備・失敗と再開は対象別に保持する。

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
1レーンでも準備・統合・回収は同じ公開操作を使い、統合は[run-lanes.md](run-lanes.md)と`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`へ接続する。
公開とCIの検収は[finish-session.md](finish-session.md)へ渡す。

## 所有、制御と再開

処理所有は通常起動とall-reposで共有し、同じoriginのWIとwriterを1担当に保つ。
短い排他で所有記録と生存を検収し、長い処理や回答待ちでは担当sessionと登録記録で所有を保持する。
別所有者の終了後は候補を取り直し、異常終了では未統合成果を保持したまま既存の再開へ渡す。
1担当の失敗・所有待ちの間も、開始済みの別対象担当は処理を続ける。

`abort`・`status`・`instruct`などの制御はOSアカウント単位で使う。
`instruct`は次のall-reposの起動集合へ1回だけ渡し、各担当は自分の対象に関わる内容を適用する。
all-reposの再開対象は、記録されたall-repos担当のsession IDに限る。
担当の返却は対象ごとの確認済み工程・残工程・再開先とともに保持し、続行できない理由と正常完了を分けて受領する。

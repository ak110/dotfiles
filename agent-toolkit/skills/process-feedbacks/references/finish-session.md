# push、CI及び終了

③の開始時に、通常レーンがffマージ、`adopt`及び後始末まで完了し、固有指示で`adopt`を延期したレーンがffマージと実装資源回収を完了したprocess-feedbacksのメインがこの文書を全文読む。
①で固定した集合の全レーンが完了した時点で③を開始し、ready一覧を再取得しない。
ここでは先行レーンが渡した終端工程だけを処理し、③の開始後にactive一覧を再取得しない。

## 生成物とpush

配布物の版数更新を要する変更では、メインが対象リポジトリの版数規範へ未push差分を照合して種別を判定し、版数更新と派生manifest同期を機械操作として実施し、差分と生成同期を検査する。
この機械差分だけを対象とする追加レビューは行わない。

メインがベースbranchをpushし、pushした完全OIDのCIを確認する。全レーン横断の総合レビューと統合差分レビューを追加しない。

## CI失敗

CI失敗は`agent-toolkit:bugfix`のCI失敗処理契約で原因を確定する。修正commitが必要な場合は専用worktreeのCI修正レーンを作成し、
担当種別`CI修正担当`として起動した`execute_model`の実装担当と単一の`implementation-review`だけを実行する。新しい計画と計画レビューを作成しない。

CI修正レーンは修正、検証、commit及び実装レビュー後に最新ベースへrebaseし、メインの直列許可を得てffマージし、所有worktree、branch及びmanaged-tempを回収する。
各マージ後かつ再push前に、直前にpushした完全OID以降の未push差分を版数規範へ照合する。
pluginのエンドユーザー向け振る舞いが変わった場合は、変更内容に応じた版数更新を追加で行い、source manifest、派生manifest及び生成同期を再検査してから再pushする。
CI成功まで同じ原因別経路と再判定を反復し、既に`adopt`済みの元フィードバックへ重複したキュー操作をしない。

## 固有の終端工程

フィードバックが明示するPR又はMR、release、tag、配布等の終端工程は、①で記録した依存順に、pushとCI成功後に実行する。
これらの後へ`adopt`を延期する固有指示がある場合は、先行工程の成功を確認後に当該レーン責務として保存済みのベースHEAD完全OIDで`adopt`し、保存結果を照合してレーンを完了させる。
旧来の公開グループ、marker JSON、再探索及び専用owner表を作成しない。

## セッション終了

最後に`agent-toolkit:completion-report`を起動する。同スキルは`agent-toolkit:process-feedbacks`実行条件により`agent-toolkit:session-review`を必ず実施し、固有成果と振り返り結果を1回だけ報告する。報告完了後に`agent-toolkit:exit-session`を起動する。

active一覧を再取得して追加分を同じセッションへ混ぜず、追加分は次回セッションで扱う。

# 監査記録: commitの打ち切りと復元

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/commit/references/commit-creation.md：hookの打ち切りと退避の復元：2026年11月10日

- 観測日: 2026年11月10日。
- 確認版: Linux、Python 3.15.2、pytest 9.2.0、prek 0.5.9、agent-toolkitの作業ツリー版。
- 再検証: `agent-toolkit/skills/commit/scripts/run_commit_test.py`の公開commit入口試験で、時間切れとプロセスグループSIGINT、別セッションの検証子、未ステージ差分の復元と無関係な実行の保持を確認する。
  実pyfltrとの境界は、隔離Git・独立したpyfltrキャッシュ・ローカルprek hookを用意し、`*_test.py`の検証子から別セッションの子を起動して同じ2終了経路を観測する。
  親終了時の所有子孫の状態、退避復元と次の検証の完了を別々に確認し、観測側の後始末による回収を製品の回収成功へ数えない。

## agent-toolkit/skills/commit/references/commit-creation.md：hookの打ち切りと退避の復元：2026年11月11日

- 観測日: 2026年11月11日。
- 確認版: Linux、Claude Code（モデルclaude-fable-5-2のセッション）、prek 0.5.9、agent-toolkitの作業ツリー版。
- ホストの時間上限: Claude Codeの`Bash`の入力仕様は、前景の`timeout`を最大600,000ミリ秒と定める。
  `run_in_background`を指定した場合は省略時1,800,000ミリ秒、最大7,200,000ミリ秒である（同セッションのツール定義で確認した）。
  Claude Code以外のホストの値は確かめていない。
- 観測1（変更前の起動側）: 隔離したリポジトリに、終わらないpre-commit hookと未ステージの変更を置いた。
  `atk run-script commit-with-timeout -- --timeout 660`を`run_in_background: true`、`timeout: 900000`で起動した。
  ホスト全体で1つの検証の実行枠を別の検証が保持しており、起動側は枠待ちを`--timeout`に数えなかったため、660秒では終わらず、900秒でホストに止められた。
  commitは開始しておらず、未ステージの変更は残った。
- 観測2（`--timeout`へ枠待ちを含めた起動側）: 同じリポジトリで、独立したpyfltrキャッシュ（別の実行枠）を指定して同じ値で起動した。
  起動から664秒後に終了コード124で終わり、prekが退避を戻した旨を出力した。
  `git diff --binary --src-prefix=a/ --dst-prefix=b/`の出力は起動前に保存した差分と一致し、commitは作られず、hookのプロセスは残らなかった。
- 再検証: 枠待ちを上限に含めることは`agent-toolkit/skills/commit/scripts/run_commit_test.py`の、実行枠を保持する別プロセスを置いた公開commit入口試験で確かめる。
  ホストの上限との前後関係は、観測2と同じ構成（終わらないローカルhook、600秒を超え背景の`timeout`より短い`--timeout`）を実際のClaude Codeで起動して確かめる。

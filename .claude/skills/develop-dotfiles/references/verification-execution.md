# 検証の実行と所要の基準

変更対象から実行するテストを決める時は[検証範囲の定義](verification.md)を使う。

## 検証コマンドの実行

共有の実行枠の対象は、`make test`、`uv run --frozen pyfltr run`、`git commit`の中の`pyfltr fast`、共通のpytestプラグインを読むpytestである。
直下の`pyproject.toml`の`addopts`が同プラグインを`-p`で読み、`make test-browser`など`-o addopts=''`で外すものは枠の対象外となる。
枠はホスト全体で2つ（pyfltr 3.30.0以降）で、pytestと統合の検証は開始からworker・子孫の回収まで枠を持ち、入れ子の実行は祖先の所有を継ぎ、収集だけのpytest（`--collect-only`）は枠を取らない。
シェルからの起動は`agent-toolkit:atk-mode/playbooks/verification.md`「統合実行ツール経由の起動」の形式と3分類に従う。

1. 対象を[verification.md](verification.md#変更範囲の検証の値)「変更範囲の検証の値」で確定する。
   `.venv`を作り直しうるチェック（`uv run --python`や依存の更新など）は同じ仮想環境のパスでは直列に実行し、並行する場合は別の仮想環境のパスを指定する。
2. 修正の条件を選ぶ。
   自動修正は`make format`（`uv run --frozen pyfltr fast`）、調査と計測は`--no-fix`を使う。
   統合の準備と最終の検証の前に、通常の実行で整形と生成物の更新を済ませて内容を確定し、以後の読み取りの実行は`--no-fix`に加えて、通常の段でも書き換えるフォーマッターを`--commands`の対象から外す。
   現在の書き換えの段は`ruff-format`、`uv-sort`、`shfmt`、`prek`、`sync-generated-files`である。
   対象の版の実行のheaderにある有効な全コマンドと`pyfltr command-info <コマンド名> --output-format=json`の実効情報を確かめ、書き換えの段を除いた必要な全チェックを`--commands=<カンマ区切りのコマンド名>`へ指定する。
   設定や版が変わったら一覧を取り直し、選んだ一覧と除外の理由を統合する担当へ渡す。
   分岐の効果を確かめる差分は`git diff --no-ext-diff --src-prefix=a/ --dst-prefix=b/`で保存し、`git apply`・`git apply -R`で適用・復元する。
   `diff.noprefix=true`では通常の`git apply`が接頭辞なしのパスを解決できないため、この形を使う。
3. 静的チェックを先に通す。
   `uv run --frozen pyfltr run --disable pytest <対象ファイルの絶対パス>`を失敗が無くなるまで直し、修正後の再実行は失敗したチェックへ限定する。
   pyfltrは1回の実行の間ずっと枠を持つため、静的チェックで失敗する実行にpytestを含めると枠を数分ずつ無駄に占める（2026年11月12日の同時数12では、失敗した`pyfltr run`18件のうち12件が静的チェックだけの失敗で、その12件でもpytestが流れていた）。
4. commitする内容を確定してから、pytestを含む実行を1回行う。
   MCPの`run`へパスを渡すか、`uv run --frozen pyfltr run <対象ファイルの絶対パス>`を`--commands`の指定なしで実行し、失敗したら直して同じ形で実行し直す。
   `develop`・`master`・`main`以外のブランチ（レーンと内部群の作業ブランチ）では`--no-always-targets`を付け、変更ファイルと、変更した契約を使うテストを実行する（pyfltr 3.31.0以降）。
   変更が影響する不変条件テストも引数へ加える。
   全量は統合後のdevelop CIで実行する。
   非テストモジュールのパスだけでは、そのモジュールを使う別名のテストが選ばれないため、`uv run --frozen python scripts/pyfltr_precommit.py --consumer-tests <変更ファイル...>`で根拠付きの候補のJSONを取得する。
   `required=true`の直接importのテストを引数へ加え、残る候補は実際の利用関係から採否を決める。
   元のJSONを保持し、曖昧な候補ごとに`source`・`test`・`include`（真偽値）・`reason`（空でない文字列）を持つ`decisions`配列を足して検証の証拠へ保存する（手順7の`--consumer-review`へ渡す判定JSON）。
   動的な呼び出し、設定の移行、通知の旧期待値、CLI全体の一覧は候補の自動列挙で網羅できないため、[verification.md](verification.md#変更範囲の検証の値)「変更範囲の検証の値」の探索で補う。
5. 失敗の確かめ直しには次の形を使う。
   - 失敗したテストだけは`--only-failed --from-run <初回のrun_id>`で実行する。
     `--from-run`を省いた`--only-failed`は他の作業ツリーを含むホスト全体の直近の実行を参照し、それに失敗が無ければ何も実行せずに終了0となり、自分の失敗が残っていても成功として報告される。
   - 単一のテストは、対象ファイルと必要なら`-k`を渡した直接pytestで確かめ、収集件数が想定と合うかを見る。
   - 直接pytestは、デバッガー、最小再現、環境の切り分けに使える。
     `-o`・`-p`はpytest固有で、`pyfltr run`へ渡すと終了2になる。
     `-o addopts=''`で設定全体を外す場合は`-p no:cacheprovider`も付ける。
     並列化だけを止める場合は`-n 0`を使い、`-p no:xdist`は既存の`-n 4 --dist=worksteal`が認識されずに収集前に終了4となる。
6. commitする。
   commitのhookは`pyfltr fast`を実行し、`develop`・`master`・`main`以外のブランチではpytestを外す。
   統合先のブランチでも、同じ内容でpytestが成功した`pyfltr run`があればpytestを外す（判定の条件は`scripts/pyfltr_precommit.py`の冒頭）。
   統合先のブランチで、pytestを含む実行の後にファイルを変えた場合と、commitしない差分を作業ツリーに残した場合は、hookがpytestを再び実行するため、含めない差分は先に別のcommitへ分けるか作業ツリーから外す。
7. 統合の準備は手順4までの成功で足り、全量の成功を得るためだけの再実行は不要である（準備の受理は統合の許可であり、最終の検証の成功と分ける）。
   統合する担当は、許可の後に統合先の最新内容へ追随して最終の統合入力を確定し、専用worktreeで`make sync`を実行してから、最終の内容で1回の実行を成功させる。
   定義元の`agent-basics:check-execution/references/verification-scope.md`「実行条件と再実行」は、条件の一致する成功の集合を合わせることを認める。
   dotfilesの統合前の最終確認はこれより厳しい追加の条件とし、最終の内容での単一の実行に限る。
   - 形は`uv run --frozen pyfltr run --no-fix --no-always-targets --commands=<手順2で選んだ全コマンド> <対象ファイルの絶対パス>`とする。
     変更箇所・直接の利用側・競合解消に関わるテストと、develop CIが実行しないチェックを含める。
   - 起動の前に`uv run --frozen python scripts/pyfltr_precommit.py --check-argv '<予定argvのJSON配列>' --consumer-review <判定JSONの絶対パス>`で引数と必須の範囲を確かめる。
     argvは`["run", "--no-fix", "--no-always-targets", "--commands=...", "<対象パス>"]`の形とし、曖昧な候補が無ければ`--consumer-review`を省く。
   - indexと作業ツリーを最終の統合入力のHEADへ一致させる。
     fast-forwardの直前に`uv run --frozen python scripts/pyfltr_precommit.py --verify-run <run_id> --consumer-review <判定JSON>`の終了0を確かめる。
     `--check-argv`の終了0は実際の実行の成功、版、cwd、HEADとindex、実行後の不変を確かめないため、verify-runは省かない。
   - 統合後・公開前に足した是正（統合時の検証失敗、CI失敗、案内文や構成の追随など）では、変えた文言・ジョブ名・設定キーの旧値を固定文字列で探し、それを期待するテスト（`ci_workflow*_test.py`、`<hook名>_test.py`など）を是正後のHEADで実行してから公開する。
   - 最終の検証が失敗したらfast-forwardせず、是正、必要な再レビュー、統合の再開へ戻る。
   - 検証がmise管理のツールを使う場合は、追随後のレーンworktreeで `mise which <tool>` を実行して設定が選ぶ実行ファイルを確認し、検証と同じPATHから対象コマンドを `--version` など副作用のない引数で起動する。
     どちらかが失敗したら検証へ進まず、実行ディレクトリ、ツール名、miseと起動の診断を記録する。
     ツールが未導入なら `MISE_LOCKED=1 MISE_LOCKED_SCOPES=project mise install` を案内し、実体が見つかっても起動できない場合はPATHの順序とshimの参照先を確認する。
   - manifestへ渡す場合は、追随後のレーンworktreeでの `make sync`、実行の成功、run_idの取得、同じ入力のverify-runの成功を、失敗が伝わる1操作にまとめる。
     次のスクリプトを自分のmanaged-tempへ保存し、manifestのverificationに次の配列を1項目として渡す。

     ```json
     ["bash", "<スクリプト>", "<ログprefix>", "<判定JSON>", "--no-fix", "--no-always-targets", "--commands=<全チェック>", "<対象パス>"]
     ```

     山括弧は実在の絶対パスと値へ置き換え、複数の対象パスは別々の要素にする。
     判定JSONが不要な場合は、その入力と`--consumer-review`を省いたスクリプトを使う。
     mise管理ツールが必要な場合に限り、上記の照会と起動確認を、同じ検証argvが呼ぶ下のスクリプト内の `make sync` と `pyfltr run` の間に入れる。
     manifestを渡す前の照会では、追随後のlockと検証時のPATHを確認できない。

     ```bash
     #!/usr/bin/env bash
     set -euo pipefail
     log_prefix=$1
     consumer_review=$2
     shift 2
     make sync
     # mise管理ツールが必要なら、ここで照会・起動確認する。
     uv run --frozen pyfltr run --output-format=jsonl "$@" > "$log_prefix.out" 2> "$log_prefix.err"
     run_id=$(jq -er 'select(.kind == "header") | .run_id' "$log_prefix.out")
     uv run --frozen python scripts/pyfltr_precommit.py --verify-run "$run_id" --consumer-review "$consumer_review"
     ```

     verificationを空にする場合は、受理まで終えた同じ入力の両出力と終了状態を既存の記録へ対応付ける。
8. CIが無いか公開先でCIが実行されず、ローカルの全体pytestが必要な場合は`--maxfail=10`を付け、計画からmanifestまで同じ値を保つ。
   直接pytestでは`--maxfail=10`、pyfltr経由では`--pytest-args='--maxfail=10'`とし、他のpytestの追加引数は同じ値へ併記する。
   少数の独立した失敗を1回で集めつつ、共通の準備の不具合で全テストが失敗した時の出力を有限に抑えるためである（10件を超える失敗では未実行のテストが残り、xdistの実行中のテストにより最終の失敗数は10を超えうる）。
   CIが全体を担う通常のレーン内・統合時・公開前は関連範囲だけを実行し、`addopts`の値5、限定の検証と失敗箇所の再確認、`make test`の通常の起動、CIの`--maxfail=0`、時間の上限はそのまま使う。

## テストとCIの所要基準

段階の選び方と超過時の検討は`agent-basics:writing-standards/references/testing.md`「速度」に従い、本節はdotfilesの値を持つ。
所要時間の比べ方は`agent-basics:check-execution/references/diagnostics.md`「読む出力」に従う。

| 必要な仕事 | テスト本体の改善判断値 |
| --- | --- |
| 同一プロセス内の通常処理 | 0.1秒 |
| 契約上不可避の実時間待機を含む処理 | 0.5秒 |
| 実プロセス・実Gitなどの境界を使う処理 | 2秒 |
| 外部接続・ブラウザー・大規模結合など | 固有の検出契約、最小構成、起動・待機回数に基づく個別の所要時間見込み |

通常処理と実境界の値は2026年10月31日の選択標本88ケースで本体87ケースが0.1秒以下、実プロセス1ケースが0.73秒だった実績を出発点とする。
待機の基準値には余裕を持たせる（必要な待機量0.08秒の共有ロックテストは本体0.11秒）。
いずれも選択標本に基づく改善判断値であり、全テストの分布から推定する上限と分ける。

pytestのCPU上限は実時間と別の合否の契約で、`agent-toolkit/agent_toolkit/_testing/pytest_plugin.py`が判定する。

- 計上するのは、setup・call・teardownのテスト実行threadと、そのテストが起動したThread・ThreadPoolExecutorへ投入した仕事（既存poolへの投入を含む）のCPUであり、他のテストやセッション基盤のthread、子プロセスのCPU、待機の実時間は数えない。
- 通常のテストは、初回importと共有heapの旧世代の自動GCの区間を控除した値が0.5秒未満で、控除前のgrossが2秒以下であることを求める。
- 通常の上限内で保証できない実プロセス・実Git・ブラウザーなどの境界テストは、契約と最小構成の理由を示して`pytest.mark.cpu_limit(seconds, reason=...)`で正の有限な個別上限を指定し、grossの上限は個別上限に共有費用の許容量1.5秒を加えた値になる。
- Windows CIのpytestは`--cpu-limit-record-only`で超過を警告とJUnitへ記録するだけにし、CPUの回帰はLinuxのCIとWindows実機でのローカル実行で検出する。
  GitHubのWindowsランナーでは、同じテストの所有CPUが再試行の間で最大2.7倍揺れたためである（run 44315531949）。
- 実時間のdurationsの表示と60秒のハング検出は保ち、CPU上限の成功を実時間の改善の証明に使わない。

CI workflowの全体の改善判断値は、対象HEADの実行開始から必須ジョブの終了までの実時間10分とする。
以前の同workflowの成功実績8〜9分へ約1〜2分の余裕を持たせた値であり、キュー待ちと人間の承認待ちは分ける。
公開の担当とPRの検収の担当は成功時も比べ、律速ジョブの準備・本体・終了処理の内訳を検証記録へ残し、10分を超えたら原因を調べる。

`ci.yaml`・`audit.yaml`・`release-statusline.yaml`の各ジョブの`timeout-minutes`は遅延を失敗として検出する上限であり、10分の改善判断値と別に扱う。
同じジョブ（OS・行列・所有の有無）の直近60 runの正常完了（スキップ・取消・失敗を除き、実処理のstepを実行したもの）の最大所要時間に、その50%と60秒の大きい方を加えて分単位に切り上げる。
結果を集約するジョブは自身の開始から終了までだけを数え、先行ジョブの待ちを含めない。
60 runに正常完了が無いか少ないジョブは、それより前の同じ定義の正常完了で補い、補った範囲を変更記録に残す。
ジョブの構成を変えた場合と律速の処理を短縮した場合は、変更後の正常完了の実績から算出し直す。

### toolkitの所要記録更新

公開の担当はtoolkitの3shardに偏りを観測したら、同じ成功runの全3shardのJUnit XMLを取得して所要時間記録を作り直し、commitする。
作り直す形は`python3 scripts/update_test_durations.py shard1.xml shard2.xml shard3.xml --output agent-toolkit/agent_toolkit/_testing/test_durations.json`である。
各runnerはCIの中で前のrunを取得せず、同じcommitの記録を使う。
公開の後に、全体の所要時間と最大shard/平均shardの所要時間の比を比べ、JUnitの和集合が同じcommitの収集集合と一致するかを確かめる。

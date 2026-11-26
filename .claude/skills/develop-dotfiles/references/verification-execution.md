# 検証の実行と所要の基準

変更対象から実行するテストを決める時は[検証範囲の定義](verification.md)を使う。

## 検証コマンドの実行

共有の実行枠の対象は、`make test`、`uv run --frozen pyfltr run`、`git commit`の中の`pyfltr fast`、共通のpytestプラグインを読むpytestである。
直下の`pyproject.toml`の`addopts`が同プラグインを`-p`で読み、`make test-browser`など`-o addopts=''`で外すものは枠の対象外となる。
枠はホスト全体で2つ（pyfltr 3.30.0以降）で、pytestと統合の検証は開始からworker・子孫の回収まで枠を持ち、入れ子の実行は祖先の所有を継ぎ、収集だけのpytest（`--collect-only`）は枠を取らない。
シェルからの起動は`agent-toolkit:atk-mode/playbooks/verification.md`「統合実行ツール経由の起動」の形式と3分類に従う。
Codexでは、共有枠を取る構成のpyfltr検証を、検査の所要にかかわらず初回からCLIで起動する。
呼出元のmanaged-tempに実行専用のprefixを決め、標準出力・標準エラー・終了コードを別々に残す。
次は同じ実行の起動応答を失っても結果を読み直せるBashの例である。

```bash
prefix=/absolute/managed-temp/unique-verification
uv run --frozen pyfltr run --output-format=jsonl "$@" > "$prefix.out" 2> "$prefix.err"
rc=$?
printf '%s\n' "$rc" > "$prefix.exit.pending"
mv -- "$prefix.exit.pending" "$prefix.exit"
exit "$rc"
```

実行の引数は検証対象に合わせて渡す。
起動応答を失っても再起動せず、そのprefixの`.exit`、`.err`、`.out`を観測する。
`.exit`が無ければ終了未確定であり、0以外は成功としない。
`.out`の`header`にある`run_id`で必要に応じて同じ実行のアーカイブを照会する。

方式や性能を比べるための全量計測（全ファイルのPylintを方式ごとに比べる実行など）は、変更の受入に必要な検証と分けて扱う。
まず既存の実行記録の再利用と代表対象での比較を選び、全量でしか判断できない比較だけを、自分の巡回の必須検証と重ならない時間帯に置き、比較を繰り返す場合はその時間帯で直列に実行する。
受入に必要な検証は直列化せず、枠の数・検査対象・成否条件は変えない。

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
3. ソースの初回編集でコメント検査を有効にしている場合は、代表ファイル1件だけに既存の `comment-check` を実行する。
   検査が対象へ到達したかと診断の有無を別々に確かめ、違反を直してから同じ書き方を他へ展開する。
   `uv run --frozen pyfltr run --no-fix --commands=comment-check <代表ファイルの絶対パス>`。
   対象の限定に失敗しても全体へ広げず、未実行と診断なしを区別する。
   通常の静的チェックをpytestより先に通す。
   `uv run --frozen pyfltr run --disable pytest <対象ファイルの絶対パス>`を失敗が無くなるまで直し、修正後の再実行は失敗したチェックへ限定する。
   pyfltrは1回の実行の間ずっと枠を持つため、静的チェックで失敗する実行にpytestを含めると枠を数分ずつ無駄に占める（2026年11月12日の同時数12では、失敗した`pyfltr run`18件のうち12件が静的チェックだけの失敗で、その12件でもpytestが流れていた）。
4. commitする内容を確定してから、pytestを含む実行を1回行う。
   上記の条件でMCPの`run`へパスを渡すかCLIで起動し、`--commands`は指定しない。
   失敗を確定したら直して同じ形で実行し直す。
   `develop`・`master`・`main`以外のブランチ（レーンと内部群の作業ブランチ）では`--no-always-targets`を付け、変更ファイルと、変更した契約を使うテストを実行する（pyfltr 3.31.0以降）。
   全量は統合後のdevelop CIで実行する。
   `uv run --frozen python scripts/pyfltr_precommit.py --consumer-tests <変更ファイル...>`で、文書を読む横断試験やhookの入口を経由する試験を含む、根拠付きの候補のJSONを取得する。
   `required=true`の直接importのテストを引数へ加え、残る候補は実際の利用関係から採否を決める。
   参照を含むファイル（`.json`・`.md`・`.py`）か参照検査の定義を変えた時は、参照検査`agent_toolkit_reference_invariant_test.py`が`reference_contracts`として必須集合に入り、曖昧候補の判定の対象から外れる。
   役割文書の入力・出力の同期元を変えた時は、`cross_contracts`が選んだ`agent-toolkit/task_documents_invariant_test.py`も実行する。
   元のJSONを保持し、曖昧な候補ごとに`source`・`test`・`include`（真偽値）・`reason`（空でない文字列）を持つ`decisions`配列を足して検証の証拠へ保存する（手順7の`--consumer-review`へ渡す判定JSON）。
   候補に出ない不変条件試験、動的な呼び出し、設定の移行、通知の旧期待値、CLI全体の一覧は、[verification.md](verification.md#変更範囲の検証の値)「変更範囲の検証の値」の探索で補う。
5. 失敗の確かめ直しには次の形を使う。
   - 失敗したテストだけは`--only-failed --from-run <初回のrun_id>`で実行する。
     `--from-run`を省いた`--only-failed`は他の作業ツリーを含むホスト全体の直近の実行を参照し、それに失敗が無ければ何も実行せずに終了0となり、自分の失敗が残っていても成功として報告される。
   - 単一のテストは、対象ファイルと必要なら`-k`を渡した直接pytestで確かめ、収集件数が想定と合うかを見る。
   - 直接pytestは、デバッガー、最小再現、環境の切り分けに使える。
     `-o`・`-p`はpytest固有で、`pyfltr run`へ渡すと終了2になる。
     `-o addopts=''`で設定全体を外す場合は`-p no:cacheprovider`も付ける。
     並列化だけを止める場合は`-n 0`を使い、`-p no:xdist`は既存の`-n 4 --dist=worksteal`が認識されずに収集前に終了4となる。
   - 回収予定のレーンで出力を残す場合は、対象ディレクトリが無い状態で `uv run --frozen python scripts/capture_lane_generated.py <hypothesis|statusline|claude-types>` を使う。
     順にpytest、固定のCargo試験、Claude Codeの同梱型の複製を隔離領域で行い、生成した内容を確かめられる記録を作業ツリーへ一緒に置く。
     `hypothesis` は後続の引数をpytestへ渡す。
     既存の出力へ記録を後付けせず、直接起動の出力や生成後に編集したものは保持する。
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
   - 合成版の検証では、最新の統合先をmergeした後、同じ統合先の完全OIDを候補列挙、`--check-argv`、`--verify-run`の`--against`へ渡す。
     合成後のファイルに対して、候補の`cross_contracts`にある必須試験を実行する。
     統合先が進んだ場合は新しい組で対象を確かめ、入力が変わった証拠だけを取り直す。
     `atk lane integrate`は`pyproject.toml`の`[tool.agent-toolkit.lane-integration]`から必須の横断試験の検証を呼ぶ。
     合成候補で必要な試験の成功を再利用できなければ該当試験を実行し、失敗ならfast-forwardしない。
     この自動検証はmanifestの変更範囲検証と最終runの受理を置き換えない。
     担当数にかかわらず、同じ公開候補に複数の群を合わせた場合は合成版として扱う。
     初めの群を取り込む前の統合先の完全OIDを`--against`へ渡し、最終候補の完全HEAD OIDと組にする。
     実行の前後に`git status --porcelain`で作業ツリーとindexがcleanであることを確かめ、HEADと基点の組が同じことを照合する。
     同じ組を成功したrun ID、`--verify-run`の結果、公開記録に対応付ける。
   - indexと作業ツリーを最終の統合入力のHEADへ一致させる。
     fast-forwardの直前に`uv run --frozen python scripts/pyfltr_precommit.py --verify-run <run_id> --consumer-review <判定JSON>`の終了0を確かめる。
     `--check-argv`の終了0は実際の実行の成功、版、cwd、HEADとindex、実行後の不変を確かめないため、verify-runは省かない。
   - 統合後・公開前に足した是正（統合時の検証失敗、CI失敗、案内文や構成の追随など）では、変えた文言・ジョブ名・設定キーの旧値を固定文字列で探し、それを期待するテスト（`ci_workflow*_test.py`、`<hook名>_test.py`など）を是正後のHEADで実行してから公開する。
   - 最終の検証が失敗したらfast-forwardせず、是正、必要な再レビュー、統合の再開へ戻る。
   - 検証がプロジェクトのmiseツールを使う場合は、追随後のレーンworktreeで `uv run --frozen python scripts/prepare_verification_tools.py --tool <miseの設定名> <実行名> -- pyfltr run ...` を実行する。
     選択済みの検証が使うツールだけを、1つのツールにつき1つの `--tool` で渡す。
     `mise which` と導入状況を調べ、不足した設定名に限って `MISE_LOCKED=1 MISE_LOCKED_SCOPES=project mise install <不足した設定名...>` で用意する。
     導入済みなら再導入せず、候補の選んだ実行ファイルを同じPATHから `--version` で起動してから検証する。
     用意や起動に失敗した場合はcwd・ツール・診断を返し、検証や記録の受理へ進まない。
     `make sync` はPython環境の同期だけを行い、仮想環境の `prek` を使う試験にmiseの `prek` を追加しない。
   - manifestへ渡す場合は、追随後のレーンworktreeでの `make sync`、実行の成功、run_idの取得、同じ入力のverify-runの成功を、失敗が伝わる1操作にまとめる。
     次のスクリプトを自分のmanaged-tempへ保存し、manifestのverificationに次の配列を1項目として渡す。

     ```json
     ["bash", "<スクリプト>", "<ログprefix>", "<判定JSON>", "refs/heads/<統合先branch>", "--tool", "shellcheck", "shellcheck", "--", "--no-fix", "--no-always-targets", "--commands=<全チェック>", "<対象パス>"]
     ```

     山括弧は実在の絶対パスと値へ置き換え、複数の対象パスは別々の要素にする。
     判定JSONが不要な場合は、その入力と`--consumer-review`を省いたスクリプトを使う。
     `--` の前に実際に選んだツールを渡す。
     例の `shellcheck` は選定した場合だけ指定し、mise管理ツールがなければ `--tool` の指定を省く。
     manifestを渡す前の照会で代用せず、追随後のlockと選んだPATHで準備して同じ候補を検証する。

     ```bash
     #!/usr/bin/env bash
     set -euo pipefail
     log_prefix=$1
     consumer_review=$2
     integration_base=$(git rev-parse --verify "$3^{commit}")
     shift 3
     mise_tools=()
     while [[ $# -ge 3 && $1 == --tool ]]; do
         mise_tools+=("$1" "$2" "$3")
         shift 3
     done
     [[ ${1-} == -- ]] || { echo "検証引数の前に -- が必要" >&2; exit 2; }
     shift
     make sync
     uv run --frozen python scripts/prepare_verification_tools.py "${mise_tools[@]}" -- \
         pyfltr run --output-format=jsonl "$@" > "$log_prefix.out" 2> "$log_prefix.err" || {
         code=$?
         cat "$log_prefix.err" >&2
         exit "$code"
     }
     run_id=$(jq -er 'select(.kind == "header") | .run_id' "$log_prefix.out")
     uv run --frozen python scripts/pyfltr_precommit.py --verify-run "$run_id" --consumer-review "$consumer_review" --against "$integration_base"
     ```

     verificationを空にする場合は、受理まで終えた同じ入力の両出力と終了状態を既存の記録へ対応付ける。
8. CIが無いか公開先でCIが実行されず、ローカルの全体pytestが必要な場合は`--maxfail=10`を付け、計画からmanifestまで同じ値を保つ。
   直接pytestでは`--maxfail=10`、pyfltr経由では`--pytest-args='--maxfail=10'`とし、他のpytestの追加引数は同じ値へ併記する。
   少数の独立した失敗を1回で集めつつ、共通の準備の不具合で全テストが失敗した時の出力を有限に抑えるためである（失敗が10件に達すると打ち切られ、未実行のテストが残ることがある。xdistの実行中のテストにより最終の失敗数は10件を超えうる）。
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

- 計上するのは、setup・call・teardownのテスト実行thread、そのテストが起動したThreadの処理、およびThreadPoolExecutorへ投入した処理（既存poolへの投入を含む）のCPUである。
  他のテストやセッション基盤のthread、子プロセスのCPU、待機の実時間は数えない。
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

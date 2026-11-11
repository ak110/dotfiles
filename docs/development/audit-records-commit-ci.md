# 監査記録: CIの状態とログ

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/commit/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月1日

2026年10月1日、LinuxのGitHub CLI 2.101.0で`ak110/dotfiles`を対象に確認した。
run `36791008397`が進行中でjob `110143643856`が`completed/success`のとき、job logs APIを`--allow-escape-sequences`付きで取得すると終了コード0で454行の全jobログを保存できた。
保存直後もrunは`in_progress`で、同runとjobを指定した`gh run view --log-failed`は終了コード1となり、進行中のため取得できない旨を返した。
終了済みの失敗run `36786321272`とjob `110128462391`への`gh run view --log-failed`は終了コード0で保存できた。
確認したAPIの成立範囲は進行中runの完了済みjobであり、進行中jobの取得可否は未確認である。

再検証では`gh version`と両コマンドのヘルプを取得する。
この節の再検証コマンドでは`<OWNER>/<REPO>`を`ak110/dotfiles`とする。
進行中runに完了jobがある場合はrunとjobの状態をAPIで保存する。
`gh api repos/<OWNER>/<REPO>/actions/jobs/<job ID>/logs --allow-escape-sequences`で全jobログを取得する。
同じ対象へ`gh run view <run ID> --repo <OWNER>/<REPO> --job <job ID> --log-failed`を実行し、出力と終了コードを比較する。
取得後もrunが進行中であることを確認し、終了済みの失敗runとjobにも後者を適用する。
APIは全jobログ、`--log-failed`は失敗ステップを返すため、出力件数の一致は求めない。
進行中runが無い場合はその条件を未観測とし、新しいCIの起動や事象の発生待ちは行わない。

## agent-toolkit/skills/commit/references/ci-failure-handling.md：GitHubの状態別ログ取得：2026年10月24日

2026年10月24日、LinuxのGitHub CLI 2.102.0で`ak110/dotfiles`の終了済み失敗run `39987263957`とjob `120529624126`（pytest (3.15)）を対象に確認した。
job logs APIを`--allow-escape-sequences`付きで取得すると終了コード0で29773行・6090534bytesを保存でき、最長行は600188bytes、外側の時間上限による終了の表示を含んだ。
同じrunとjobへの`gh run view --log-failed`も終了コード0・標準エラー0bytesだったが、17833行・3430008bytesで、試験の進捗44%の行で終わり、時間上限の表示を含まなかった。
APIの原本では、その次の行が600KB前後の試験名を持つ行だった。
GitHub CLIの[該当source](https://github.com/cli/cli/blob/v2.102.0/pkg/cmd/run/view/view.go#L549)は既定の`bufio.Scanner`で行を読み、走査後の`Err()`を返していない。
[`bufio.Scanner`の既定の上限](https://pkg.go.dev/bufio#MaxScanTokenSize)は64KiBである。

再検証では`gh version`を取得し、64KiBを超える行を含む失敗jobへ両コマンドを適用して、終了コード・行数・bytes・末尾の行を比較する。
該当する失敗jobが無い場合は条件を未観測とし、新しいCIの起動や事象の発生待ちは行わない。

## agent-basics/skills/bugfix/references/ci-failure-handling.md：分類判断のための詳細検討：2026年10月7日

2026年10月7日、GitLabのリポジトリ（Runnerはgitlab-runner 18.2.1のDocker executor）のAWI処理回で、許容された終了コード4で終わるはずのjobがjobとして終了コード1を報告した事象を再現した。
手段は次のとおりである。
GitLabのjob取得とjob更新の要求に応える最小のHTTPサーバー（偽のjob API）を、コンテナーから到達できるアドレスで起動した。
`gitlab/gitlab-runner:v18.2.1`の`run-single --executor docker`（helper `x86_64-v18.2.1`）でjobを1件ずつ実行し、Runnerがjob APIへ送った`exit_code`を報告値として読んだ。
bashのイメージにはCIのjobと同じdigestのPythonイメージ（bash 5.2.37）、shのイメージには`docker:27-cli`を使った。
bashのイメージで外部コマンドが4で終わると、`after_script`の有無にかかわらず1を報告した。
組み込みの`exit 4`は4を報告した。
shのイメージでは外部コマンドの4を4と報告した。
変数`FF_USE_NEW_BASH_EVAL_STRATEGY=true`を与えると、bashのイメージでも外部コマンドの4と5をそのまま報告した。
生成形の典拠はgitlab-runner v18.2.1の`shells/bash.go`の`BashWriter.Finish`で、同フラグが偽なら`: | eval <script>`、真なら`: | (eval <script>)`を生成する。
スクリプトとuvの層だけを含むローカル再現ではこの報告値の差は現れず、Runnerと実行器を含めた再現で初めて直接的原因を対照で確定できた。

再検証では同じ手段で、bashのイメージで`python -c 'import sys; sys.exit(4)'`だけをscriptに持つjobを、同フラグの指定なしと`true`の2条件で実行し、前者が1、後者が4を報告することを確かめる。
Runnerの版を変えた場合は、その版の`shells/bash.go`で生成形を確かめてから同じ2条件を実行する。

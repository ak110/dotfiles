---
name: commit
user-invocable: false
description: >
  commitを作成・取り込む操作（通常commit、amend、fixup、cherry-pick、merge、rebase、revertなど）に着手する直前、
  push、CI確認、公開工程、リリースに着手する前、
  既存未コミット差分を不採用として破棄する前、
  またはコミットメッセージ案（計画ファイル・PR説明など）を書く時点で起動する。
# 編集時の注意点:
# コミット境界判断のSSOTは本ファイル「通常commit」節。
# 本文中の判断分岐・規範削除・機能的な規範変更はchoreへ分類しない。
---

# コミット運用とコミットメッセージ

履歴変更と公開にはGit、対象リポジトリ、公開先を操作できるCLIが必要である。Gitを操作できない環境では差分からメッセージ案と確認条件を返し、commit・push・CI監視はClaude CodeやCodexの機能で行う。案の作成は[文章の品質基準](references/quality-standards.md)に従う。

本書はgit commitの運用とメッセージを定める。識別子確認・履歴操作・公開の前に`references/git-identifier.md`を読む。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したら判断や操作の前に指定節と、その成立に必要な前提・例外を自分で読む。全体が判断に必要な場合は全文を読む。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:writing-standards/references/investigation.md#「根拠と主張の一致」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/investigation.md#「根拠と主張の一致」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:commit/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/references/analysis-records.md#「暫定回避と結果報告」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/analysis-records.md#「暫定回避と結果報告」` |
| 保存済みの検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope.md#「実行条件と再実行」` | 判断 | `section:agent-basics:*/skills/check-execution/references/verification-scope.md#「実行条件と再実行」` |
| 未コミット検証の実行前とcommit後の適用確認前 | `agent-toolkit:/share/verification-evidence.md`の「検証時本文の保存」 | 判断 | `section:agent-toolkit:*/share/verification-evidence.md#「検証時本文の保存」` |
| 取得版の異なる結果をレビューへ渡す前 | `agent-toolkit:/share/exec-review-recording.md#「根拠の書き方」` | 判断 | `section:agent-toolkit:*/share/exec-review-recording.md#「根拠の書き方」` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| Git識別子・worktree・履歴・公開済みの状態を判定または操作する前 | `references/git-identifier.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/git-identifier.md` | |
| 公開工程またはリリース操作に着手する前 | `references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `references/history-rewrite.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite.md` | |
| 実際にpushする前 | `references/push-and-ci.md` | 操作 | `bash:git push` | |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `references/message.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/message.md` | |
| 既存未コミット差分を不採用として破棄する前 | `references/stash-cleanup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/stash-cleanup.md` | |
| 一時ブランチ、`git stash`の退避、hookの退避ファイルまたは別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `references/stash-cleanup.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/stash-cleanup.md` | |

## 検証

**1.** 検証の重複を避けるため、初回の検証を選ぶ際に、コミットフックが実行するチェックと対象集合も含めて比較する。

フックの保存出力で計画の検証を省略・置換するのは、実行したツールごとの名前・対象ファイル・終了状態を観測し、その範囲が計画のコマンドの分を含むと確かめられた場合に限る。
フック単位の`pyfltr....Passed`などの集約表示や「lint」という種別名だけでは、この包含を判定できない。
実装担当とレビュー担当が不足分を判断できるよう、計画のツール集合、フックの実行ツール集合、対象ファイルの対応、不足分を検証記録へ残す。
同じ条件の検証はその保存出力を用い、フックの前後に重ねない。不足するツールだけを別に実行し、フックによる更新があれば影響する成功結果を再取得する。
このまとめ方でもコミットフックを実行し、終了状態・診断・個別結果と最終内容の対応を確認する。

pyfltrを使うプロジェクトでは、`pyfltr list-runs`の各runの`commands`と`files`（対象件数）、`pyfltr show-run <run_id>`のツール別結果を確認手段にできる。
対象件数だけでは対象ファイルの包含を証明できないため、実行時の対象指定と結果も対応付ける。

**2.** 保存結果の再利用は、`agent-basics:check-execution/references/verification-scope.md`「実行条件と再実行」に沿って判断する。取得版の対応は、`agent-toolkit:/share/exec-review-recording.md`「根拠の書き方」に従う。確定後も有効な成功結果を活かし、影響する変更と未検証項目だけを追加検証する。

未コミットで後続へ渡す検証の前に、`agent-toolkit:/share/verification-evidence.md`「検証時本文の保存」を適用する。
commitやフックの後は、その保存本文と最終内容を比較する。cleanな版のOIDと適格な保存結果で対応を確認できる場合は、本文の重複保存や一律の全体再検証を要求しない。

**3.** 開発中は必要に応じて動作確認する。レビューに提出する最終結果は、実装・テスト・入力と、すべてのコミットやコミットフックによる更新を終えた内容へ適用できることを確認する。レビューへ進む際は、計画全体の変更範囲を網羅した検証結果と、それを取得した時点のバージョンを対応付ける。

## 通常commit

**4.** 安全にレビュー・revertできる単位を保つため、変更目的、依存関係、レビュー・revertのしやすさで分割を決める。計画にcommit単位があれば基準にする。各コミットの中間状態（中間`HEAD`）でも、その時点の公開契約とテストを満たすように整える（努力目標。git bisectやrevertで各コミットを単独で安全に扱えるようにするため）。

付帯作業を関連する開発のコミットに含める場合も、その開発作業と同じ変更目的として扱う。

**5.** commit権限はメインが持つ。サブエージェントは明示的な許可か`exec.subagent.md`の正規手順がある場合に限りcommitする。`git push`は委譲先への明示指示がある場合を除き、委譲元が行う。

**6.** WI実装はcommit前に「WI実装commitの対応」の手順1を行う。他セッションの成果を守るため、自身の担当範囲だけをステージングし、ユーザーや他セッションの未コミット変更を残す。commit直前に開始時からベースcommitが変わっていれば、`agent-toolkit/share/rules/common/02-agent-operations.md`「作業中に観測したリポジトリ状態変化の扱い」に従う。

**7.** 保存対象を確かめるため、commit前に対象変更だけのステージングとformat・lint・testの警告なし成功を確認する。マージの競合を解決したあとは、競合マーカー（`git grep -nE '^(<{7}|={7}|>{7})( |$)' -- .`）が残っていないことを確認する。プロジェクトの規範でCIを主要な検証関門と定めている場合は、CIが無条件に実行するチェックをローカルの事前確認から省いてよい。

保存する内容は、`agent-basics:writing-standards/references/investigation.md`「根拠と主張の一致」に従い、要求・計画から今回保存すべき担当変更を先に確定する。
その集合とステージ済み実差分を比べ、担当外の混入と担当変更の欠落を判定する。
担当外の未ステージ差分を保護し、不足する担当差分だけを追加して再比較する。
status・diffの表示やhook成功の確認とは別に、保存対象とステージ済み実差分を比べる。
解除条件付きの暫定対応を含むコミットでは、`agent-toolkit:wi-standards/references/analysis-records.md`「暫定回避と結果報告」に従い、保存済みAWIに正式な対応内容、解除条件、復帰後の期待結果、および待機期限が記録されているかを確認する。不足がある場合は、通常のWI登録手順でそれらを記録してからコミットする。

**8.** フック（Git hookやhook管理ツール）のチェック迂回は、pre-commitの退避（stash）が競合し、対象ファイルが所定の手順で検証済みの場合に限り認める。

該当する操作には、`--no-verify`、`-n`、`-c core.hooksPath=/dev/null`などのフック無効化オプションや、環境変数`SKIP=<hook名>`の指定が含まれる。通常の検証を省略したり、フックの失敗を安易に回避したりする目的では使用しない。ただし、プロジェクト規範で個別に許可されている`SKIP=<hook名>`はその定めに従う。フックを迂回した場合は、その手段と理由をコミット完了後の報告に明記する。

**9.** 「hookの打ち切りと退避の復元」に従ってcommitを実行し、成功後は「検証」の最終内容への適用を確認し、WI実装は対応を記録する。push後のCI失敗は`agent-basics:bugfix`を起動し、同スキルの`references/ci-failure-handling.md`で原因を分析する。

### hookの打ち切りと退避の復元

1. POSIXのホストでcommit hookを伴う`git commit`を実行するときは、`timeout -s INT <秒> git commit ...`でcommit自身の時間上限を設け、ホストのコマンド時間上限（Claude Codeの`Bash`の`timeout`など）をその秒数より長くする。
   hook管理ツールのprekはhookの実行中に未ステージの変更を一時ファイルへ退避し、通常終了とSIGINTでは作業ツリーへ戻すが、SIGTERM・SIGKILLやホストの時間上限による強制終了では戻さない（prek 0.5.7で確認）。
   上限に達したcommitは退避を戻して終了コード124で失敗するため、hookが長引いた原因（検証の実行枠の待機など）を確かめてからやり直す。
   Windowsのホストは同等の手段の挙動を確認していないため、この手順の対象外とする。

2. commitが失敗・中断した出力に`Unstaged changes detected. Temporarily saving them to <パス>`がある場合は、他の操作より先に退避が作業ツリーへ戻ったかを確かめる。
   SIGINTの打ち切りではcommitの終了後にprekが退避を戻すことがあり、`Restored unstaged changes from <パス>`の行の有無だけでは判定できない。
   prekのプロセスが終了してから、作業ツリーと退避ファイル（`~/.cache/prek/patches/`、`PREK_HOME`の指定があればその配下）を比べる。
   比較には`git diff --binary --src-prefix=a/ --dst-prefix=b/`の出力と、退避ファイルから先頭の`#`で始まる注記行を除いた本文を使う（`diff.noprefix`の設定によらず同じ形式で比べるため）。
   一致しなければ退避は戻っておらず、`git apply <退避ファイル>`で復元し、同じ比較で一致することを確かめる。
   復元後の退避ファイルは`references/stash-cleanup.md`のhookの退避ファイルの項に従って扱う。

## WI実装commitの対応

WI実装commitとAWIの対応は、計画（計画なしなら引き継ぎ記録）と同じディレクトリ・ファイル幹（stem）の`<stem>.wi-commits.jsonl`にのみ構造化して記録する。書込には`atk run-script plan-progress`と`atk run-script plan-rewrite`、読取には`atk run-script plan-commits`を使う。commitは一意な短縮OIDで扱い、入力には完全OIDも使える。各工程の文書には操作時機、記録・worktree、固有入力だけを書き、詳細は本節を参照する。オプションは`--help`で確認し、計画・引き継ぎ記録・worktreeは絶対パス、AWIはファイル名で渡す。

**1.** AWIとの対応を保持するため、実装コミットを作成する前に、対応するAWIの集合と現在のHEADを取得する。`git commit`を単独で実行して正常に完了した場合に限り、作成前のHEAD、新しいHEAD、worktree、および対応するすべてのAWIを`atk run-script plan-progress`へ渡して記録する。引き継ぎ記録は`--handoff`と対象AWI全件の`--allowed-awi`を指定する。レビュー修正やCI修正も同様に全件記録する。cherry-pickやfast-forwardで複数commitを取り込んだ場合は操作前後のHEADで一括記録できる。AWI集合が異なれば集合ごとの区間直前を作成前HEAD、区間末尾を新HEADとして記録する。区間末尾は現在HEADのfirst-parent履歴にあればよい。マージコミットや記録済みのコミットが含まれる範囲は一括記録できないため、マージは使わずにコミットを取り込み、未記録の区間のみを渡す。

**2.** WI終了時の記録を正しくするため、AWIを完了状態にするエージェントは、以下のコマンドを実行し、出力されるJSON Linesの`awi`および`commits`から、最新のコミット一覧を短縮OIDで取得する。

```sh
atk run-script plan-commits -- <計画Markdownの絶対パス> --worktree <対象worktreeの絶対パス>
atk run-script plan-commits -- <引き継ぎ記録Markdownの絶対パス> --worktree <対象worktreeの絶対パス> --handoff --allowed-awi <記録の対象AWI全件を反復指定>
```

対象を絞る場合は`--awi <AWIファイル名>`を反復指定する。省略時は計画の関連WI全件（引き継ぎ記録は`--allowed-awi`の全件）を取得する。同stemのJSONLは保存先であり位置引数に渡さない。保存で計画のパスが移動しても、同名の保存済み計画が一意ならそのまま指定できる。同名が複数あれば保存先の実在パスを指定する。取得した短縮OIDは`atk wi adopt --commit`に渡し、複数のコミットがある場合は`--commit <OID>`を繰り返し指定する。コミットの説明などのメモは`--note-file`に記録する。実装差分を伴わない項目に対応記録がない場合は、その項目を除外した対象を`--awi`で指定する。実装コミットを持つはずの項目で記録が欠落している場合、対象外のAWIが含まれる場合、あるいはOIDを解決できない場合は、記録の生成側で内容を補正してから再取得する。
`atk lane integrate`へ渡すadoptの`--commit`も現在の取得結果から作り直し、manifestのWI行の`commit_record`へ元の計画または引き継ぎ記録Markdownの絶対パスを指定する。CLIは統合直前に祖先関係と対象AWIのcommit集合の一致を確認する。
記録済みのコミットへAWIを追加するには、旧OIDを同じOIDへ写す対応表`{"<旧OID>": [{"commit": "<同じOID>", "awi": [<記録後の全AWI>]}]}`を`atk run-script plan-progress`の`--rewrite-map`へ渡す。
`awi`には記録後に対応付けるAWIを全件書き、計画の関連WI（`--handoff`では`--allowed-awi`）に無いAWIは記録を変えずに拒否される。メッセージの説明文や件名から対応を推測しない。

**3.** 履歴変更後の対応を継承するため、履歴の書き換えやコミットの分割によってWI実装コミットのOIDが変化した場合は、`references/history-rewrite.md`「WI実装commitの対応の継承」に従う。

検収済みの対応表と書き換え前のHEADを、変更範囲に含まれるWI実装コミットを持つすべての計画および引き継ぎ記録とともに、1回の`atk run-script plan-rewrite`呼び出しで渡す。必要な情報に不足がある場合、同コマンドはいずれの記録も変更せずに失敗する。
同コマンドは、対応記録に含まれる完了済みAWIの対応コミットや件名も、新しいコミットに合わせて更新する。private-notesの保存に失敗した場合は、原因となるエラーを解消したうえで同じ引数で再実行する。
指定が漏れている記録がある場合、コマンドは書き込み前に該当する記録を一覧で提示するため、それらを引数に追加して再実行する。複数の対応表を扱う場合は、書き換えの順序に従って`--rewrite-map`を繰り返し指定し、合成後のOIDが現在のHEADに含まれていることを確認する。

**4.** 公開情報と内部記録を分けて保存する。実装差分なしで充足した項目、WIと無関係なcommit、回答のみのUWIは対応記録の対象外とし、進捗ログや引き継ぎ記録を根拠にcommit指定を省く。公開するメッセージにはWI識別子や内部の認可情報を記載せず、対応記録へ残す。計画と対応記録を`atk plans commit`で保存する。

## コミットメッセージ

件名のフォーマット、type、description、本文、および`Co-Authored-By:`の書式は`references/message.md`に従う。計画ファイルやプルリクエストの説明文に記載するコミットメッセージ案にも、同じ基準を適用する。

## 工程で参照する運用資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| CI失敗を扱う前 | `agent-toolkit:commit/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md` | |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `agent-toolkit:commit/references/design-time.md#「破壊的変更の記録」` | 判断 | `section:agent-toolkit:*/skills/commit/references/design-time.md#「破壊的変更の記録」` | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `agent-toolkit:commit/references/testing.md#「分岐条件の効果の裏付け」` | 判断 | `section:agent-toolkit:*/skills/commit/references/testing.md#「分岐条件の効果の裏付け」` | |

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |

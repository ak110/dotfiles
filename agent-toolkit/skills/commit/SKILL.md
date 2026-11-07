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

履歴の変更や公開をするには、Git、対象リポジトリ、および公開先を操作できるCLIが必要である。Gitを直接操作できない環境では、提示された差分をもとにメッセージ案と確認条件を返し、実際のcommit・push・CI監視はClaude CodeやCodexの機能で行う。メッセージ案を作成する際は[文章の品質基準](references/quality-standards.md)に従う。

本書は、git commitの運用ルールとコミットメッセージの規約を定める。識別子の確認、履歴の操作、公開の作業に着手する前に、`references/git-identifier.md`を確認する。

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

push後にCIが失敗した場合は`agent-basics:bugfix`を起動し、同スキルの`references/ci-failure-handling.md`に沿って原因を分析する。

## 検証

開発中の動作確認は必要に応じて適宜行う。レビューに提出する最終結果は、実装・テスト・入力と、すべてのコミットやコミットフックによる更新を終えた内容へ適用できることを確認する。レビューへ進む際は、計画全体の変更範囲を網羅した検証結果と、それを取得した時点のバージョンを対応付ける。

初回の検証を選ぶ際に、コミットフックが実行するチェックと対象集合も含めて比較する。
同じ条件の検証をフックが実行する場合は、その保存出力を変更範囲の検証にも用い、フックの前後に同じ検証を重ねない。
フックで不足する検証だけを追加し、フックによる更新があれば影響する成功結果を再取得する。
このまとめ方でもコミットフックを実行し、終了状態・診断・個別結果と最終内容の対応を確認する。

以前に保存した検証結果を再利用できるかどうかは、`agent-basics:check-execution/references/verification-scope.md`「実行条件と再実行」に沿って判断する。取得したバージョンの対応付けは、`agent-toolkit:/share/exec-review-recording.md`「根拠の書き方」に従う。内容が確定したあとも有効な成功結果はそのまま活かし、結果に影響を与える変更点や未検証の項目だけを追加で検証する。
未コミットの状態で後続へ渡す検証を実行する前には、`agent-toolkit:/share/verification-evidence.md`「検証時本文の保存」を適用する。
commitやフックの後は、その保存本文と最終内容を比較する。cleanな版のOIDと適格な保存結果で対応を確認できる場合は、本文の重複保存や一律の全体再検証を要求しない。

## 通常commit

コミットの分割単位は、変更の目的、依存関係、レビューやrevertのしやすさを考慮して決める。作業計画に想定されるコミット単位が記載されていれば、それを基準にする。各コミットの中間状態（中間`HEAD`）でも、その時点の公開契約とテストを満たすように整える（努力目標。git bisectやrevertで各コミットを単独で安全に扱えるようにするため）。
`agent-toolkit/rules/01-agent.md`に定める付帯作業を関連する開発のコミットに含める場合も、その開発作業と同じ変更目的として扱う。

コミットの実行権限はメインエージェントが持つ。サブエージェントは、明示的に許可された場合や`exec.subagent.md`に定める正規の手順を踏む場合に限りコミットできる。また、`git push`は委譲先に対して明示的に指示された場合を除き、常に委譲元が実行する。

他のセッションが同じリポジトリに変更を加える可能性があるため、ステージングするのは自セッションの担当範囲だけに限定し、ユーザーや他セッションによる未コミットの変更はそのまま残す。コミットの直前に、作業開始時からベースコミットが変わっていた場合の対応は、`agent-toolkit/rules/02-agent-operations.md`「作業中に観測したリポジトリ状態変化の扱い」に従って判断する。

コミットを実行する前に、対象の変更だけがステージングされていること、format・lint・testが警告なしで成功することを確認する。マージの競合を解決したあとは、競合マーカー（`git grep -nE '^(<{7}|={7}|>{7})( |$)' -- .`）が残っていないことを確認する。プロジェクトの規範でCIを主要な検証関門と定めている場合は、CIが無条件に実行するチェックをローカルの事前確認から省いてよい。
保存する内容は、`agent-basics:writing-standards/references/investigation.md`「根拠と主張の一致」に従い、要求・計画から今回保存すべき担当変更を先に確定する。
その集合とステージ済みの実差分を比べ、担当外の混入と担当変更の欠落を両方向で判定する。
担当外の未ステージ差分を保護し、不足があれば担当変更に必要な差分だけを追加してから、ステージ済みの実差分を再び比較する。
statusやdiffの表示とcommit hookの成功を確認しても、保存すべき担当変更とステージ済みの実差分の比較は別に行う。
解除条件付きの暫定対応を含むコミットでは、`agent-toolkit:wi-standards/references/analysis-records.md`「暫定回避と結果報告」に従い、保存済みAWIに正式な対応内容、解除条件、復帰後の期待結果、および待機期限が記録されているかを確認する。不足がある場合は、通常のWI登録手順でそれらを記録してからコミットする。

コミットフック（Git hookやhook管理ツール）のチェックをスキップまたは迂回する操作は、pre-commitの退避（stash）が競合を起こし、かつ対象ファイルを所定の手順ですでに検証済みである場合にのみ認める。
該当する操作には、`--no-verify`、`-n`、`-c core.hooksPath=/dev/null`などのフック無効化オプションや、環境変数`SKIP=<hook名>`の指定が含まれる。通常の検証を省略したり、フックの失敗を安易に回避したりする目的では使用しない。ただし、プロジェクト規範で個別に許可されている`SKIP=<hook名>`はその定めに従う。フックを迂回した場合は、その手段と理由をコミット完了後の報告に明記する。

### hookの打ち切りと退避の復元

POSIXのホストでcommit hookを伴う`git commit`を実行するときは、`timeout -s INT <秒> git commit ...`でcommit自身の時間上限を設け、ホストのコマンド時間上限（Claude Codeの`Bash`の`timeout`など）をその秒数より長くする。
hook管理ツールのprekはhookの実行中に未ステージの変更を一時ファイルへ退避し、通常終了とSIGINTでは作業ツリーへ戻すが、SIGTERM・SIGKILLやホストの時間上限による強制終了では戻さない（prek 0.5.7で確認）。
上限に達したcommitは退避を戻して終了コード124で失敗するため、hookが長引いた原因（検証の実行枠の待機など）を確かめてからやり直す。
Windowsのホストは同等の手段の挙動を確認していないため、この手順の対象外とする。

commitが失敗・中断した出力に`Unstaged changes detected. Temporarily saving them to <パス>`がある場合は、他の操作より先に退避が作業ツリーへ戻ったかを確かめる。
SIGINTの打ち切りではcommitの終了後にprekが退避を戻すことがあり、`Restored unstaged changes from <パス>`の行の有無だけでは判定できない。
prekのプロセスが終了してから、作業ツリーと退避ファイル（`~/.cache/prek/patches/`、`PREK_HOME`の指定があればその配下）を比べる。
比較には`git diff --binary --src-prefix=a/ --dst-prefix=b/`の出力と、退避ファイルから先頭の`#`で始まる注記行を除いた本文を使う（`diff.noprefix`の設定によらず同じ形式で比べるため）。
一致しなければ退避は戻っておらず、`git apply <退避ファイル>`で復元し、同じ比較で一致することを確かめる。
復元後の退避ファイルは`references/stash-cleanup.md`のhookの退避ファイルの項に従って扱う。

## WI実装commitの対応

WIに基づく実装コミットとAWIの対応関係は、計画ファイル（計画がない場合は引き継ぎ記録）と同じディレクトリかつ同じファイル幹（stem）を持つ対応記録ファイル`<stem>.wi-commits.jsonl`にのみ構造化して記録する。このファイルへの書き込みには`atk run-script plan-progress`と`atk run-script plan-rewrite`を用い、読み込みには`atk run-script plan-commits`を用いる。コミットは一意に特定できる長さの短縮OIDで扱い、入力値としては短縮OIDと完全OIDのどちらも指定できる。各作業工程の文書には、操作タイミング、利用する記録ファイルやworktree、および工程固有の入力のみを記載し、詳細な手順は本節を参照する。各コマンドのオプションの詳細は`--help`を確認し、計画ファイル・引き継ぎ記録・worktreeは絶対パスで、AWIはファイル名で渡す。

記録: 実装コミットを作成する前に、対応するAWIの集合と現在のHEADを取得する。`git commit`を単独で実行して正常に完了した場合に限り、作成前のHEAD、新しいHEAD、worktree、および対応するすべてのAWIを`atk run-script plan-progress`へ渡して記録する。引き継ぎ記録を用いる場合は`--handoff`を指定し、その記録が対象とするすべてのAWIを`--allowed-awi`で渡す。通常の実装コミットだけでなく、レビュー修正やCI修正のコミットもすべて同様に記録する。cherry-pickやfast-forwardによって1回の操作で複数のコミットを取り込んだ場合は、操作前後のHEADを指定して一括で記録できる。異なるAWI集合に対応するコミットを取り込んだときは、AWI集合ごとの区間直前のコミットを作成前HEAD、区間末尾のコミットを新HEADとして区間ごとに記録する。末尾のコミットは、現在のHEADのfirst-parent履歴上に存在していればよい。マージコミットや記録済みのコミットが含まれる範囲は一括記録できないため、マージは使わずにコミットを取り込み、未記録の区間のみを渡す。

取得: AWIを完了状態にするエージェントは、以下のコマンドを実行し、出力されるJSON Linesの`awi`および`commits`から、最新のコミット一覧を短縮OIDで取得する。

```sh
atk run-script plan-commits -- <計画Markdownの絶対パス> --worktree <対象worktreeの絶対パス>
atk run-script plan-commits -- <引き継ぎ記録Markdownの絶対パス> --worktree <対象worktreeの絶対パス> --handoff --allowed-awi <記録の対象AWI全件を反復指定>
```

取得対象を絞り込む場合は、`--awi <AWIファイル名>`を繰り返し指定する。省略した場合は、計画に含まれるすべての関連WI（引き継ぎ記録では`--allowed-awi`で指定した全件）が取得される。同じstemのJSONLファイルは保存先であり、コマンドの位置引数には渡さない。作業中に保存処理によって計画ファイルのパスが移動した場合でも、同名の保存済み計画が一意であればそのまま指定できる。同名の計画が複数存在するときは、保存先の実在パスを指定する。取得した短縮OIDは`atk wi adopt --commit`に渡し、複数のコミットがある場合は`--commit <OID>`を繰り返し指定する。コミットの説明などのメモは`--note-file`に記録する。実装差分を伴わない項目に対応記録がない場合は、その項目を除外した対象を`--awi`で指定する。実装コミットを持つはずの項目で記録が欠落している場合、対象外のAWIが含まれる場合、あるいはOIDを解決できない場合は、記録の生成側で内容を補正してから再取得する。
記録済みのコミットへAWIを追加するには、旧OIDを同じOIDへ写す対応表`{"<旧OID>": [{"commit": "<同じOID>", "awi": [<記録後の全AWI>]}]}`を`atk run-script plan-progress`の`--rewrite-map`へ渡す。
`awi`には記録後に対応付けるAWIを全件書き、計画の関連WI（`--handoff`では`--allowed-awi`）に無いAWIは記録を変えずに拒否される。コミットメッセージの説明文や件名から対応関係を推測しない。

履歴変更: 履歴の書き換えやコミットの分割によってWI実装コミットのOIDが変化した場合は、`references/history-rewrite.md`「WI実装commitの対応の継承」に従う。
検収済みの対応表と書き換え前のHEADを、変更範囲に含まれるWI実装コミットを持つすべての計画および引き継ぎ記録とともに、1回の`atk run-script plan-rewrite`呼び出しで渡す。必要な情報に不足がある場合、同コマンドはいずれの記録も変更せずに失敗する。
同コマンドは、対応記録に含まれる完了済みAWIの対応コミットや件名も、新しいコミットに合わせて更新する。private-notesの保存に失敗した場合は、原因となるエラーを解消したうえで同じ引数で再実行する。
指定が漏れている記録がある場合、コマンドは書き込み前に該当する記録を一覧で提示するため、それらを引数に追加して再実行する。複数の対応表を扱う場合は、書き換えの順序に従って`--rewrite-map`を繰り返し指定し、合成後のOIDが現在のHEADに含まれていることを確認する。

対象外: 実装差分を伴わずに充足された項目、WIと無関係なコミット、および回答のみを行うUWIは対応記録の対象外とする。これらは進捗ログや引き継ぎ記録の記述を根拠とし、コミットの指定は省略する。公開するコミットのメッセージにはWI識別子や内部の認可情報を記載せず、対応記録ファイル側に残す。作業計画は、対応記録ファイルとともに`atk plans commit`を用いて保存する。

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

---
verified-digest: 3c45fc4ccab3d7083f448e44a7a6c5fe3047948cda81ae2f007a4b741173d5b3
name: git-sync-interleavings
user-invocable: false
description: >
  private-notesのGit同期（`git_sync.py`、`wi/sync.py`、`wi/background_sync.py`）の
  ロック、pull、push、commit、分岐とrebaseの扱いを変える時と、それらへ触れた変更を検査せずに統合した後に、
  並行する複数の実行主体の全ての順序で更新が失われないかを確かめる時に起動する。
verifies:
  - agent-toolkit/agent_toolkit/_atk/git_sync.py
  - agent-toolkit/agent_toolkit/_atk/wi/sync.py
  - agent-toolkit/agent_toolkit/_atk/wi/background_sync.py
  - agent-toolkit/agent_toolkit/_atk/wi/background_sync_test.py
  - agent-toolkit/agent_toolkit/_atk/wi/mutations/transitions.py
  - agent-toolkit/agent_toolkit/_atk/wi/processing_owner.py
  - agent-toolkit/agent_toolkit/_git/command.py
  - agent-toolkit/agent_toolkit/_atk/git_sync_interleaving_test.py
---

# private-notesのGit同期の網羅検査

`agent-toolkit/agent_toolkit/_atk/git_sync_interleaving_test.py`は、実際の`wi/sync.py`・`git_sync.py`と実Gitで、並行する実行主体の操作の順序を全て列挙して性質を確かめる有界の網羅検査である。
スレッドの停止・再開と順序の列挙は `pytilpack.modelcheck.ThreadExplorer` が担い、この検査はGit操作の分類と性質の判定を渡す。
検証の作り方と追従の一般の手順は`agent-basics:formal-verification`にある。

## 実行

裏の同期の停止位置を固定した補助試験も行う。
この試験だけで網羅検査を代用しない。

```bash
uv run --frozen pytest -q -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/wi/background_sync_test.py
```

workerの最初の受信・送信を一時停止し、通信中に別の主体が作業ツリーのロックを取得できることを確かめる。
受信待ち中のローカルcommit、送信待ち中のローカルcommit、最初の受信後に別cloneが送る更新の有無を独立に振り、8通りすべてでローカルとremoteが最終的に一致し、作った更新が履歴または本文に残ることを確かめる。
同じinbox項目の着手を2つのcloneで同じ時刻に確定し、送信を競合させる試験では成功が1件だけであることを確かめる。
共有設定の初回作成が2つのcloneで競合する試験では、裏の同期が先着の共有設定と後発cloneの独立したWIを維持し、rebaseの途中状態を残さないことを確かめる。
この列挙は、指定した停止位置に各主体が1回だけ更新する場合に限る。
別の停止位置、継続的な書き込み、プロセス停止、認証失敗、Windowsでの子プロセス起動順は列挙しない。

```bash
cd <dotfilesのworktree> && AGENT_TOOLKIT_GIT_SYNC_INTERLEAVINGS=all uv run --frozen pytest -v -s -o addopts='' -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/git_sync_interleaving_test.py
```

- 値を`first`にすると、シナリオごとに、既知として登録していない性質の違反を初めて見つけた時点で止まる。
  既知かどうかは手順の列ではなく性質名で判定し、`separate-clones`の「性質1（操作の終了時）」の違反は数えて続ける。
  変数が無ければ全て`skip`になり、通常のテスト（`make test`、CI）はこの検査を省いて進む。
- 所要時間は4シナリオの合計で約13分（2026年11月19日、Linux、1つのプロセス）で、実Gitの境界を使うテストの改善判断値（2秒）を大きく超えるため通常のテストから外した。
  シナリオを2つのプロセスへ分けて同時に起動すると、実時間は約8分になる。
  共有の実行枠を長く占有しないよう`pytest_plugin`を読み込まず、テストの隔離は`agent-toolkit/conftest.py`が与える。
- 出力はシナリオごとに、順序の数、所要時間、内容が衝突した順序の数、違反した性質、終了状態の分布、性質ごとの最初の反例（主体ごとの手順の列）である。
  競合のシナリオで内容が衝突した順序が1つも無ければ、性質2を確かめていないためテストは失敗する。
- 担当引き継ぎのシナリオは、2つのcloneで`adopt`と`take-over`を各1回行う。
  先に引き継ぎが確定した順序では旧担当の採用を拒否し、先に採用が確定した順序では引き継ぎを拒否することを、remoteの本文と両方の終了結果で確かめる。

## 対応と前提

- 1手順は、`agent_toolkit._git.command._launch`を通る1回のGitの起動、`git_sync._RepoLock.acquire`の最初の取得、1つの項目の書き換えである。
- 止める手順は、remoteへの操作（`fetch`、`push`）、複数の主体が使うcloneのロックの取得、そのcloneへのロックの外の操作である。
  共有しないcloneへの操作と、ロックの保持者のそのcloneへの操作は止めずに続けて実行する。
  配置の照会（`rev-parse --git-common-dir`・`--git-path`）と、構成を変更しないシナリオでのupstreamの有無の照会も停止の対象外とする。
  ロックの外の操作は性質「共有cloneの操作はロックの内側」の違反として記録する。
- sleep setは別の資源への操作やremoteの読み取り同士の重複探索を省く。
  参照を変えなかった`push`は実行後に読み取りとして報告し、分岐点に戻るときの判定には実行前の操作種別を使う。
  等価と記された実行も最後まで進め、性質を判定して順序数に含める。
  workerを含む時は、remoteへの操作と共有cloneへの操作を独立とせずに列挙する。
  fetchが同期専用refへ書き、pushが通常の追跡refへ及ぶことがあるためである。
- 停止の上限を超えた主体の終了が確認できない場合は、主体名と手順の列を出力し、そのpytestプロセスで後続のシナリオを実行しない。
- 主体の更新は`wi/mutations/transitions.py`の状態遷移と同じ`Storage.mutation(push_pending=True)`と`Mutation.commit`で行う。
  主体Cは`atk`を使わない別ホストで、取得・commit・pushを1手順で行う。
  非同期のAは`wi_sync.local_save(True)`を使う。
  Wは同じcloneの`background_sync.run`を1回実行する。
  `atk serve`は`Operations`が`Storage(local_only=True)`で変更を確定し、定期同期も同じ`background_sync.run`へ進む。
  待ち時間の違いは範囲外なので、別の主体として重ねて数えない。
  明示同期も同じGit同期と排他の関数を使う。
- 対象の関数は次のとおりである。
  - `git_sync.py`の`repo_lock`、`pull`、`push_pending_commits`、`commit_and_push`、`_recover_matching_tree_divergence`、`_recover_redundant_divergence`、`ensure_not_rebasing`。
  - `wi/sync.py`の`Storage.mutation`、`pull`、`push_pending_commits`、`_redundant_terminal_divergence`。
  - `wi/background_sync.py`の`run`、同期専用refへの受信、排他中の統合、確定したOIDの送信。
- 前提: Gitの1回の起動は、他の主体の起動が割り込まずに終わる1手順で、pushは参照を1回で更新する。
  参照のロックの競合による失敗、通信・認証の失敗と時間切れ、ロックの待機上限、操作の途中のプロセスの停止は範囲外とする。
  更新回数の上限を超える継続的な書き込みと、Windowsでのプロセス起動の違いも範囲外とする。
  後続の同期は他の主体が動かない状態で1回だけ行う。

## 性質と害

| 性質 | 破れた時の害 |
| --- | --- |
| 受理した更新を失わない（commitした更新は、後続の同期の後もremoteか自分のcloneのmainにある） | WIの状態遷移や結果が黙って消える |
| 同期した操作が成功した更新はremoteにある。非同期操作の成功はローカルのcommitの受理を表す | 同期成功の報告を誤るか、ローカル保存が完了していない |
| 成功は受理を伴う、受理前の失敗は変更を残さない | 失敗した操作の書きかけが作業ツリーに残り、後の操作のcommitへ混ざる |
| 競合が無い操作は成功する（失敗は、他の主体が操作の間にremoteを変えた場合に限る） | 常に失敗する変更が、更新を失わない性質だけでは検出されない |
| 性質1（後続の同期の後）: 別々の項目の更新は、受理したものが次の同期で全てremoteに届く | 未pushのcommitが残り続ける |
| 性質1（操作の終了時）: 2つの更新と別ホストのcommitが、両方の操作の終了時に全てremoteにある。非同期の更新は終了時のremote到達を求めない | 更新が他のホストへ届くのが次の操作まで遅れるか、操作をやり直す必要がある |
| 性質2: rebaseの途中で止まったcloneでは、pullとpushが`RebaseInProgressError`で拒否し、HEADと本文を変えない。止まったことを操作が失敗として返す | 競合の解消前の上書きで、ローカルかremoteのcommitの内容が消える |
| 性質2（内容の衝突で止まる）: 2つの主体が同じ項目を互いの更新を見ずに書き換えた順序（両方の更新のcommitの親で項目が変更前の本文）では、後続の同期の後に、片方のcloneが競合を保った（`git ls-files --unmerged`に項目がある）rebaseの途中で止まる | 後から統合する側が競合を自動で解決し、先の更新をremoteの先端で黙って上書きする。先の更新は履歴に残るため、受理した更新を失わない性質では検出されない |

## シナリオと結果

2026年11月19日の結果（同期したcommitで実行）。
主体A・B・Dは`atk`の更新、Cは別ホストのcommitで、`x`・`y`・`z`はcloneの名前である。

| シナリオ（テストのID） | 主体と項目 | 順序 | 所要時間 | 結果 |
| --- | --- | ---: | ---: | --- |
| `separate-clones` | A@x項目1、B@y項目2、C@z項目3 | 403 | 337秒 | 性質1（操作の終了時）だけ98通りで違反（既知の反例）。他の性質は全て成立 |
| `shared-clone` | A@x項目1、B@x項目2、C@z項目3 | 17 | 8秒 | 全て成立 |
| `conflict` | A@x項目1、B@y項目1、C@z項目3 | 395 | 351秒 | 全て成立。内容が衝突した240通りの全てで競合を保って止まり、うち180通りは操作の終了時にrebaseの途中で、性質2の拒否を確かめた |
| `conflict-shared-clone` | A@x項目1、B@y項目1、D@x項目4 | 199 | 94秒 | 全て成立。内容が衝突した94通りの全てで競合を保って止まり、うち78通りは操作の終了時にrebaseの途中で、4通りでDが`RebaseInProgressError`で拒否された |

2026年11月25日に裏の同期を加えたシナリオを、Linuxで2プロセスに分けて全順序を実行した。
両方で性質は成立した。
2件の同時実行にかかった時間は225秒だった。

| シナリオ（テストのID） | 主体 | 各主体の操作の上限 |
| --- | --- | --- |
| `worker-external` | A@x項目1、W@x、C@z項目2 | AとCは更新各1回、Wは裏の同期を1回起動 |
| `worker-conflict` | A@x項目1、W@x、B@y項目1 | AとBは更新各1回、Wは裏の同期を1回起動 |

上限は3主体とする。
従来のシナリオでは各更新主体が1回ずつ更新する。
workerのシナリオでは2つの更新主体が各1回更新し、Wは受け取った更新を送受信する。
Wの起動1回の内側にある再試行は、製品コードの上限を使う。
既存の`git_sync_property_test.py`は1つのプロセスの操作列だけを扱い、並行する主体の順序はこの検査が初めて扱う。

## 既知の反例

性質1（操作の終了時）はWIの記述どおりの性質で、`test_separate_clones_publish_every_update_by_operation_end`が`xfail(strict=True)`で記録している。
どちらの反例も、操作は`CalledProcessError`で失敗を返し、受理した更新は失わない。
性質1（後続の同期の後）は成立する。

1. 受理前の失敗（30通り）: 状態遷移の`Storage.mutation(push_pending=True)`で、Bに未pushのcommitが無いのに失敗する。
   Bの`push_pending_commits`の`git push`がAのpushで拒否され、`git fetch`と`merge --ff-only @{u}`の後、再送の`git push`の直前にCがpushする。
   再送が拒否されて例外が`_push_pending_commits_impl`の外へ出て、Bは項目を書き換える前に終わる。
   送るcommitが無い操作が、別ホストのpushとの競合だけで失敗する（修正するかは未決定。例えば未pushが0件なら送信を省く）。
2. 受理後の未push（68通り）: Bのcommitの`git push`がAのpushで拒否され、`git fetch`と`rebase @{u}`の後、再送の直前にCがpushする。
   再送も拒否され、Bのcommitは`y`のmainに残り、次の同期で送られる。
   `wi/sync.py`の`commit_and_push`は再試行を1回とし、再試行後の失敗を例外のまま送出する。

## 欠陥と検証の誤りの区別

反例の各行は、主体と、その主体が実行したGitのコマンドである。
各行を実際のコードの分岐で起こりうるかを確かめ、起こらない行があれば検査の枠組み（`_git_op`の分類、`_atk_update`の流れ、前提）の誤りとして直す。
全て起こりうれば欠陥として`agent-basics:bugfix`を起動する。
性質の判定は`_judge`と`_follow_up`にあり、性質を変えるのは意図した契約の変更の根拠（テストまたはcommitの説明）がある場合だけにする。

## 追従

- 版の記録はfrontmatterの`verifies`と`verified-digest`で持ち、pyfltrの`ci`とcommit時の`fast`が対象と記録の一致を確かめる。
  対象を変えたら、`verified-digest`を最後に書いたcommitからの対象の差分を読み、検査を再実行して現在のコードを記述すると確かめる。
  確かめた後にだけ、`uv run --frozen pyfltr verification-sync --update .claude/skills/git-sync-interleavings/SKILL.md`で記録を進める。
- Gitの起動、ロックの区間、remoteへの操作の追加と順序の変更は、`_launch`と`_RepoLock`を差し替える枠組みが自動で拾う。
  WIの状態遷移の流れ（`push_pending`の有無、commitの単位）を変えた時は`_atk_update`を合わせる。
- 検査が働くことは、`git_sync.py`の一時的な複製へ次の変異を適用し、`AGENT_TOOLKIT_GIT_SYNC_INTERLEAVINGS=first`で確かめた。
  複製は`PYTHONPATH`で開発環境の`agent_toolkit`より先に読ませ、worktreeの`git_sync.py`はそのまま保つ。
  - `_recover_redundant_divergence`の冗長性の確認を削る: `shared-clone`の2通り目で、成功を返したBの更新が失われた。
  - `_push_pending_commits_impl`の`fetch`を祖先関係の判定の後へ移す: `shared-clone`の2通り目で性質1（操作の終了時）が破れた。
  - `_pull_impl`と`_push_pending_commits_impl`の`ensure_not_rebasing`を削る: `conflict-shared-clone`の35通り目で性質2が破れた。
  - `_pull_impl`と`_push_pending_commits_impl`の`rebase @{u}`を`rebase -Xtheirs @{u}`にする: `conflict`の10通り目と`conflict-shared-clone`の35通り目で性質2（内容の衝突で止まる）が破れた。
    この性質を加える前の検査は、この変異でも`conflict`の403通り全てで違反なしと判定した。
    この変異は複製の代わりに、読み込んだ`git_sync`の2つの関数だけを変異後の定義へ差し替えるpytestのプラグインで適用した。
  - 変異を戻した複製は、worktreeと同じ内容であることを`diff`で確かめ、`shared-clone`で元の結果（17通り、違反なし）へ戻った。
- workerの場合は一時複製の`background_sync.py`のrebaseに`-Xtheirs`を加えた。
  `worker-conflict`の最初の順序で、worker側の送信によって競合を保つ性質が破れた。
  worktreeには変異を適用していない。
- 変更後も、守る性質の向きにコードを一時的に変えて失敗することを確かめてから戻す。

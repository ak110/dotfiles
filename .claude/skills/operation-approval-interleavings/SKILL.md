---
verified-digest: b262702ec524c1fb64e64f251357fadfa97922fdef4050d2661a05e3ca366eef
name: operation-approval-interleavings
user-invocable: false
description: >
  WIの操作承認（`agent-toolkit/agent_toolkit/_atk/wi/operation_permissions.py`、`agent-toolkit/agent_toolkit/_common/operation_approvals.py`）の
  照合、消費、取消、保存の扱いを変える時と、それらへ触れた変更を検査せずに統合した後に、
  並行する要求と取消の全ての順序で、承認の範囲を超える許可と承認済みの操作の拒否が起きないかを確かめる時に起動する。
verifies:
  - agent-toolkit/agent_toolkit/_atk/wi/operation_permissions.py
  - agent-toolkit/agent_toolkit/_common/operation_approvals.py
  - agent-toolkit/agent_toolkit/_common/session_state.py
  - agent-toolkit/agent_toolkit/_atk/wi/operation_permissions_interleaving_test.py
---

# 操作承認の照合・消費・取消の網羅検査

`agent-toolkit/agent_toolkit/_atk/wi/operation_permissions_interleaving_test.py`は、操作承認の有界の網羅検査である。
実際の`operation_permissions`と`operation_approvals`のコードで、同じセッションの2つの実行要求と1回の取消の順序を全て列挙して性質を確かめる。
スレッドの停止・再開と順序の列挙は`pytilpack.modelcheck.ThreadExplorer`が担う。
検証の作り方と追従の一般の手順は`agent-basics:formal-verification`にある。

## 実行

```bash
cd <dotfilesのworktree> && AGENT_TOOLKIT_OPERATION_APPROVAL_INTERLEAVINGS=all uv run --frozen pytest -v -s -o addopts='' -p no:cacheprovider agent-toolkit/agent_toolkit/_atk/wi/operation_permissions_interleaving_test.py
```

- 変数が無い時は、取消を含まない8シナリオ（各10通り以下）だけを通常の試験（`make test`、CI）で実行し、取消を含む12シナリオは`skip`になる。
  全20シナリオは約1,430通りで約18秒（2026年11月24日、Linux、1つのプロセス）かかり、通常の試験のCPU時間の上限（1試験0.5秒）を超えるためである。
  取消を含まない8シナリオは、通常の試験の設定で合計約2秒である。
- 出力はシナリオごとに、順序の数、所要、許可した件数と保存後の承認の状態（`state/remaining`）の分布、違反した性質、性質ごとの最初の反例（主体ごとの手順の列）である。

## 対応と前提

- 主体は、同じ操作を同じセッションで実行しようとする要求A・B（`pretooluse`）と、取消C（`revoke_permission`）である。
- 1手順は、セッション状態の1回の読取（`read_state`）と1回の更新（`update_state`。排他ロックの中の読取・変更・書込を分割しない）である。
  UWIの1回の一覧（`entries.iter_entries`）、1回の書込（`frontmatter.write_entry_text`）、保存排他の取得（`Storage.mutation`）も1手順とする。
- 承認の出所は、対話の直接回答（`direct`）とUWIの回答（`uwi`）の2つ、回数は`once`と`count:2`である。
- 保存の失敗は、要求側の最初の1回だけ、セッション状態の書込（`session-write`）かUWIのcommit（`uwi-commit`。作業ツリーへの書込は済んだ後の例外）へ注入する。
- 前提:
  - `session_state.update_state`の1回は、他の主体の更新が割り込まずに終わる。
  - UWIの保存排他（`Storage.mutation`）は、private-notesのGitの同期を行わない差し替えで、排他の区間だけを再現する。
    Gitの同期の順序は`.claude/skills/git-sync-interleavings/SKILL.md`が扱う。
  - 承認の分類器（`approval_classifier.inspect`）、対話画面の確認（`function_hook_requests.interactive_available`）、remoteのURLは固定値を返す。
    実効設定の変化や対話画面の不在による拒否は範囲の外とする。
  - 範囲は要求2件、取消1回、承認1件である。

## 性質と害

性質は`operation_permissions.py`の`pretooluse`・`revoke_permission`と、`operation_approvals.py`の`matches`・`consume`の契約（承認の回数、取消、保存できない時の通常の権限確認への差し戻し）から導いた。

| 性質 | 破れた時の害 |
| --- | --- |
| 性質1: 許可は承認の回数を超えない | 認可していない回数の書込や外部操作が実行される |
| 性質2: 取消の完了後に始まった要求と、取消後の新しい要求を許可しない | 取り消した承認で操作が実行される |
| 性質3: 取消も保存の失敗も無ければ、有効な承認の回数まで許可する | 承認済みの作業が通常の権限確認へ戻り、止まる |
| 性質4: 承認の消費をセッション状態かUWIへ保存できなかった要求を許可しない | 記録の無い許可が残り、取消や回数の管理が効かない |

## シナリオと結果

2026年11月24日の結果（`verified-digest`を書いたcommitで実行）。
全シナリオで違反は無い。

| 承認 | 取消なし | 取消あり |
| --- | --- | --- |
| `direct-once` | 4通り。失敗の注入で3通り | 62通り。失敗の注入で61通り |
| `direct-count2` | 4通り。失敗の注入で3通り | 80通り。失敗の注入で61通り |
| `uwi-once` | 6通り。`session-write`で5通り、`uwi-commit`で4通り | 121通り。`session-write`で119通り、`uwi-commit`で41通り |
| `uwi-count2` | 10通り。`session-write`で9通り、`uwi-commit`で4通り | 401通り。`session-write`で351通り、`uwi-commit`で81通り |

- 保存の失敗を注入したシナリオでは、失敗した要求の分の承認が消費済みのまま残ることがある（例: `uwi-once-session-write`は5通り全てで許可0件・`consumed`）。
  保存できない時に許可しない方向の振る舞いで、性質4の契約どおりである。
- この検査の作成時に、`count:2`で並行する2件目の要求を拒否する反例（性質3）が見つかった。
  直接回答とUWIのどちらの経路も、保存排他の中で読取時点の記録との完全一致を求めており、先の要求が残り回数を減らしただけで照合をやめていた。
  `fix(operation-permissions): 並行する要求が回数付きの承認の残りを拒否しないようにする`で、同一性の照合を質問・回答・要求に限り、状態と残り回数を`consume`で判定する形に直した。

## 欠陥と検証の誤りの区別

反例の各行は、主体と、その主体が実行した手順の名前である。
各行を実際のコードの分岐で起こりうるかを確かめ、起こらない行があれば検査の枠組み（`_instrument`の差し替え、`_FakeStorage`、前提）の誤りとして直す。
全て起こりうれば欠陥として`agent-basics:bugfix`を起動する。
性質の判定は`_violations`にあり、性質を変えるのは意図した契約の変更の根拠（試験またはcommitの説明）がある場合だけにする。

## 追従

- 版の記録はfrontmatterの`verifies`と`verified-digest`で持ち、pyfltrの`ci`とcommit時の`fast`が対象と記録の一致を確かめる。
  対象を変えたら、`verified-digest`を最後に書いたcommitからの対象の差分を読み、検査を再実行して現在のコードを記述すると確かめる。
  確かめた後にだけ、`uv run --frozen pyfltr verification-sync --update .claude/skills/operation-approval-interleavings/SKILL.md`で記録を進める。
- 永続状態への新しい読取・書込の経路を足した時は、`_instrument`で手順として止める。
  止めない経路の順序は列挙されない。
- 検査が働くことは、作業ツリーのコードへ次の変異を一時的に適用し、`AGENT_TOOLKIT_OPERATION_APPROVAL_INTERLEAVINGS=all`で確かめてから戻した。
  - `operation_approvals.matches`の`state == "approved"`の照合を外す: `direct-once`・`uwi-once`とその取消ありで性質1が破れた。
  - `revoke_permission`の直接回答の承認を`revoked`にする処理と、`pretooluse`の保留中の拒否の`revoked`の確認を外す: 直接回答の取消ありの4シナリオで性質2が破れた。
    どちらか片方だけを外した変異は、もう一方が取消を止めるため違反にならない。
  - `_consume_uwi`でセッション状態の保存の失敗を無視する: `session-write`を注入したUWIの4シナリオで性質4が破れた。
  - 上記の修正の前のコード: `direct-count2`と`uwi-count2`で性質3が破れた。
  - `matches`と`consume`の残り回数の照合を外す変異は、残り1回の消費で状態が`consumed`になり状態の照合が止めるため、違反にならない（等価な変異）。
  - 変異を戻した後、全20シナリオで違反なしの結果へ戻ったことを確かめた。
- 変更後も、守る性質の向きにコードを一時的に壊して失敗することを確かめてから戻す。

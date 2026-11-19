# 外部サービスへの投稿前レビュー

第三者が読む外部サービスへ文面を送る直前に、起草者と独立した読み取り専用の担当に文面を確かめさせ、指摘を反映してから投稿する手順書である。
作る者と確かめる者を分ける原則（`agent-basics:writing-standards/references/quality-principles-verification.md`）の適用であり、投稿の認可とは別に行う。
対象の判定、レビューの依頼、指摘の採否、実際の投稿は、投稿を担当するエージェントが行う。

## 対象

- 対象: PR・MR・イシューの作成と本文の編集、コメント、レビューへの返信、Releaseの本文、チャットやメールへのMCP経由の送信など、第三者が読む外部サービスへ、エージェントが起草または加筆した人間向けの文面。
  タイトル（PR・MR・イシュー・Releaseの表題）も文面に含める。
- 対象外: ユーザーから逐語で渡された文面、git push、コミットメッセージ、private-notesのWI本文。
  コミットメッセージは`agent-toolkit:atk-mode/references/commit-message.md`、WI本文は`agent-toolkit:atk-mode/playbooks/wi-drafting.md`に従う。
- 原文や資料から起草・推敲した文面の意味の保持は`agent-basics:writing-standards/references/writing-genres.md`「ユーザー入力素材の取扱い」に従う。
- 投稿の操作に要る認可は`agent-toolkit:atk-mode/playbooks/confirmation.md`「認可を要する操作」で判定する。
  レビューを通したことは投稿の認可にならない。
- 独立した担当を起動できない環境では、提示された文面と根拠を読み、検討結果を下書きとして返す。
  レビューと認可を経た投稿は、Claude CodeかCodexで行う。

## 投稿前レビュー担当の起動と受領

```text
起動対象: external-write-review.subagent.md
```

<!-- input-contract:external-write-review:start -->
<!-- 生成元: skills/atk-mode/roles/external-write-review.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。

- 必須入力名: `文面ファイル`、`投稿先と目的`、`根拠の所在`

<!-- input-contract:external-write-review:end -->

レビュー担当の観点は`agent-toolkit:atk-mode/roles/external-write-review.subagent.md`が定める（所在参照。起動の前に読まない）。

### 起動

1. 確定した文面を、タイトルを含めて読み取り可能なUTF-8の通常ファイルへ保存する。
2. `agents_server`の`start`を次の引数で1件起動する。
   委譲先として投稿するエージェントも同じ手順で起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 文面が根拠とする対象リポジトリの絶対パス |
| `subagent_md_path` | `external-write-review` |
| `extra_params` | `文面ファイル`、`投稿先と目的`、`根拠の所在`の3つだけ |

- `文面ファイル`: 手順1のファイルの絶対パス。
- `投稿先と目的`: 投稿先と投稿の目的。
- `根拠の所在`: 文面が根拠とする差分・観測結果・関連ファイルの絶対パス、またはcommit、run ID、PR番号など対象を一意に取得できる識別子と取得先。
  複数はJSON配列（各要素は絶対パスか、取得先を添えた識別子）で渡す。
  同じ観測時点に固定する必要がある資料は、ファイルへ保存して渡す。
  レビュー担当は同じ対象・版の根拠を読み取り専用の操作で取得し、文面の主張と根拠の対応を確かめる。

### 受領

1. 結果は引数なしの`atk agents wait`で受け取る。
2. 指摘の採否は`agent-toolkit:atk-mode/playbooks/review-fix.md`に従って確定し、採用した指摘を文面へ反映してから投稿する。
   レビューは投稿前に1回とし、反映で事実の主張か対象の範囲が変わった時だけ、再レビューの要否を判断する。
3. 同じ入力で再起動しても結果を返さずに終わる場合や、`start`と同等の読み取り専用の委譲手段が使えない場合は、投稿を中断する。
   原因と同等のレビュー手段を調べ、認可の範囲で復旧できれば自分で再開する。
   レビューの省略や公開する結果の変更に、追加の認可かユーザーの選好が要る時だけ、文面と理由を示して確認する（`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」）。

<!-- return-contract:external-write-review:start -->
<!-- 生成元: skills/atk-mode/roles/external-write-review.subagent.md「出力」。手動編集しない。 -->
### 投稿前レビューの返却契約

次の固定形式のみで結果を返す。
根拠を読み取れなかった主張や、根拠との対応を確認できなかった主張は、`未解決の指摘数`に算入し、観点2の指摘としてその範囲を明記する。

```text
状態: completed
未解決の指摘数: <0以上の整数>
<指摘ごとの該当行、観点の番号、問題点、修正案。0件なら3観点それぞれの「指摘なし」>
```

続行不能と追加報告行は、共通契約`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」に従う。
<!-- return-contract:external-write-review:end -->

返す報告に含める項目: 投稿した文面の所在と投稿先、レビューの未解決の指摘数と採否、投稿しなかった場合はその理由。

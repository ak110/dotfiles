# 表記の一括置換と一括置換後レビュー担当の起動

```text
起動対象: bulk-replace-review.subagent.md
```

`agent-basics:writing-standards/references/notation-rules.md`「表記の一括置換」で置換を適用したエージェントが、変更箇所の全件レビューをこの担当へ渡す時の起動と受領である。
置換の手順、誤置換時のやり直し、書き直しと検査の実行は同節に従う。

## 運用条件

- 指摘を受けたら、採否と修正の前に`agent-basics:bugfix`を起動する。
- 害が大きく、広がりの走査（`agent-basics:bugfix/references/spread.md`「害が大きい時」）を独立の担当へ渡す時は、`agent-toolkit:atk-mode/roles/similar-review.parent.md`で起動する。
  結果の採否は委譲元が確定する。

## 入力

<!-- input-contract:bulk-replace-review:start -->
<!-- 生成元: skills/atk-mode/roles/bulk-replace-review.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
起動準備はこの契約の入力一覧で完結する。

- 必須入力名: `差分ファイル`

<!-- input-contract:bulk-replace-review:end -->

- `差分ファイル`: 置換した範囲の`git diff --word-diff=plain --word-diff-regex=.`を、managed-temp（`agent-toolkit:atk-mode/playbooks/managed-temp.md`）の中のファイルへ保存した絶対パス。

## 起動

1. 本書「入力」の差分ファイルを保存し、`agents_server`の`start`を次の値で呼んで1件起動する。
   `cwd`は置換した作業ツリーの絶対パス、`subagent_md_path`は`bulk-replace-review`、`extra_params`は`差分ファイル`だけとする。

## 受領

1. 引数なしの`atk agents wait`で結果を受け取る。
2. 全範囲を読んだことを確かめるため、返却の`読んだ変更箇所数`を、起動時に保存した差分ファイルの変更箇所数（`grep -c -F -e '[-' -e '{+' <差分ファイル>`の値）と比べる。
   - 少ない場合は完了とせず、問題が報告された箇所の結果を保つ。
     返却のファイルと行から未読の範囲を特定し、その範囲だけを差分ファイルへ保存して新しい起動へ渡す。
     特定できなければ全差分を渡す。
   - 多い場合も完了とせず、数え方の不一致として同じ担当へ数え直しを1回求め、再び比べる。
3. 問題が報告された文と置換候補に合わない文を、`agent-basics:writing-standards/references/notation-rules.md`「表記の一括置換」の手順5に従って書き直す。

<!-- return-contract:bulk-replace-review:start -->
<!-- 生成元: skills/atk-mode/roles/bulk-replace-review.subagent.md「出力」。手動編集しない。 -->
## 一括置換後レビューの返却契約

次の固定形式のみを返却する。
`未解決の指摘数`には、問題が見つかった箇所の件数を指定する。
`読んだ変更箇所数`は、差分中に`[-…-]`または`{+…+}`を含む行数で数える。

```text
状態: completed
未解決の指摘数: <0以上の整数>
読んだ変更箇所数: <件数>
<問題のある箇所ごとのファイルと行、観点、問題点、修正案。0件なら全件で問題が無いこと>
```

続行不能と追加報告行は、共通契約`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」に従う。
<!-- return-contract:bulk-replace-review:end -->

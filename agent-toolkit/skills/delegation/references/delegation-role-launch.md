# 委譲：`<役割名>.parent.md`を持つ委譲の起動

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲プロンプトや返却の文章を作成・検収する前 | `quality-standards.md` |
| 役割文書を持つ委譲の入力値を確定する前 | `base-contract.md` |

## `<役割名>.parent.md`を持つ委譲の起動

呼び出し元の契約で起動手順が定められている委譲は、次の定型手順で起動する。
本節の規定は、共有の`<役割名>.parent.md`を用いる場合と、単一の呼び出し元規範に統合された契約を用いる場合の両方に適用する。
委譲元は起動前に該当する契約を読み、前提条件、入力値の決定規則、受領と検収の要件に従う。
各契約には役割ごとに異なる引数値のみを記述する。

1. 入力をそろえるため、起動契約が定める絶対パスを`cwd`、役割名（例: `add-wi`）を`subagent_md_path`、名前付き入力を`extra_params`へ設定する。
   サーバーが自身のplugin rootの`share/<役割名>.subagent.md`を解決する。
   `mode`は指定しない。
   `model_type`は起動契約に値の決定規則がある場合にのみ指定する。
   省略された場合のmodeやmodel_typeは、サーバーが役割文書のfrontmatterから自動的に解決する。
   `引き継ぎ記録先`の扱いについては、`base-contract-handoff.md`「引き継ぎ記録先」の規定に従う。
   `extra_params`に設定する値には、`sending-prompt.md`「委譲プロンプトの構成」の「値に含めない内容」と「未検証の値」、ならびに`base-contract.md`の`forwarded-user-input`標識と絶対パスの規定を適用する。
   必須入力の漏れや未宣言の入力名の拒否、`引き継ぎ記録先`の準備、応答言語の伝達、`<役割名>.subagent.md`の実在確認、`${CLAUDE_PLUGIN_ROOT}`の展開はサーバー側で処理する。
   サーバーから起動を拒否された場合は、エラー応答に示された項目と指示に従って再起動する。

2. 担当を開始するため、確定した引数で`agents_server`の`start`を1回呼び出す。

3. 起動後は返却された`session_id`を`atk agents wait`で観測する。
   Claude Code環境では、初回の`start`処理の中でagent-toolkitのmodが定期再確認タスクを設定し、その結果が`start`の返却後に通知される。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 基本委譲契約：引き継ぎ記録先の基準を適用する前 | `base-contract-handoff.md` |
| 委譲プロンプトの送信：委譲プロンプトの構成の基準を適用する前 | `sending-prompt.md` |

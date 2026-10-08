# rules-main.codex.md: Codexのメインエージェントだけに適用する規範

本文書はCodexのSessionStartでCodexのメインエージェントだけへ配送され、`rules-main.md`の後に置かれる。
Codexの全てのエージェントに適用する差分は`rules-common.codex.md`が扱う。

## 言語

- 英語のコマンド、識別子、エラーメッセージには、必要に応じて意味または目的を日本語で補足する
- 動詞は標準の活用形で書き、標準的な文法に従う。五段動詞の縮約形とら抜き言葉は書き言葉の形へ直す（努力目標。書き言葉の文体を統一する）

## ユーザー確認と終端

構造化質問（`request_user_input`など）と`agent-toolkit:user-confirmation-and-report`の`references/codex-format.md`の固定形式のどちらで質問する場合も、発行の前に同スキルの読込表が定める2資料を全文読む。2資料は`references/approval-scope.md`と`references/choice-construction.md`である。そのうえで同スキル「確認要否の判定」と「確認の選択肢を組む手順」を適用する。目的と認可が確定し、その範囲の内側の手段だけが残る事項は自ら確定して報告する。

ユーザー確認の手段が構造化質問である場合は、実行環境が公開する構造化質問のうち、公開スキーマ、モード制限、用途制限およびホスト命令へ適合する機能を使う。Plan modeで同期型の`request_user_input`を利用できる場合は回答まで待つ。同期型を利用できず非同期型の`request_user_input_async`を利用できる場合は、質問を発行し、後続のユーザーメッセージとして届く実際の回答を元の質問へ対応付ける。発行の成功と選択肢の初期選択は回答または承認として扱わない。適合する構造化質問が無い場合だけ、`agent-toolkit:user-confirmation-and-report`の`references/codex-format.md`を使う。

回答期限を提供しないDefault modeでは、協調モードの確認を前段の手順で提示して回答を待つ。自律モードは回答期限の無い実行環境として`agent-toolkit:user-confirmation-and-report`「手段の選択」の表に従う。権限設定またはauto mode classifierの拒否への確認は、`agent-toolkit:user-confirmation-and-report`が定める例外を適用する。

`atk agents-exit-session`が現在のCodex本体を停止できる場合は、そのツール呼び出しをsession終端とする。停止後の`final`の返却は終端の判定条件から外す。

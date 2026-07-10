# 計画ファイル本文Write前の事前検査

`plan-file-guidelines.md`「計画ファイル全体の遵守事項」節から参照される、初版`Write`前の機械検査手順。

## text コードブロック化義務

- `## 調査結果`・`## 変更内容`配下の追加文面案・反映文面案など
  他文書本文を転記する長文段落（120字を超え得る段落）は`text`コードブロックで包んで記述する。
  textlintのsentence-length違反を初版`Write`段階で回避する
  - `## 調査結果`配下の既存docstring要約・既存関数の説明文・既存規範文の引用などの長文段落も対象に含める
    （行数・件数等の単純な箇条書きの確定値は対象外）
- 計画本文で機械検査の検出パターン代表例・検出対象語彙・禁止語彙を記述する場合、
  地の文ではなく必ず`text`コードブロック内に配置する（走査対象と代表例が同一節で自己抵触するリスクを事前予防する）
- 新規規範または新規機械検査の導入計画では、計画ファイル本文自身へ先行適用してから初版`Write`へ進み、
  対象該当時は対象外形式（コードフェンス内転記・抽象表現での参照など）へ事前変換する（規範導入計画は初回適用事例）

## scratchpad一時ファイルへの事前検査

計画ファイル本文全域を`Write`前にscratchpad配下の一時ファイルへ出力する。

- 出力は`agent-toolkit/skills/plan-mode/scripts/build_pre_lint_copy.py`を呼び出して生成する。
  CLI: `build_pre_lint_copy.py <計画ファイル> <一時ファイル>`
  同スクリプトが`## 背景`配下のフェンス付きコードブロックのみ除外する
  （ユーザー提示素材原文の口語表現で事前検査が失敗するため）。
  `## 変更内容`配下のコードブロックは対象に含め、unified diffはフェンスと行頭`+`・`-`を除去して出力する
- 出力後に次のコマンドを実行する。
  `uvx pyfltr run-for-agent --commands=textlint,markdownlint,typos,colloquial-check --enable=colloquial-check <一時ファイル>`
  続けて`check_line_width.py`・`check_dash.py`・`check_line_ref.py`・
  `check_self_ref.py`・`check_plan_diff_gates.py`も実行し、本文全域および対比ブロックの違反を反映前に解消する
  （工程7を代替としない。`writing-standards`の厳格適合対象は`## 変更内容`配下のコードブロックに限定）

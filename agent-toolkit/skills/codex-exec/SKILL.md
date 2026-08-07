---
name: codex-exec
description: >
  plan-modeの呼び出し元またはplan-impl-executorから明示的に呼ばれ、
  codex MCPまたは汎用エージェントへ作業を委譲するときに起動する。
user-invocable: false
# 編集時の注意点:
# agents定義は単一Markdownで専用referenceを持てないため、計画ファイル処理に固有の
# レビュー・実装・実装差分レビュー用プロンプトを本スキル配下referencesへ置く。
# SKILL.md本体は共通の委譲経路だけを扱う。
---

# codex委譲

codex MCPを優先し、利用できない場合だけ汎用エージェントへ切り替える。
用途固有の作業内容は呼び出し元がreferenceから構成し、本スキルは接続・継続・代替経路だけを担う。
設計判断を伴う実装・レビューはGPT-5.6-Solを選ぶ。内容が確定した低リスクの機械作業だけ
GPT-5.6-Terraまたは同等の軽量経路を選べる。価格やquotaの固定比較ではなく、失敗時の再試行を含む総費用で決める。

Skillツールの成功応答は本スキルの読み込み完了を示し、委譲結果の待機状態を示さない。
呼び出し元は成功後、既存のMCPスキーマの有無を確認し、ToolSearch、MCP初回接続、
MCP継続接続、Claude代替判定のうち該当する工程へ直ちに進む。
`plan-mode`の呼び出し元と`plan-impl-executor`はSkill、ToolSearch、MCP接続、委譲の順序を共通で適用する。
起動文と受領の共通契約は`references/delegation-boilerplate.md`を正本とする。

## 入力

呼び出し元から次を受け取る。

- 作業ディレクトリの絶対パス
- 実行手順referenceとtask referenceの絶対パス
- 計画、品質規範、プロジェクト規範などtask referenceが要求する資料の絶対パス
- 作業ディレクトリ、対象、完了条件、実行時に確定した値、task referenceが要求する用途別の必須値、
  呼び出し元が許可した追加指示だけで構成したタスク本文
- `計画レビュー系`、`計画準拠実装レビュー系`、`独立実装レビュー系`、
  `実装・修正系`のいずれか
- codex経路を継続する場合の`threadId`
- Claude代替を継続する場合の系統別Agent識別子
- Claude代替を継続する場合の前回応答全文

用途固有referenceは次の組とする。frontmatterで本スキルを読み込んだだけでは
referenceを読み込んだと扱わない。呼び出し元と委譲先の双方が絶対パスで全組をReadする。

| 用途 | 実行手順reference | task reference |
| --- | --- | --- |
| 計画機械チェック・修正／計画レビュー | `plan-codex-review.md` | 同reference「用途別task reference」で選択 |
| 計画実装・修正 | `plan-codex-implementation.md` | `plan-codex-implementation-task.md` |
| 計画準拠の実装差分レビュー | `plan-codex-implementation-review.md` | `plan-codex-implementation-plan-review-task.md` |
| 独立した実装差分レビュー | `plan-codex-implementation-review.md` | `plan-codex-implementation-independent-review-task.md` |

task referenceは委譲先自身が守る作業契約だけを記載する。
実行手順referenceは経路選択、継続、検収、再委譲を記載する。

## codex経路

1. ToolSearchで`mcp__codex__codex`と`mcp__codex__codex-reply`のスキーマを解決する
2. `threadId`が無い初回は`mcp__codex__codex`を呼び出す
   - `cwd`には受領した作業ディレクトリの絶対パスを指定する
   - `sandbox`には`danger-full-access`を指定する
   - 設計判断を伴う実装・レビューではGPT-5.6-Solと十分なreasoning effortを指定する
3. 同じ系統の`threadId`がある場合は`mcp__codex__codex-reply`で継続する
4. 指定モデルが利用できない場合だけ、同じ経路で利用可能な既定モデルへ切り替えて再試行する
5. 一時的な失敗は同じ経路で再試行する

Codexへ渡すプロンプトは、referenceと資料の絶対パス、作業ディレクトリ、対象、完了条件、
実行時に確定した値、task referenceが要求する用途別の必須値、呼び出し元が許可した追加指示だけで構成する。
Codexが利用しないClaude Code固有の経路管理、通知、受領手順を含めない。

初回応答の`threadId`は呼び出し元へ返し、同じ系統の後続処理へ再利用させる。
計画レビュー系、計画準拠実装レビュー系、独立実装レビュー系、
実装・修正系の`threadId`は混同しない。

同じ`threadId`へ継続の指示を送る場合は、先行する指示の応答を受領してから次を送る。1つのセッションは
逐次処理であり、先行分の完了前に追送すると双方の応答が返らないまま待機が続く。先行分の完了前に
追加の判断材料が生じた場合は、次の指示へ統合して1回で送る。複数系統へ同時に渡す必要がある場合は、
系統ごとに別の`threadId`を用いる。

中断された`threadId`へ継続の指示を送らない。中断とは、codexの応答が`turn_aborted`を示す場合、
およびMCPツールがタイムアウトを示す文字列を返した場合をいう。
当該`threadId`は以後の継続へ用いず、新規スレッドで再開して前回の応答全文と確定済み状態を渡す。
中断された会話への継続呼び出しは、codex側がターンを完了しても応答が配送されない事象を実測した。
同時間帯に開始した新規スレッドは正常に応答を返すため、スレッドを分けることで当該事象を回避できる。

## Claude代替経路

ToolSearchでcodex MCPを解決できない場合だけ、
呼び出し元がAgentツールで`subagent_type: claude`へ切り替える。
利用限度到達を応答から確認した場合も同じ経路へ切り替える。
本スキルの起動元が`plan-mode`の呼び出し元または`plan-impl-executor`でありAgentツールを利用できない場合は、
`route: unavailable`を返して呼び出し元へ代替起動を要求する。

通常のツール戻り値または完了通知を第一の受領経路とする。
通常配送が成立しないことを実測した場合だけ、
`references/delegation-boilerplate.md`が定める記録照会へ切り替える。

Agentを起動した主体は記録ファイルから受領した応答全文を実測と照合する。
呼び出し元が代替起動した場合は、検収済みの応答全文を窓口へ戻す。
利用した経路は実際の実行結果に基づいて1回だけ報告し、予定した経路を実績として扱わない。

## 出力

呼び出し元へ次を返す。
呼び出し元固有の`status`・`changed`・`verification`・`unplanned`などの完了報告欄は定義しない。

```text
route: codex | claude | unavailable
thread_id: <codexのthreadId。Claude代替または利用不能では「なし」>
agent_id: <ClaudeのAgent識別子。codex経路または利用不能では「なし」>
response:
<委譲先の応答全文。利用不能では理由と呼び出し元への要求>
```

レビュー担当による成果物変更を委譲先応答または呼び出し元の実測で検知した場合、
変更内容を採用せず呼び出し元へその事実を返す。

# 実行経路の選択

委譲の起動直前に、実際に利用できる経路へ該当する節だけを読む。
受信者固有の作業手順は本referenceへ置かない。

## 経路

- 専用agent定義がある作業は、その定義を実装する実行機能で起動する
- Codex MCPを利用できるClaude Code環境では、ToolSearchで実在とスキーマを確認してから初回接続または継続接続を選ぶ
  - 新規接続では作業ディレクトリの絶対パスと`sandbox: danger-full-access`を例外なく渡す
  - 他のsandbox値又は作業ディレクトリの欠落では相手プロセスが承認待ちから復帰せず、呼び出し元が完了を検知できないため、この値を使う
  - 読み取り専用は`## 読み取り専用`の手順で担保し、実行環境のsandbox値で表現しない
  - 継続接続は同一threadへの返信用経路を使い、作業ディレクトリとsandboxを再送しない
  - モデルを指定する場合は`config`へ`model_reasoning_effort`も渡し、意図した深さを明示する
- Codex自身はMCP経由で自己呼び出しせず、利用可能なサブエージェント機能へ同じ契約で読み替える
- 専用定義もCodex経路も利用できない場合だけ汎用Agentを使う
- 起動結果として返されたrouteと識別子を保持し、予定した経路を実績として記録しない

## 工程別モデル設定

次の工程では、消費主体が起動直前に`atk config get <キー>`を実行して実効値を取得する。

| キー | 対応工程 | 消費主体 |
| --- | --- | --- |
| `pick_feedbacks_model` | フィードバック調査 | `feedbacks-planner` |
| `plan_model` | 計画起草とレビュー指摘反映 | `feedbacks-planner` |
| `plan_review_model` | 計画レビュー | `feedbacks-planner` |
| `execute_model` | 実装writerと統合後レビュー修正writer | `plan-impl-executor` |
| `execute_review_model` | 実装後の二系統レビュー | `plan-impl-executor` |
| `merge_model` | lane commitの適用、競合解消、履歴一本化、検証 | `process-feedbacks`の実行主体 |

設定値の書式は`<engine>:<model>[/<effort>]`とし、`engine`は`claude`または`codex`とする。
全キーの未設定時の実効値は`codex:gpt-5.6-sol/medium`とし、effort省略時は`medium`とする。
モデル名とeffortの受理可否は各engineの実行機能へ委ねる。

1. 設定値を`engine`、`model`、`effort`へ分解する。
2. `engine=codex`ではCodex MCPを使い、`config`へ`model`と`model_reasoning_effort`を渡す。
   agents定義の`tools`でMCPツールを直接許可している場合は、ToolSearchによる実在とスキーマの照会を省略できる。
3. `engine=claude`ではAgentツールを使い、`model`へモデル名部分を渡す。
   effort部は実行機能に相当する引数が無いため適用しない。
4. 指定engineの経路を利用できない場合は他engineへ自動切替せず、当該工程を`needs_escalation`または未完了として返す。
5. Codexは同一threadへ継続接続する。
   Claudeは完了済み識別子を再利用せず新規起動する。
   計画、進捗ログ、保存済み6列表のいずれかで検収済み状態を一意に参照できる場合は、
   正本の絶対パス、対象ID、未記録の差分だけを渡す。
   参照可能な正本がない場合は、前回の指摘6列表、反映差分、検査結果などを起動文内で完結させる。

工程別モデル設定の適用範囲は表に記載した工程に限定し、他の委譲には「modelとreasoning effort」を適用する。

## modelとreasoning effort

次の基準を上から評価し、最初に該当した項を選ぶ。reasoning effortの既定値は`medium`とする。

1. 設計判断を伴う実装とreviewは上位モデルを選ぶ
2. 内容が確定済みで低リスクな機械作業は軽量モデルとreasoning effort `max`を選ぶ
3. その他は標準モデルを選ぶ

モデルを明示する経路ではreasoning effortも併せて指定する。
指定モデルを利用できない場合は、作業を成立させる利用可能なモデルへ切り替える。
価格やquotaの固定比較ではなく、失敗時の再試行を含む総ライフサイクルコストで選ぶ。
前記の選択基準は努力目標とし、指定モデルを利用できないことだけで作業を停止しない。

## 読み取り専用

reviewerと調査担当は対象成果物を読み取り専用とする。
起動前後の`git status --short`と対象commitを比較し、書き込みがあれば結果を採用せず報告する。
再現証跡が必要な場合は、管理対象一時領域だけを書込可能にする。
読み取り専用の担保に、実行環境のsandbox値による書込制限を用いない。

## writerとworktree

- 1つのworktreeへ同時に起動するwriterは1つだけとする
- writer起動前に上流追随済みで、staged、unstaged、non-ignored untrackedが全て空であることを確認する
- 作業ディレクトリ、複製元、対象外worktreeを絶対パスで渡し、複製元リポジトリのファイルを編集させない
- git操作は`git -C <受領したworktree絶対パス>`の形とし、作業場所を自己解決させない
- reviewerはwriterの終端後に起動し、相互に独立したreviewerは別識別子で並列起動できる
- 作業用の複製（git worktree等）内でセッションを起動する場合は、調査・計画作成への着手前に
  `git fetch`後の分岐元との差分を双方向で確認し、分岐元が進んでいる場合は先に追随してから着手するよう
  起動文で指示する（努力目標）

## snapshot

外部委譲による意図しないremote ref変更を検出する必要がある経路では、起動前後のremote refを同じ条件で取得する。
委譲元が差分を検出した場合は、委譲先が変更したと推定して復元せず、対象refと観測値を呼び出し元へ返す。
ローカル成果物の検収はsnapshotで代替せず、Git実体と検証結果を直接確認する。

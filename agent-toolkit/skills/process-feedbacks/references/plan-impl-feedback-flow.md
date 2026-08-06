# 計画実装型フィードバックの処理フロー

計画実装型フィードバックの判定、依存関係、並列prepare、直列finalizeを定める。
`agent-toolkit:process-feedbacks`の計画実装フローから参照するSSOTとする。

## 判定基準

トップレベルの`plan_file`を持つactive feedbackを計画実装型とし、それ以外を通常型とする。
本文から型を推定しない。`plan_file`が存在しない場合は通常型へ変換せず、修復対象として扱う。

## readiness

active feedbackは次の条件を全て満たす場合にreadyとする。

- `depends_on`が参照する全項目が終端状態にある
- TBDの場合は回答済みである
- frontmatterを解析できる
- 計画実装型の`plan_file`が実在する

トップレベルの`depends_on`を正本とする。過去に生成された`queue_schedule.dependency`は読取互換としてのみ
解釈し、新規生成・更新しない。欠落依存、自己依存、循環、frontmatter破損、計画ファイル消失はblockedとし、
修復対象としてprocess-loopの起動件数へ含める。未回答TBDと明示依存以外を推測でblockerへ追加しない。

## 分類

未分類の通常型が1件だけならメインが分類する。複数件の場合だけ、分類referenceと選択したfilenameを渡して
1回の分類委譲を行う。本文hash、対象ファイル予測、carry、競合groupは作成しない。

## 実行wave

readyな計画が1件の場合は現在のcleanなworktreeで実行する。複数件の場合は利用可能なworker枠までwaveを構成し、
上流追随済みのcleanな基準から計画ごとに別worktreeを作成する。固定件数の上限は設けない。
dirty差分を前提とする計画は並列対象に含めない。

各laneのprepareでは、実装、近接検証、commitまでを並列に行う。1つのworktreeへ書き込む主体は1つだけとし、
計画作成とreviewerは読み取り専用とする。laneが失敗した場合は調査可能な状態でworktreeを保全する。

finalizeは1件ずつ次の順に直列実行する。

1. 最新の基準へ追随し、競合を解消する
2. 最終検証する
3. 計画準拠レビューと独立レビューを実行する
4. 実在する未解決指摘が無いことを確認する
5. push、CI通過確認、`atk mq adopt`を実行する
6. 成功したlaneのworktreeだけをcleanupする

finalize後に対象commitを変更した場合は、最終検証と二系統レビューを再実行する。
ユーザー合意を覆す必要が生じた場合は自動反映せず、モード別の確認経路へ送る。

## 複数リポジトリ横断作業

作業が複数リポジトリを対象とする場合は、各リポジトリ向けの独立したfeedbackへ分解投入する。
複数リポジトリ間で不可分な同期改訂を要する場合だけ単一計画へ集約する。

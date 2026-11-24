# セッション状態ファイルとフラグ：UWI系

各状態の共通の設計、生存期間、排他、継承は`docs/development/internals-session-state-common.md`にある。

## UWI系

- `uwi_awaiting_answer`: 会話が投入したUWIのうち、回答を待っているもののファイル名の昇順の一覧（`_common/uwi_watch.py`）。
  `atk wi add`がUWIの保存後に、投入元の会話の状態へ既存の`update_state`の排他で追記する。
  投入元は`submitter_session`と同じ値であり、委譲先が投入したUWIは委譲の最上位の会話へ記録される。
  PostToolUseはメインの呼び出しで一覧のファイルだけを読み、回答済みのUWIを通知して一覧から外し、採用・不採用で終了したUWIを通知せずに外す。
  未回答・保留中のUWIと読めないファイルは一覧へ残し、一覧が空の会話はWIの保存先を読まない。
  一覧から外す書き込みに失敗した場合は通知しない。
  セッション終了まで保持する。
- `uwi_answered_by_repo`・`uwi_fingerprint_by_repo`と、改名前の`tbd_answered_by_repo`・`tbd_fingerprint_by_repo`: 全WIを走査して回答を検出していた版が記録したキー。
  PostToolUseは回答済みの基準値（`{エージェント識別子: {対象リポジトリID: ファイル名一覧}}`）を見つけた時に、基準値にある対象リポジトリを1回だけ走査する。
  その会話が投入した未回答のUWIと、回答済みで基準値に無いUWIを`uwi_awaiting_answer`へ引き継ぎ、4つのキーを削除する。
  PostToolUseはこの4つのキーを読み取りと削除だけに使う。
- `uwi_adopt_retry_at`: 上の引き継ぎをやり直してよい時刻（エポック秒）。
  WIの保存先を解決できない場合と走査が不完全な場合に、PostToolUseが60秒後の時刻を書き、4つのキーを保つ。
  その時刻まではツール実行のたびの走査を控え、`uwi_awaiting_answer`のファイルだけを確かめる。
  引き継ぎの完了時に4つのキーと併せて削除する。

## 具体的な操作の承認

`_atk/wi/operation_permissions.py`は既存の`update_state`の排他で次の状態を更新する。

- `operation_permission_denials`: PermissionDeniedが記録する要求ID、実入力、cwd・repo、拒否理由と実効設定の版・指紋。
  直近64要求を保持し、許可は拒否時と同じ入力・範囲に限って適用する。
- `direct_operation_approvals`: AskUserQuestionの実回答と、表示した要求全体の対応。
  承認元にはモデルの入力に含まれるanswersを除く実回答を使い、同じ回答の再保存でも消費済み・撤回済みの状態を保持する。
- `operation_permission_results`: 許可を適用したtool_use_id別の出所と応答到着の記録。
  直近64件を保持し、`authorized`、`returned`、`failed`を区別する。
  `returned`が表すのは応答の到着だけであり、業務処理の成否は別に確認する。

UWI側の`permission_request`は実入力の型を保つJSON文字列、`operation_approval`は要求・質問・実回答の指紋と消費状態を持つ。
回答保存・消費・取消は既存のWI保存排他と同期を使う。
構造化欄の無い旧UWIは、質問の逐語入力・ツール・対象と明示的な許可回答が、保留中の1操作だけを指す場合に単発で利用する。
`legacy_operation_approval`へ質問・回答・実入力の指紋と消費済み状態を保存し、同じ旧回答の利用を1回に制限する。
自動適用する範囲は`once`、または同じ入力で有限回の試行を認める`count:N`とし、範囲変更では要求IDを更新する。
自動許可は未消費の承認があり、現在の実効設定が拒否時と同じ場合に限る。
MCPが公開する操作は、状態取得・要求準備・所有する委譲先からの引継ぎ・取消に限定する。
これらの状態の継承・回収は共通のセッション状態と同じ寿命を使う。

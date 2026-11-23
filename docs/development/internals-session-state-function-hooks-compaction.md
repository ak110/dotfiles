# 背景待機の状態と再起動

`ui_requests.tsx`は現在の背景タスクを共有状態の`backgroundWaits`に登録する。
`compact_conversation.ts`は完了・失敗・強制終了の通知から該当IDを取り除く。
`runtime_restart.ts`はユーザーの再起動時にこの一覧から、所有する子担当を持つ単独の`atk agents wait`だけを停止する。
この一覧から会話圧縮は予約しない。

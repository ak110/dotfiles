# 実装配置と共有状態の非同期境界の設計記録

[設計記録の索引](design.md)

## agents_serverの実装の配置

目的はMCPツールの公開面、sessionの管理、engine別のbackendおよび共有状態の読み書きを責務ごとのモジュールへ分けることである。
変更が1つの責務の範囲で完結するようにする。

責務の分け方は[manager.py](../../agent-toolkit/agent_toolkit/_agents_server/manager.py)にある。
各責務が同じsession集合と条件変数を共有するため、状態を持つ1つのインスタンスの中でモジュールだけを分ける。
責務ごとに別のオブジェクトへ分け、状態を引数で渡し合う案は採らない。
session集合・待機中の再開・条件変数を全てのオブジェクトが参照するため、受け渡しの箇所が増えるだけで依存は減らない。

engineごとの差は[backends.py](../../agent-toolkit/agent_toolkit/_agents_server/backends.py)に閉じ、manager側にengine名の判定を重ねない。

## 共有状態の非同期境界

イベントループのスレッドで同期のロック取得・Event待機・join・I/Oの完了を待つと、同じloopの独立した要求、通知、heartbeatと停止要求を処理できない。
このため、同期操作をworkerへ分離し、loop側はawaitで完了を待つ。
保存と監視の完了を待つのは、登録・保存の順序と終了時の結果の一度だけの回収を保ち、停止時に全threadの終了と出力の完了を確認するためである。
待機を打ち切る案と、保存や監視を背景化したまま返る案ではこれらの完了を保証できないため採らない。

## atkの起動形と観測

WindowsのPowerShellで実行した`atk agents wait`もPostToolUseが観測の試みとして記録し、`pending_observation`を解消する。
待機の出力追跡は分解済みの実行トークンから起動を判定し、引数へ引用されたコマンドの文字列を実行の根拠へ含めない。

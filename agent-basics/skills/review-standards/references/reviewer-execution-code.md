# 実行レビュー：コードとOS境界の受入試験

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

テストの有効性と内部変更への耐性、観測手段・環境の十分性は、`agent-basics:writing-standards/references/testing.md`の冒頭、「検証単位の選定」「期待値の出所」「分岐条件の効果の裏付け」で評価する。
入力の生産・変換から最終消費まで実行した範囲と、fixtureを対象版の標本へ対応付けた根拠を読み、差し替えた境界までの成功と全体の保証を区別する。
追加・変更した試験自体にも作成規範を適用し、`../../writing-standards/references/testing-selection.md`と`../../writing-standards/references/testing-stability.md`の全文で必要性・決定性を評価する。
対象外の外部照会、固定時間だけの同期、製品本文を書き換えて配布本文と異なる処理を検証する構成を確認する。
条件ポーリング内の短いsleep、必要な実OS境界、単なるデータ置換とは意味で区別し、より軽い単体と最小の実境界を比較する。
OS分岐と保存済み成功の適用では、同資料「プラットフォーム分岐の検証」に従い、モジュールの判定値、実ホストOS、外部実行体・OS APIをfixtureと実入力から比較する。
共有モジュールの変更範囲、実行したOS固有の変換・副作用、開発環境と配布環境の初期状態を対応付ける。
試験名やWindowsパラメーター、型・全体試験の成功、レビュー回数だけでOS境界を受理せず、未確認の境界を残す

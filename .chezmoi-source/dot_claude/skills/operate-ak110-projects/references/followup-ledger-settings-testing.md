# 追従台帳：CIとテスト

## CIキャッシュを依存入力に対応させる

- 項目ID: ci-cache-by-dependency-inputs
- 実施日: 2026年11月
- 内容: 必要なサブディレクトリだけを保存し、依存入力が同じ実行では保存を省く
- 理由: イメージ同梱分の再圧縮と、不要になった版の累積を減らす
- 当てはまる条件: GitHub Actionsで実行ごとに変わるキーや前方一致の復元を使い、キャッシュを毎回保存している
- 適用方法: 推奨ガイドのCI節に沿って増分と再取得時間を測り、対象・入力ハッシュ・イメージ識別・保存するジョブを決め、同じ入力の2回目で保存が無いことを確認する
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「CI」と`.github/workflows/ci.yaml`
- 適用済みの見分け方: キーが依存入力を反映し、対象が必要な領域に限られ、完全一致の復元後は保存を省くことがActionsログから分かる

## 検証範囲を保ってCIを短縮する

- 項目ID: ci-runtime-and-job-structure
- 実施日: 2026年10月〜11月
- 内容: ジョブの責務分離、重い検査の独立実行、変更領域の選択と所要基準を整える
- 理由: 同じ準備や検査の重複と資源競合を減らし、CIの待ち時間を抑える
- 当てはまる条件: 直列実行、重い検査の同居、無関係な領域の検査、所要基準の欠如のいずれかがCIにある
- 適用方法: 律速と準備・本体・後処理を測り、検査集合を保って分割し、未知の変更は全体へ戻し、失敗を必須checkへ伝え、プロジェクトの実績から所要基準を定める
- 参照先: `github.com/ak110/dotfiles`の`.github/workflows/ci.yaml`と`.claude/skills/develop-dotfiles/references/verification-values-speed.md`
- 適用済みの見分け方: 必須検査の集合と失敗伝播が検証され、変更領域の選択理由と、全体・律速ジョブの所要を基準と比較できる

## 試験の途中終了でも失敗名を保存する

- 項目ID: preserve-test-failures-before-interruption
- 実施日: 2026年10月
- 内容: 試験ごとの名前と結果を逐次出力し、中断前に完了した失敗を保存ログから識別できるようにする
- 理由: 最後の詳細表示へ到達しない場合にも、調査と再検証の対象を選べるようにする
- 当てはまる条件: CIで失敗した試験名を実行末尾の出力だけから得る
- 適用方法: 詳細進捗の出力を既存の収集へ加え、巨大な自動IDは短い明示IDへ直し、途中終了のログでも失敗名・外側終了状態・未完了範囲を区別する
- 参照先: `github.com/ak110/dotfiles`の`.github/workflows/ci.yaml`と`.github/actions/prepare-python-check/action.yml`
- 適用済みの見分け方: pytestなら詳細表示の引数があり、打ち切った保存ログにも完了済みの失敗node IDと結果が残る

## 任意pytestをマーカーで選択する

- 項目ID: pytest-explicit-optional-marker-selection
- 実施日: 2026年10月
- 内容: 任意実行の試験群を環境変数の全件skipからマーカーの選択へ移す
- 理由: 実行条件の指定漏れで1件も実行しなかった検証を、成功と取り違えないようにする
- 当てはまる条件: 環境変数が無いと試験群全体がskipされ、pytestが終了0になる
- 適用方法: 試験へ専用マーカーを付け、通常設定で除外し、明示実行のタスクから選択し、選択0件の非0終了と明示実行の成功を確認する
- 参照先: `github.com/ak110/dotfiles`の`pyproject.toml`、`agent-toolkit/pyproject.toml`、`Makefile`
- 適用済みの見分け方: 任意指定の欠落を全件skipで扱わず、通常実行では除外し、該当ファイルだけを無指定で起動した場合は収集0件の非0になる

## LinuxからWindows向けにビルドする

- 項目ID: linux-windows-cross-build-setup
- 実施日: 2026年10月
- 内容: Linuxの環境構築を1タスクにまとめ、Windows向けのビルドと可能な検査を実行する
- 理由: Windows専用アプリでもLinuxで開発し、コンパイルや静的検査の不良を早く検出する
- 当てはまる条件: Windows向けの.NETまたはRust製品をLinuxから開発する
- 適用方法: .NETはWindows targetingの有効化、Rustはcargo-xwinなどを比較し、setup・test・buildへ接続し、Linuxで実行できない試験や除外機能を明示してWindows CIで検証する
- 参照先: `github.com/ak110/lc`の`Directory.Build.props`と`mise.toml`、`github.com/ak110/gv`の`mise.toml`と`scripts/setup_linux.py`
- 適用済みの見分け方: `setup-linux`後に通常のtest・buildタスクが成功し、Windows用exeを生成でき、Linuxで未実行の試験はWindows CIが担う

## Windowsの試験反復をCIへ分ける

- 項目ID: windows-ui-test-repetition-in-ci
- 実施日: 2026年10月
- 内容: 対話デスクトップの資源を使う試験の性質と、ローカル検証・CI反復の使い分けを記す
- 理由: ウィンドウの開閉などによって、対話中のユーザーの作業を妨げることを防ぐ
- 当てはまる条件: 通常テストが実ウィンドウ、WinForms、Direct2DなどのOS・UI資源を生成する
- 適用方法: `AGENTS.md`へ対象試験と影響を記し、ローカルは通常検証に必要な回数とし、反復測定や反復再現を一時ブランチのWindows CIへ割り当てる
- 参照先: `github.com/ak110/gv`と`github.com/ak110/lc`の`AGENTS.md`にある注意点の章
- 適用済みの見分け方: 試験の実行前に読む手順から、非表示の実UI資源も含めた反復先と、通常の単発検証を継続できることが分かる

## テストの配置と準備処理を整理する

- 項目ID: test-layout-and-shared-setup
- 実施日: 2026年10月
- 内容: 実装に対応するテストの配置・命名・実行環境をそろえ、準備と補助処理の重複・不足を直す
- 理由: 対応する試験を見つけやすくし、準備の複製や不要に重い実行環境を減らす
- 当てはまる条件: 実装の構成と配置規則が合わない、または準備処理や補助関数が複製されている
- 適用方法: 単体・結合・E2Eの配置を定め、混載を主題ごとに分け、準備・後始末を共有し、空きポート・有限の起動待ち・直接依存を使い、不足する主要処理の試験を補う
- 参照先: `github.com/ak110/pyfltr`の`AGENTS.md`と`tests/`、`github.com/ak110/glatasks`の`vitest.config.ts`と`app/tests/helpers/`
- 適用済みの見分け方: 配置から対象実装を引け、純粋関数をE2E環境なしで検証でき、共有の準備を使い、再配置後も既存の入力と期待値が維持される

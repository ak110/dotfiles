# 追従台帳：プロジェクトが持つ設定・CI・文書の改善

区分「設定」は、各プロジェクトが持つ設定、CI、開発手順、文書などの変更が要る改善を記録する。
設定やCIの現在の推奨形は、pyfltrの`docs/guide/recommended.md`と`docs/guide/recommended-nonpython.md`を正とし、台帳へ写さない。
各項目は変更の時期と理由、適用条件、移行作業、現物から確認する方法を示す。

## Markdownを1文ごとに改行する

- 項目ID: markdown-one-sentence-per-line
- 実施日: 2026年11月
- 内容: 既存Markdownを1文ごとに改行し、複数文を1行に書いた箇所を検査する
- 理由: 文単位で差分を読み、行数から文書の大きさを把握できるようにする
- 当てはまる条件: 日本語のMarkdown文書を持つ
- 適用方法: 適用方法は公開版では未整備であり、現在はdotfilesの`scripts/check_markdown_structure.py`と`pyproject.toml`のカスタムコマンド登録を参照し、既存文書の内容を保って改行する
- 参照先: `github.com/ak110/dotfiles`の`scripts/check_markdown_structure.py`と`pyproject.toml`
- 適用済みの見分け方: 地の文と箇条書きが1文1行で、表・コード・逐語引用を除く複数文の行をCIの検査が拒否する

## 全角丸括弧の入れ子を解消する

- 項目ID: markdown-no-nested-fullwidth-parentheses
- 実施日: 2026年11月
- 内容: 全角丸括弧の入れ子を直し、行をまたぐ入れ子も検査する
- 理由: 補足の範囲を読み取りやすくし、同じ表記の崩れを防ぐ
- 当てはまる条件: 日本語のMarkdown文書を持つ
- 適用方法: 適用方法は公開版では未整備であり、現在はdotfilesの`scripts/check_markdown_structure.py`と`pyproject.toml`のカスタムコマンド登録を参照し、名称を保って外側の括弧を読点や別文へ直す
- 参照先: `github.com/ak110/dotfiles`の`scripts/check_markdown_structure.py`と`pyproject.toml`
- 適用済みの見分け方: コードや引用の外に全角丸括弧の入れ子が無く、表・見出し・行をまたぐ入れ子を検査が報告する

## 自然な日本語を保つtextlint設定へ改める

- 項目ID: textlint-natural-japanese
- 実施日: 2026年10月
- 内容: 読点の上限と冗長表現の辞書を見直す
- 理由: 自然な列挙や「操作を実行する」を、検査のために不自然な文へ書き換える往復を減らす
- 当てはまる条件: textlintの設定で`preset-ja-technical-writing`を使う
- 適用方法: 推奨ガイドの`.textlintrc.yaml`節に沿って該当する閾値と辞書だけを変更し、読点の境界と通常の文を検査する
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「.textlintrc.yaml」と、`github.com/ak110/lc`の`.textlintrc.yaml`
- 適用済みの見分け方: `max-ten.max`が`5`、`ja-no-redundant-expression.dictOptions.dict6.disabled`が`true`で、他の有用な検査は維持されている

## 検証前に仮想環境を同期しPython版を維持する

- 項目ID: uv-sync-before-verification
- 実施日: 2026年10月
- 内容: 検証前に仮想環境をロックと基準Python版へ厳密同期し、後続の検査でもその環境を使う
- 理由: 余分なパッケージや親の`UV_PYTHON`による版の再選択で、検査結果が変わることを防ぐ
- 当てはまる条件: `uv.lock`と基準Python版を持ち、仮想環境を検証へ再利用する
- 適用方法: 推奨ガイドの同期付きMakefile例を使い、検証の前提に厳密同期を置き、後続起動への`UV_PYTHON`の継承を外す
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「Makefile」と、`github.com/ak110/pytilpack`の`Makefile`
- 適用済みの見分け方: `format`・`test`の前に全groups・extrasと基準版へ同期し、異なる`UV_PYTHON`を渡しても検査後のPython版と依存集合が一致する

## CIキャッシュを依存入力に対応させる

- 項目ID: ci-cache-by-dependency-inputs
- 実施日: 2026年11月
- 内容: 必要なサブディレクトリだけを保存し、依存入力が同じ実行では保存を省く
- 理由: イメージ同梱分の再圧縮と、不要になった版の累積を減らす
- 当てはまる条件: GitHub Actionsで実行ごとに変わるキーや前方一致の復元を使い、キャッシュを毎回保存している
- 適用方法: 推奨ガイドのCI節に沿って増分と再取得時間を測り、対象・入力ハッシュ・イメージ識別・保存するジョブを決め、同じ入力の2回目で保存が無いことを確認する
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「CI」と`.github/workflows/ci.yaml`
- 適用済みの見分け方: キーが依存入力を反映し、対象が必要な領域に限られ、完全一致の復元後は保存を省くことがActionsログから分かる

## actionlintが使うShellCheckを宣言する

- 項目ID: mise-shellcheck-for-actionlint
- 実施日: 2026年11月
- 内容: ワークフロー内のシェル検査に使うShellCheckをプロジェクトのmise設定へ加える
- 理由: グローバル設定やCIイメージの同梱ツールへ依存せず、ローカルでも検査できるようにする
- 当てはまる条件: actionlintを使い、ShellCheckをプロジェクトのツール設定へ宣言していない
- 適用方法: 推奨ガイドのmise設定例に従いShellCheckを宣言し、通常のactionlintからワークフローのシェル検査を実行する
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「mise.toml」と`docs/guide/recommended-nonpython.md`「miseで配置を変えるツール設定」
- 適用済みの見分け方: `mise.toml`の`[tools]`に`shellcheck`があり、actionlintから起動できる

## GitHub Actions更新用のpinactを宣言する

- 項目ID: mise-pinact-for-action-updates
- 実施日: 2026年10月
- 内容: 更新処理が使うpinactをプロジェクトのmise設定で解決する
- 理由: ホストの共有設定を変えても、プロジェクトの更新コマンドを実行できるようにする
- 当てはまる条件: Makefileやmiseの更新タスクがpinactを起動する
- 適用方法: `mise.toml`へpinactを宣言し、追跡するmiseロックがあれば更新し、通常のActions更新経路で解決を確かめる
- 参照先: `github.com/ak110/pyfltr`と`github.com/ak110/glatasks`の`mise.toml`および`Makefile`
- 適用済みの見分け方: 更新コマンドの呼出先pinactがプロジェクトのmise宣言から解決され、個人のグローバル宣言を要しない

## Corepack同梱に頼らないpnpm運用へ移る

- 項目ID: pnpm-independent-bootstrap
- 実施日: 2026年11月
- 内容: pnpmの初回導入、版選択、版更新をCorepack同梱に頼らない手順へそろえる
- 理由: Node.jsのCorepack同梱終了後も、版固定・integrity検証・公開待機を保つ
- 当てはまる条件: pnpmを使い、導入・CI・更新手順にCorepackを前提とする記述がある
- 適用方法: gv・lcの導入例を基に初回導入とpnpm自身の版管理を分け、setup・hook・CI・監査・更新から同じ版を使うように移行する
- 参照先: `github.com/ak110/gv`と`github.com/ak110/lc`の`mise.toml`、`package.json`、`pnpm-lock.yaml`、`pnpm-workspace.yaml`
- 適用済みの見分け方: Corepack無しで導入でき、`packageManager`とlock先頭の版記録が一致し、`pnpm self-update`が両方を更新し、公開待機が維持される

## 依存の定期脆弱性監査を加える

- 項目ID: scheduled-dependency-audit
- 実施日: 2026年10月〜11月
- 内容: 配布物と開発用依存の監査対象、定期実行、結果の確認先をそろえる
- 理由: 依存を変更しない期間に公表された脆弱性も検出する
- 当てはまる条件: 外部パッケージに依存し、定期監査に含まれない依存がある
- 適用方法: 使用するパッケージ管理系ごとに監査対象を列挙し、定期・手動起動のworkflowと方針を用意し、検出と監査自体の失敗を区別する
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「依存の脆弱性監査の有効化（任意）」と、`github.com/ak110/gv`・`github.com/ak110/lc`の`.github/workflows/audit.yaml`
- 適用済みの見分け方: scheduleと手動起動があり、実行時・開発用の監査対象と結果の確認先が開発文書に記され、未実行や取得失敗を成功にしない

## miseロックを版管理する

- 項目ID: version-control-mise-lock
- 実施日: 2026年10月
- 内容: 開発ツールの解決版をmiseロックへ記録し、通常の依存更新へ組み込む
- 理由: `latest`や`lts`を含む環境を再現し、未追跡の生成物による公開停止を防ぐ
- 当てはまる条件: `mise.toml`を版管理し、対応するロックを追跡していない
- 適用方法: miseのロックと参照する副生成物を追跡し、形式を読める最低mise版を宣言し、既存の更新タスクでロックも更新する
- 参照先: `github.com/ak110/gv`と`github.com/ak110/lc`の`mise.toml`、`mise.lock`
- 適用済みの見分け方: Gitがロックと必要な副生成物を追跡し、空の環境で解決でき、ツール変更が通常の更新でロック差分になる

## 文書lintのJavaScript依存を修正する

- 項目ID: javascript-doc-lint-dependency-fixes
- 実施日: 2026年10月
- 内容: 開発用JavaScript依存の脆弱性を、更新・対象を絞った上書き・追跡パッチで解消する
- 理由: 開発用途の依存でも脆弱な実装を保持せず、監査と実際の修正を対応付ける
- 当てはまる条件: 文書lintなどのJavaScript依存をプロジェクトのpnpmで管理する
- 適用方法: 解決経路を調べ、修正版へ更新し、通常更新で到達しない場合は互換性を確認して上書きやパッチを適用し、再導入後の実体と文書lintを確かめる
- 参照先: `github.com/ak110/gv`の`pnpm-workspace.yaml`、`pnpm-lock.yaml`、`patches/braces@3.0.3.patch`
- 適用済みの見分け方: 該当勧告の修正が全解決先へ適用され、上書き・パッチに理由と撤去条件があり、監査除外だけを修正済みの根拠にしていない

## 遅延importの方針をRuffで検査する

- 項目ID: ruff-lazy-import-policy
- 実施日: 2026年10月
- 内容: Ruff 0.17以降のTID254・TID255を、プロジェクトの遅延import方針へ対応付ける
- 理由: 遅延指定の列挙漏れと即時解決を、方針に応じて検出する
- 当てはまる条件: Pythonコードで遅延importや`__lazy_modules__`の使い方を定めている
- 適用方法: TID254と`require-lazy`で列挙方針を検査し、即時解決も禁止する場合はTID255が抑制されない別設定で検査し、必要な通常importだけに理由付きの例外を置く
- 参照先: `github.com/ak110/pytilpack`の`pyproject.toml`と、`docs/development/architecture.md`の依存の向きとimportを説明する節
- 適用済みの見分け方: Ruffの実効版と設定が方針に合い、列挙漏れの入力を拒否し、即時解決を禁じる方針ならその入力も拒否する

## VitePressのビルド後に全リンクを検査する

- 項目ID: vitepress-built-site-link-check
- 実施日: 2026年10月
- 内容: 生成HTMLのページ・見出し・外部リンクを公開前に検査し、見出しIDをNFCへそろえる
- 理由: frontmatterとナビゲーションのリンクや、日本語見出しの正規化の差をソースの検査だけでは検出できない
- 当てはまる条件: VitePressで文書サイトを生成する
- 適用方法: ビルド後・公開前にlycheeの見出し検査を置き、公開baseと検査rootを対応付け、slugifyの結果をNFCへ正規化し、壊したページと見出しを拒否することを確かめる
- 参照先: `github.com/ak110/lc`の`.github/workflows/docs.yaml`と、`github.com/ak110/gv`の`docs/.vitepress/config.ts`
- 適用済みの見分け方: 全HTMLを`--include-fragments`付きで検査し、失敗時は公開されず、濁点を含む見出しのIDとリンクが一致する

## Markdownの内部アンカーを検査する

- 項目ID: markdown-internal-anchor-check
- 実施日: 2026年10月
- 内容: 同じファイル内と別Markdownへの見出し付きリンクが実在する見出しへ解決することを検査する
- 理由: 見出しの記号・英大小文字・重複によるIDの違いで、索引から節へ移動できないことを防ぐ
- 当てはまる条件: リポジトリ内のMarkdownへ見出し付きリンクを書く
- 適用方法: 対象の描画先と同じ見出しIDの生成規則でリンクを照合し、コード内を除外し、不一致のファイル・行・リンク先を検査で報告する
- 参照先: `github.com/ak110/dotfiles`の`markdown_link_anchor_invariant_test.py`
- 適用済みの見分け方: リポジトリ内の全Markdownリンクを検査し、見出しの削除・改名・記号を含む誤ったアンカーを検出する

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

## 共通品質基準の参照をagent-basicsへ移す

- 項目ID: project-quality-references-agent-basics
- 実施日: 2026年10月
- 内容: プロジェクトの品質資料の参照、プラグイン設定、導入案内、読込契機をagent-basicsへそろえる
- 理由: 成果物の品質基準を、WIや委譲などの運用から独立して利用できるようにする
- 当てはまる条件: `AGENTS.md`や固有スキルが、agent-toolkitの旧品質資料を参照する
- 適用方法: 作成・設計・テストは`agent-basics:writing-standards`、レビューは`agent-basics:review-standards`へ接続し、各ホストの導入設定と既存の固有資料への参照を確認する
- 参照先: `github.com/ak110/pyfltr`の`AGENTS.md`、`.claude/settings.json`、`.codex/config.toml`、`docs/development/development.md`
- 適用済みの見分け方: 共通品質基準の提供元と導入案内がagent-basicsで一致し、専用担当を含め必要な資料へ到達できる

## Python 3.15を対応版へ加える

- 項目ID: python-three-fifteen-support
- 実施日: 2026年10月
- 内容: 対応版、CI、基準の静的検査環境とローカル環境をPython 3.15へ対応させる
- 理由: 新しいPython版の実行時互換性と、基準版での検証を確認する
- 当てはまる条件: Pythonプロジェクトで、対応版にPython 3.15が含まれない
- 適用方法: 依存の対応を調べ、対応版の宣言とCI行列へ加え、基準版を変更する場合は`.python-version`・全検査・文書や公開の実行版もそろえる
- 参照先: `github.com/ak110/pyfltr`の`pyproject.toml`、`.python-version`、`.github/workflows/ci.yaml`と`docs/guide/recommended.md`「CI」
- 適用済みの見分け方: 対応版の宣言とCIに3.15があり、依存を導入して試験を実行でき、全検査を担う版と開発環境が一致する

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

## 文書へ写した実装の一覧を検査する

- 項目ID: documentation-implementation-list-consistency
- 実施日: 2026年10月
- 内容: 設定キー・選択肢・ツールなど、実装が要素を決める文書の一覧を実装とそろえ、整合テストを加える
- 理由: 機能追加に一覧が追随せず、利用できる機能や既定値を誤って伝えることを防ぐ
- 当てはまる条件: エンドユーザー向け文書に、実装が決める集合を閉じた一覧として手書きしている
- 適用方法: 文書の一覧を列挙し、定義元から取得した集合と比較するテストを置き、新しい一覧の追加手順にも同じ検証を含める
- 参照先: `github.com/ak110/pyfltr`の`tests/integration/documentation_test.py`と`.claude/skills/ssot/SKILL.md`
- 適用済みの見分け方: 一覧ごとに実装の定義と比較するテストがあり、要素を文書か実装の片方だけで変えると失敗する

## 文書を読み手と責務ごとに再編する

- 項目ID: documentation-responsibility-structure
- 実施日: 2026年10月
- 内容: エンドユーザー向け・開発者向け・エージェント向けの文書を、目的と問いごとの構成へ整理する
- 理由: 同じ説明の重複、内部仕様の混在、実装と合わない説明や参照を減らす
- 当てはまる条件: 読み手の違う情報が混在する文書や、同じ説明を繰り返す文書がある
- 適用方法: 各文書の読み手と主要操作を定め、操作案内・保守理由・作業指示の配置を分け、重複を参照へ変え、移動先・索引・アンカー・更新手順をそろえる
- 参照先: `github.com/ak110/pytilpack`の`docs/guide/index.md`・`docs/development/architecture.md`・`AGENTS.md`と、`github.com/ak110/dotfiles`の`docs/guide/`
- 適用済みの見分け方: 見出しが読み手の問いへ対応し、主要操作はガイド内で分かり、内部手順と保守理由は対応する資料へ分かれ、参照が解決する

## テストの配置と準備処理を整理する

- 項目ID: test-layout-and-shared-setup
- 実施日: 2026年10月
- 内容: 実装に対応するテストの配置・命名・実行環境をそろえ、準備と補助処理の重複・不足を直す
- 理由: 対応する試験を見つけやすくし、準備の複製や不要に重い実行環境を減らす
- 当てはまる条件: 実装の構成と配置規則が合わない、または準備処理や補助関数が複製されている
- 適用方法: 単体・結合・E2Eの配置を定め、混載を主題ごとに分け、準備・後始末を共有し、空きポート・有限の起動待ち・直接依存を使い、不足する主要処理の試験を補う
- 参照先: `github.com/ak110/pyfltr`の`AGENTS.md`と`tests/`、`github.com/ak110/glatasks`の`vitest.config.ts`と`app/tests/helpers/`
- 適用済みの見分け方: 配置から対象実装を引け、純粋関数をE2E環境なしで検証でき、共有の準備を使い、再配置後も既存の入力と期待値が維持される

## 手順スキルを動詞始まりの名前にする

- 項目ID: procedural-skill-leading-verb
- 実施日: 2026年10月〜11月
- 内容: 順序だった作業手順を中心に持つスキルを、動詞で始まる名前へ改める
- 理由: 名前から何をするスキルかを読み取れるようにする
- 当てはまる条件: プロジェクト固有のスキルに、作業手順を中心とするものがある
- 適用方法: 手順を担うスキルを分類し、ディレクトリ名・frontmatter・呼出名・参照を一緒に変え、基準や契約を提供するスキルと機械用の別識別子は区別する
- 参照先: `github.com/ak110/pyfltr`の`.claude/skills/add-pyfltr-tool/SKILL.md`と`AGENTS.md`
- 適用済みの見分け方: 手順スキルの名前が動詞で始まり、ディレクトリ・`name`・呼出元が一致し、旧名の呼出しや不要な中継が残らない

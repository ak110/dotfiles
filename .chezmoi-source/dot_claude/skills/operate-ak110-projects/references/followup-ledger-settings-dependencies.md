# 追従台帳：依存と開発ツール

## 検証前に仮想環境を同期しPython版を維持する

- 項目ID: uv-sync-before-verification
- 実施日: 2026年10月
- 内容: 検証前に仮想環境をロックと基準Python版へ厳密同期し、後続の検査でもその環境を使う
- 理由: 余分なパッケージや親の`UV_PYTHON`による版の再選択で、検査結果が変わることを防ぐ
- 当てはまる条件: `uv.lock`と基準Python版を持ち、仮想環境を検証へ再利用する
- 適用方法: 推奨ガイドの同期付きMakefile例を使い、検証の前提に厳密同期を置き、後続起動への`UV_PYTHON`の継承を外す
- 参照先: `github.com/ak110/pyfltr`の`docs/guide/recommended.md`「Makefile」と、`github.com/ak110/pytilpack`の`Makefile`
- 適用済みの見分け方: `format`・`test`の前に全groups・extrasと基準版へ同期し、異なる`UV_PYTHON`を渡しても検査後のPython版と依存集合が一致する

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
- 適用済みの見分け方: 更新コマンドの呼出先pinactがプロジェクトのmise宣言から解決され、個人のグローバル宣言なしで動く

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

## Python 3.15を対応版へ加える

- 項目ID: python-three-fifteen-support
- 実施日: 2026年10月
- 内容: 対応版、CI、基準の静的検査環境とローカル環境をPython 3.15へ対応させる
- 理由: 新しいPython版の実行時互換性と、基準版での検証を確認する
- 当てはまる条件: Pythonプロジェクトで、対応版にPython 3.15が含まれない
- 適用方法: 依存の対応を調べ、対応版の宣言とCI行列へ加え、基準版を変更する場合は`.python-version`・全検査・文書や公開の実行版もそろえる
- 参照先: `github.com/ak110/pyfltr`の`pyproject.toml`、`.python-version`、`.github/workflows/ci.yaml`と`docs/guide/recommended.md`「CI」
- 適用済みの見分け方: 対応版の宣言とCIに3.15があり、依存を導入して試験を実行でき、全検査を担う版と開発環境が一致する

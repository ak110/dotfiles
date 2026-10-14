# Claude Code auto modeのカスタムルール

本書はauto modeのカスタムルールを追加・編集する手順を扱う。
拒否に遭遇したときの行動手順は`agent-toolkit:user-confirmation-and-report`の`references/permission-denial.md`が定める。

カスタムルールは`~/.claude/settings.json`の`autoMode`配下に自然言語で書く。
`allow`・`soft_deny`・`hard_deny`・`environment`の各配列は設定するとその区分のデフォルト一覧を置き換える。デフォルトを維持して追加するには配列へリテラル文字列`"$defaults"`を含める。
`allow`の許可指示はデフォルトの`soft_deny`判定を上書きする。設定する区分のキーは公式スキーマ（`$schema`のURL）で実在を確認する。

## ルール区分

- `allow`: 明示的に許可する操作
- `soft_deny`: 標準では拒否するが、ユーザー指示や文脈で`clears`される操作
- `hard_deny`: いかなる場合も拒否する操作
- `environment`: 信頼境界（リポジトリ・ドメイン・バケット・サービス）の定義

## CLIサブコマンド

- `claude auto-mode defaults`: デフォルトの全ルールをJSON形式で出力する
- `claude auto-mode config`: 現在有効な設定（デフォルト＋カスタム）を表示する
- `claude auto-mode critique`: カスタムルールをAIがレビューし、曖昧・冗長・偽陽性のリスクを指摘する。出力は非決定的で、exit 0のまま末尾が欠落する場合が観測されているため、ファイルへ保存して欠落を確かめ、欠落時は再実行する

## カスタムルール追加のワークフロー

1. `claude auto-mode config`の有効なルールと配布側の設定ファイルのsourceと、`claude auto-mode defaults`の標準のルールと`clears when ...`の例外条件を読み、誤拒否されている領域を特定する
2. 提案と有効なルールを、ラベルと文面ではなく対象操作、適用条件および信頼境界の包含関係で比べ、未被覆の操作だけを追加対象にする。同じ範囲を持つローカルルールの重複撤去は、実効的な許可・拒否範囲と信頼境界を保つなら自ら確定して報告し、セキュリティ効力・認可範囲を変える場合やユーザーだけの選好が残る場合は`agent-toolkit:user-confirmation-and-report`「確認要否の判定」に従って確認する。権限拒否の迂回には用いない
3. 配布側の設定ファイルがあればそのsourceを、無ければ`~/.claude/settings.json`を編集し、`autoMode.allow`へ追加する。偽陽性として実行を承認した後は、`permission-denial.md`「偽陽性と判断できる拒否への対応」の手順6の分類（環境固有・汎用など）をこの一般則より優先する
4. 再ロード後に`claude auto-mode config`でその項目が有効なことと、`"$defaults"`を含む区分で標準の項目の件数が維持されていることを確認する
5. `claude auto-mode critique`の指摘は変更前後で対応付けられる場合だけ新規・悪化差分を評価する。全件採用を必須とせず、`agent-toolkit:review-standards`の問題と手段の比例性で採否を決める。配布元がある場合は、配布元が所有する契約を既存の対象固有テストで確認する

配布元の`autoMode.allow`からラベルごと廃止したルールは、配列のunionマージのためユーザー環境の設定に残る。廃止したラベルの旧文面を除去する処理を配布処理へ登録する。

認証を要するClaude Codeの検証では`CLAUDE_CONFIG_DIR`を既存の値のまま使い、標準の認証解決を保つ。`--settings <file-or-json>`は既存設定への追加読込として扱う。

## カスタムルール記述の注意点

- 適用対象（対象操作・対象コミットの性質・対象パスなど）を文面に明示する
- 粒度は操作の種別とし、classifierの判別に必要な代表例だけを添える。変化しやすい識別子（サブコマンド名、オプションの完全な綴り、個別ファイルのパス）の列挙は避ける（ユーザー指定）
- 許可する対象の記述だけで構成し、記述の外の操作は`soft_deny`と`hard_deny`の標準の判定に委ねる（ユーザー指定）。対象は実際に行う操作の範囲で書き、remoteのホスト、由来、所有者とユーザー意図による限定は加えず、ユーザー意図の判定はUser Intent Ruleへ委ねる。対象を狭めると手順どおりの操作が拒否されて工程が止まる
- 上書きは`soft_deny`までとし、デフォルトの`hard_deny`領域は維持する（auto modeの安全境界を保つため）
- 信頼対象（個人リポジトリ・信頼ドメインなど）を参照するallowルールは、`autoMode.environment`の信頼境界宣言と整合させる

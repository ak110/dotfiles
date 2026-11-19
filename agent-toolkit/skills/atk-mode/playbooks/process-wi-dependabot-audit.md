# Dependabotアラートの監査

作業`process-wi`の自動コードレビュー監査のうち、Dependabotアラートの取得、判定、却下と、判定の記録の手順書である。
`agent-toolkit:atk-mode/playbooks/process-wi-review-audit.md`で起動された監査担当が、同書「判定済みの記録」までのCopilotのレビューの監査と同じ実行で行う。
監査担当は対象リポジトリの成果物を変えず、アラートの却下だけを本書の範囲で行う。
終了条件は、判定した全てのアラートを却下か版付きのmarkで記録し、未処置の番号と理由を返却へ含めた時点である。

## Dependabotアラート

対象リポジトリのopenなDependabotアラートを、GitHub APIの`default_branch`が指すブランチの確認版ごとに判定する。
確認版で修正済みのアラートと、マニフェストが存在しないアラートは`inaccurate`で却下する。

### 取得と判定区分

1. 通常は`atk review-audit pending`のJSONの`dependabot`と`counts.dependabot`を入力に使う。
   `dependabot.alerts`の各要素は、番号、`manifest_path`、パッケージ名、エコシステム、修正版（`first_patched_version`。無ければnull）、`category`を持つ。
   `category`は、対象のマニフェストが`default_branch`に無ければ`inaccurate`、あれば`manifest_present`である。
   `dependabot.status`が`disabled`（機能無効）か`unauthorized`（権限不足。`message`に応答本文が入る）なら判定せず、その状態をそのまま返す。
2. pendingのJSONを受け取れない環境では直接取得する。
   各応答はmanaged-tempの中のJSONファイルへ保存し、件数と値は保存したファイルから確かめる。
   1. `gh api --paginate --slurp 'repos/<OWNER>/<REPO>/dependabot/alerts?state=open&per_page=100'`で全ページを取得する。
      HTTP 403のエラー本文が「Dependabot alerts are disabled for this repository.」なら機能無効、それ以外の403なら権限不足とし、判定は行わずに状態を返す。
   2. `gh api 'repos/<OWNER>/<REPO>'`の`default_branch`からブランチ名を取得し、URLエンコードした名前で`gh api 'repos/<OWNER>/<REPO>/commits/<branch名>'`を実行する。
      応答の`sha`を40桁の16進のSHAとして検証し、小文字に正規化して確認版`<SHA>`へ固定する。
   3. `atk review-audit list --repo <OWNER>/<REPO> --revision <SHA>`が返す`dependabot:<番号>`を判定済みとして除く。
      版の無い旧い記録は再判定の対象とする。
   4. 残った各アラートの`dependency.manifest_path`をURLエンコードし、`gh api 'repos/<OWNER>/<REPO>/contents/<manifest_path>?ref=<SHA>'`で取得する。
      HTTP 404だけを不在とし、他の失敗は未完了とする。
      存在する場合は、同じSHAの同じマニフェストと対応するロックファイルから、対象パッケージの解決済みの版を確かめる。
3. 通常取得でopenのアラートが1件以上ある場合、`dependabot.default_branch`は対象のブランチ名、`dependabot.revision`はマニフェストの取得にも使った固定のSHAである。
   openが0件なら版の取得を省き、取得状態と0件を返す（代替取得でも、以降の版の取得・判定・markを省いて取得済みの0件を記録する）。
   判定済みの除外より先にこのSHAを解決し、以降の取得・比較・記録に同じSHAを使う。
   通常取得・代替取得とも、未観測、取得失敗、機能無効、権限不足、取得成功の0件を区別する。
4. 次の表のベースブランチは、対象リポジトリ（起動時の`cwd`）で`process-wi`が処理するブランチ（系統を登録したリポジトリでは処理中の系統のブランチ）を指す。

   | 区分 | 条件 | 処置 |
   | --- | --- | --- |
   | 誤検知 | 固定SHAの`manifest_path`が存在しない | 本書「却下」の処理を行う |
   | 是正済み | 固定SHAの同一マニフェストで対象パッケージの解決済み版が修正版以上 | 本書「却下」の処理を行う |
   | 反映待ち | `default_branch`の確認版は未修正で、ベースブランチだけが修正済み | openを維持し、確認版付きで判定を記録して対象ブランチ更新後に再判定する |
   | 要修正 | 脆弱版が残る、修正版がない、または解決済み版を比較できない | 却下せず、番号、マニフェスト、パッケージ、確認版、修正版と対処案を報告し、確認版付きで判定を記録する |

   修正版との比較は各エコシステムの版の規則に従い、依存の指定の範囲だけでは修正済みとせずに固定SHAの解決済みの版を確かめる。

### 却下

1. 誤検知と是正済みのアラートは`state=dismissed`、`dismissed_reason=inaccurate`で却下する。
   `dismissed_comment`（280文字以内）には、manifest、package、確認SHA、解決済みの版と修正版を書き、不在の場合は確認SHAでmanifestが存在しないことを書く。
   キュー項目の識別子はコメントに含めない。
2. `dismissed_comment`をファイルへ保存し、投稿の直前に`agent-toolkit:atk-mode/playbooks/review-external-writes.md`を読んで指摘を反映する。

   ```sh
   gh api --method PATCH 'repos/<OWNER>/<REPO>/dependabot/alerts/<番号>' -f state=dismissed -f dismissed_reason=inaccurate -F dismissed_comment=@<COMMENT_FILE>
   ```

   処置の完了は手順3で判定する。

3. APIの終了コード0に加えて、応答の`number`が対象の番号、`state`が`dismissed`であることを確かめて処置の完了とする。
   0以外の終了、番号の不一致、状態の不一致の却下と未判定の番号はmarkせず、失敗の内容と次回の再取得を返す。
   誤って却下したアラートは`state=open`へ更新して開き直せる。
4. 却下の認可は、全プロジェクトの`default_branch`が指すブランチで修正済みのアラートと、マニフェストが不在のアラートに適用し、反映待ち・要修正のアラートはopenのまま保つ。
   却下の認可を今後と他プロジェクトにも適用する方針について、ユーザーは「要らないのは閉じちゃって」「今後も＆他プロジェクトも同様の対応をするようにしといてね」と指示した。
   従来の集約に関するユーザーの発言と確認回答は次のとおりである。

   ```text
   dotfiles向けDependabot alertsが出てる気がするんだけどこれって自動で対応されないんだっけ？ (対応して＆対応する仕掛け入れて）
   ```

   <!-- agent-doc-tone: example-mask:start -->
   ```text
   PRレビュー指摘を拾うスクリプトにそっちもチェックして拾う感じにまとめるのがいい気がする。
   ```
   <!-- agent-doc-tone: example-mask:end -->

   ```text
   回答: review-auditへ集約 (Recommended)
   ```

   この回答に従い、Dependabotアラートの取得と判定は`atk review-audit`へ集約している。

### 判定したアラートの記録

1. 処置の完了を確かめた却下と、判定を終えた反映待ち・要修正の番号を、`atk review-audit mark --repo <OWNER>/<REPO> --revision <SHA> dependabot:<番号>...`で記録する。
   確認時に固定した`<SHA>`をそのままmarkへ渡す。
2. 同じ版ではpendingから除かれ、`default_branch`が指すブランチの更新後は同じ番号も判定し直す。
   却下の失敗・未判定の番号は未markのまま残し、単一・all-reposとも既存のアラートの確認間隔で起動し直す。
   要修正の是正かAWIへの記録は委譲元が判断する。

返す報告に含める項目: `agent-toolkit:atk-mode/playbooks/process-wi-review-audit.md`「監査担当の返却契約」のDependabotの各行。

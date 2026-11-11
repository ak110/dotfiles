# 監査記録: セッション記録

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-toolkit/skills/review-session/references/session-records.md：H1直下：2026年9月2日

2026年9月2日に`~/.claude/projects`配下と`~/.codex/sessions`配下の記録で確認した。
再検証は両ディレクトリの記録から対象のキーを検索する。

## agent-toolkit/skills/review-session/references/session-records.md：集計値の典拠：2026年9月3日

本節の記述は2026年9月3日に`agent-toolkit/skills/review-session/scripts/session_review_evidence.py`の`_latest_claude_usages`と`_stats_summary_data`を読んで確認した。
再検証は同じ2つの関数を読む。

## agent-toolkit/skills/review-session/references/session-records.md：スキル起動の判定：2026年9月10日

2026年9月10日に確認した。
`~/.claude/projects`配下でClaude Code 2.1.252と2.1.267の記録を読んだ。
`Skill`ツールの`tool_use`要素が`input.skill`にスキル名を持つことを確認した。
対応する`tool_result`要素の`content`は`Launching skill: <スキル名>`と一致した。
同日、`~/.codex/sessions`配下のrollout記録2169件のうち`agent-toolkit:exit-session`を含む1527件を対象に、先頭120件のレコード種別を集計した。
このスキル名が現れたのは、`world_state`、`developer`ロールの`message`、`compacted`、`function_call_output`および`custom_tool_call_output`だけであった。
同日、codex-cli 0.154.0の同じ2169件に対し、`atk wi process-loop`がCodexへ渡す起動プロンプトの完全一致を数えた。
一致は0件であった。
いずれの記録も最初のuser役レコードの本文は実行環境が挿入する前置きであった。
前置きは``# AGENTS.md instructions``または``<recommended_plugins>``で始まる。
`agent-toolkit:process-wi`をuser役の本文へ含む記録は304件であった。
この304件の`session_meta`を確認すると、`originator`は`agent-toolkit-codex-app-server`が303件、`codex-tui`が1件であった。
再検証はClaude Codeの記録から`Launching skill:`を含む行を1件取得して`tool_result`の構造を確認し、Codexの記録から同じスキル名を含む行を取得してレコード種別を確認する。
あわせてCodexの記録から`atk wi process-loop`が渡す起動プロンプトの完全一致と包含の件数を数える。
user役レコードの`text`の先頭が前置きであることも確認する。

## agent-toolkit/skills/review-session/references/session-records.md：Claude Codeの記録：2026年10月6日

2026年10月6日にClaude Code 2.1.291の本体（`~/.local/share/claude/versions/2.1.291`）を確かめた。
出力の項目`narration_block_indexes`の説明は該当する`thinking`ブロックを「server summaries of the prose between tool calls, not the model's own reasoning」と記す。
本体のバイナリを`rg -a -F 'server summaries of the prose between tool calls'`で検索して再検証する。
検索の手順は`.claude/skills/develop-dotfiles/SKILL.md`「ホスト本体のバイナリの検索」に従う。

## agent-toolkit/skills/review-session/references/session-records.md：暗号化された値：2026年10月7日

2026年10月7日に公式文書「Thinking」（<https://platform.claude.com/docs/en/build-with-claude/thinking>、同日取得）を確かめた。
節「Thinking encryption」は`signature`を「The `signature` field is opaque: don't interpret or parse it.」と定め、`redacted_thinking`の`data`を「opaque and encrypted」と記す。
同じ文書の節「Progress updates between tool calls」は、`display`が`"updates"`のとき「any `thinking` block with non-empty text is a progress update」と記す。
同日、codex-cli 0.160.1の記録（`~/.codex/sessions/2026/10/06/rollout-*.jsonl`）で、`encrypted_content`を持つレコードの`type`が`response_item`、`payload.type`が`reasoning`であることを確かめた。
観測はいずれも値を開かずに行った。
公式文書の2つの節を取得して前掲の記述を確かめ、Codexの記録で`encrypted_content`を持つレコードの`payload.type`を数えて再検証する。

## セッション記録のAPI要求の写し：2026年10月18日

既存調査はClaude Code 2.1.301のリポジトリ外のセッション記録に、同じtool_use IDを持つ`assistant`と`api-request-blob`があると報告した。
写しは`message`に会話と同じ形のcontentを持ち、timestampとuuidを持たない。
公開仕様での形式の保証は未確認である。
今回の検証は、報告された構造を最小化した検体へツール呼出・失敗結果・完了通知の写しを追加し、通常抽出・呼出照会・警告・統計・ユーザー発話の結果が追加前と一致することを確かめる。
生の行のdetail・grepは写しも表示する。
再検証は`session_evidence_record_boundary_test.py`の`test_request_copies_do_not_create_conversation_events`を実行する。

import type { On } from "claude-code";
import { automatedMessage } from "./automated_message.ts";
import { deliverOnce, type LanguageCheck, languageCheckCommand, languageCheckResult, languageCheckUnavailable, withNotice } from "./response_language.ts";

// Claude Codeは、同じ応答でツール呼び出しより前に置いた地の文の一部を、APIが返す要約（progress update）へ置き換えて表示する。
// ツールの入力は要約されないため、ユーザーへ届ける本文をこのツールの`message`で運び、
// 呼び出しの行を`message`の`Markdown`で描く。ツールの登録と表示を同じmoduleに置き、表示できる環境にだけツールが現れるようにする。
// メイン用ツールであり、委譲先への登録と委譲先・サブエージェントからの呼び出しは`register.ts`が除外する。
// 設計と不採用とした代替は`docs/development/design-hooks.md`「send_to_userツール」にある。

const TOOL_NAME = "send_to_user";

const DESCRIPTION = [
  "ユーザーへ届ける本文を、途中・末尾ともにユーザーの画面へそのまま表示する。",
  "質問への回答、確認結果、判明した事実や原因、成果物、進捗、作業完了報告と次の工程の予告に使う。",
  "ユーザーの判断を求める確認には既存の質問手段を使う。推論は送らない。",
].join("");

const INPUT_SCHEMA = {
  type: "object",
  properties: {
    message: { type: "string", description: "ユーザーの画面へそのまま表示するMarkdownの本文。" },
  },
  required: ["message"],
  additionalProperties: false,
};

function fullName(plugin: string): string {
  return `mcp__${plugin}__${TOOL_NAME}`;
}

function messageOf(input: unknown): string | undefined {
  if (input && typeof input === "object" && "message" in input) {
    const message = (input as { message: unknown }).message;
    if (typeof message === "string") return message;
  }
  return undefined;
}

// `register.ts`の`session.start`が`$.tool.register`へ渡すツールの定義。
export const SEND_TO_USER_TOOL = { name: TOOL_NAME, description: DESCRIPTION, inputSchema: INPUT_SCHEMA, isDeferred: false };

// 委譲先からの呼び出しへ`register.ts`が返す拒否の本文。表示済みと応答すると委譲先が終端の返却を省くため、未表示であることと返却先を示す。
export const SEND_TO_USER_DELEGATED_DENY =
  "委譲先・サブエージェントからの呼び出しではユーザーの画面へ表示していない。結果本文は委譲元へ返す必要があるため、" +
  "このツールを使わず、報告する本文を全て最終応答（終端の返却）へ書く。";

const DISPLAYED = "ユーザーの画面へ表示した。";
const INVALID_MESSAGE = automatedMessage("send-to-user", "block", "文字列のmessageが無いため、画面へ本文を表示していない。本文をmessageへ入れて呼び直す。");

export function register(on: On): void {
  // 判定に失敗した場合も、他のツールの呼び出しと本文の表示を止めない。判定不能の診断は会話ごとに1回だけ届ける。
  const delivered = new Map<string, string>();
  on("tool.call", async ($, e, next) => {
    if (e.tool !== fullName($.plugin.name)) return next(e);
    const message = messageOf(e);
    if (message === undefined) return { deny: INVALID_MESSAGE };
    let check: LanguageCheck;
    try {
      check = languageCheckResult($.plugin.root, await $.process.run(languageCheckCommand($.plugin.root, message), { timeoutMs: 30_000 }));
    } catch {
      check = languageCheckUnavailable("言語判定のprocess.runで例外が発生");
    }
    let sessionId: string | undefined;
    try {
      sessionId = await $.session.id();
    } catch {
      sessionId = undefined;
    }
    // 英語主体の検出でも判定は成功しているため、判定不能の既往を破棄してから拒否する。
    const note = deliverOnce(delivered, sessionId, check);
    if (check.kind === "violation") return { deny: check.deny };
    return withNotice({ result: DISPLAYED }, note);
  }).catch(($, e, next) => {
    if (e.tool !== fullName($.plugin.name)) return next(e);
    if (messageOf(e) === undefined) return { deny: INVALID_MESSAGE };
    return withNotice({ result: DISPLAYED }, deliverOnce(delivered, undefined, languageCheckUnavailable(`send_to_userのtool.callで${next.error.kind}が発生`)));
  });

  // 呼び出しの行を`message`の全文の`Markdown`で描く。他のツールの行は描き替えない。
  on("ui.render", { component: "ToolUse" }, ($, e, next) => {
    if (e.props.tool !== fullName($.plugin.name)) return next(e);
    const message = messageOf(e.props.input);
    const { Markdown, Box } = $.ui.resolve(e);
    // 入力は拒否後もToolUseへ渡る。検査中・拒否・中断の行では本文を描かず、
    // tool.callが言語判定を通して返した成功結果の行だけを表示する。
    if (message === undefined || e.props.isRunning || e.props.isErrored || e.props.isInterrupted || e.props.output !== DISPLAYED) {
      return <Box />;
    }
    return <Markdown text={message} />;
  });

  // 成功結果はモデルへ返し、画面には本文の行だけを残す。エラーは後続の描画で示す。
  on("ui.render", { component: "ToolResult" }, ($, e, next) => {
    if (e.props.tool !== fullName($.plugin.name) || e.props.isErrored) return next(e);
    const { Box } = $.ui.resolve(e);
    return <Box />;
  });
}

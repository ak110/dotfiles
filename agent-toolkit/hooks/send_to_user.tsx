import type { On } from "claude-code";

// Claude Codeは、同じ応答でツール呼び出しより前に置いた地の文の一部を、APIが返す要約（progress update）へ置き換えて表示する。
// ツールの入力は要約されないため、ユーザーへ届ける本文をこのツールの`message`で運び、
// 呼び出しの行を`message`の`Markdown`で描く。ツールの登録と表示を同じmoduleに置き、表示できる環境にだけツールが現れるようにする。
// 設計と不採用とした代替は`docs/development/design-hooks.md`「send_to_userツール」にある。

const TOOL_NAME = "send_to_user";

const DESCRIPTION = [
  "ユーザーへ届ける本文を、途中・末尾ともにユーザーの画面へそのまま表示する。",
  "質問への回答、確認結果、判明した事実や原因、成果物、進捗、作業完了報告と次の工程の予告に使う。",
  "ユーザーの判断を求める確認は、このツールではなく既存の質問手段を使う。推論は送らない。",
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

export function register(on: On): void {
  // 報告のたびに権限確認の画面が出ると作業が止まるため、このツールの呼び出しを許可する。
  on("tool.check", ($, e, next) => (e.tool === fullName($.plugin.name) ? { decision: "allow" } : next(e))).catch(
    ($, e, next) => next(e),
  );

  // 判定が失敗した場合も他のツールの呼び出しを止めないよう、後続へ渡す。
  on("tool.call", ($, e, next) => {
    if (e.tool !== fullName($.plugin.name)) return next(e);
    if (messageOf(e) === undefined) {
      return { deny: "文字列のmessageが無いため、画面へ本文を表示していない。本文をmessageへ入れて呼び直す。" };
    }
    return { result: "ユーザーの画面へ表示した。" };
  }).catch(($, e, next) => next(e));

  // 呼び出しの行を`message`の全文の`Markdown`で描く。他のツールの行は描き替えない。
  on("ui.render", { component: "ToolUse" }, ($, e, next) => {
    if (e.props.tool !== fullName($.plugin.name)) return next(e);
    const message = messageOf(e.props.input);
    if (message === undefined) return next(e);
    const { Markdown } = $.ui.resolve(e);
    return <Markdown text={message} />;
  });

  // 成功結果はモデルへ返し、画面には本文の行だけを残す。エラーは後続の描画で示す。
  on("ui.render", { component: "ToolResult" }, ($, e, next) => {
    if (e.props.tool !== fullName($.plugin.name) || e.props.isErrored) return next(e);
    const { Box } = $.ui.resolve(e);
    return <Box />;
  });
}

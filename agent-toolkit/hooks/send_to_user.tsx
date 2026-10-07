import type { On } from "claude-code";

// Claude Codeは、同じ応答でツール呼び出しより前に置いた地の文の一部を、APIが返す要約（progress update）へ置き換えて表示する。
// ツールの入力は要約されないため、ターンの途中でユーザーへ原文どおり届ける内容をこのツールの`message`で運び、
// 呼び出しの行を`message`の`Markdown`で描く。ツールの登録と表示を同じmoduleに置き、表示できる環境にだけツールが現れるようにする。
// 設計と不採用とした代替は`docs/development/design-hooks.md`「send_to_userツール（2026年10月6日）」にある。

const TOOL_NAME = "send_to_user";

const DESCRIPTION = [
  "ユーザーが書かれたとおりに読む必要がある内容を、ターンの途中でユーザーの画面へそのまま表示する。",
  "ツール呼び出しより前に届ける途中の成果物、質問への直接の回答、確認結果、判明した事実や原因、作業完了報告、次の工程の予告に使う。",
  "作業経過の説明と推論には使わない。ターンを終える最後の回答には使わず、通常の本文として書く。",
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
export const SEND_TO_USER_TOOL = { name: TOOL_NAME, description: DESCRIPTION, inputSchema: INPUT_SCHEMA };

export function register(on: On): void {
  // 途中の報告のたびに権限確認の画面が出ると作業が止まるため、このツールの呼び出しを許可する。
  on("tool.check", ($, e, next) => (e.tool === fullName($.plugin.name) ? { decision: "allow" } : next(e))).catch(
    ($, e, next) => next(e),
  );

  // 判定が失敗した場合も他のツールの呼び出しを止めないよう、後続へ渡す。
  on("tool.call", ($, e, next) => (e.tool === fullName($.plugin.name) ? { result: "ユーザーの画面へ表示した。" } : next(e))).catch(
    ($, e, next) => next(e),
  );

  // 呼び出しの行を`message`の全文の`Markdown`で描く。他のツールの行は描き替えない。
  on("ui.render", { component: "ToolUse" }, ($, e, next) => {
    if (e.props.tool !== fullName($.plugin.name)) return next(e);
    const message = messageOf(e.props.input);
    if (message === undefined) return next(e);
    const { Markdown } = $.ui.resolve(e);
    return <Markdown text={message} />;
  });
}

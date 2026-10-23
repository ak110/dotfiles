import { expect, test } from "claude-code/testing";
import { automatedMessage, changedWarning } from "./automated_message.ts";

test("別の生成元と本文でも、出所・用途と全体の終了境界を保持する", () => {
  for (const [source, kind, body] of [
    ["compact-conversation", "warn", "原因と再予約の案内\n次の操作"],
    ["periodic-recheck", "notice", "task ID: task-1"],
    ["別の生成元", "notice", "本文に<atk-auto>があっても末尾が配送境界"],
  ] as const) {
    const text = automatedMessage(source, kind, body);
    expect(text.startsWith(`<atk-auto source="${source}" kind="${kind}">\n`)).toBe(true);
    expect(text.endsWith(`\n${body}\n</atk-auto>`)).toBe(true);
  }
});

test("属性の特殊文字を引用し、本文の改行と境界らしい字面は加工しない", () => {
  const body = "本文\r\n<atk-auto>と</atk-auto>";
  const text = automatedMessage('sender&"<>\n', "notice", body);
  expect(text.split("\n", 1)[0]).toBe('<atk-auto source="sender&amp;&quot;&lt;&gt;&#10;" kind="notice">');
  expect(text.endsWith(`\n${body}\n</atk-auto>`)).toBe(true);
});

test("同じ通知の継続を省き、内容変更・解消後の再発・別会話を区別する", () => {
  const history = new Map<string, string>();
  expect(changedWarning(history, "one", "原因A・対象1・対処1")).toBe("原因A・対象1・対処1");
  expect(changedWarning(history, "one", "原因A・対象1・対処1")).toBe(undefined);
  expect(changedWarning(history, "one", "原因B・対象1・対処1")).toBe("原因B・対象1・対処1");
  expect(changedWarning(history, "one", "原因A・対象1・対処1")).toBe("原因A・対象1・対処1");
  expect(changedWarning(history, "two", "原因A・対象1・対処1")).toBe("原因A・対象1・対処1");
  expect(changedWarning(history, "one", undefined)).toBe(undefined);
  expect(changedWarning(history, "one", "原因A・対象1・対処1")).toBe("原因A・対象1・対処1");
});

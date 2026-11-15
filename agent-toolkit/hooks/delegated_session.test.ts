import { expect, test } from "claude-code/testing";
import type { On } from "claude-code";
import { register } from "./register.ts";

test("Code Modeを妨げるtoolイベントとmod固有ツールを登録しない", () => {
  const events: string[] = [];
  const capture = ((name: string) => { events.push(name); return { catch: () => undefined }; }) as unknown as On;
  register(capture, {});
  expect(events.includes("tool.call")).toBe(false);
  expect(events.includes("tool.check")).toBe(false);
  expect(events.includes("session.compact")).toBe(true);
});

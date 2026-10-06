import type { Register } from "claude-code";

import { register as registerSendToUser, SEND_TO_USER_TOOL } from "./send_to_user.tsx";
import { exitStatePath, register as registerSessionExit } from "./session_exit.ts";

// hooks.jsonの`modules`は1つのプラグインにつき1つのhooks moduleだけを受け付け、照合条件の無い同じイベントのhookは
// 1回しか登録できない。また`$`は別ファイルの関数へ渡せないため、各機能の`session.start`の処理をここへまとめる。
export const register: Register = (on) => {
  on("session.start", async ($, e, next) => {
    // ツールは最初の発話より前に一覧へ載るよう、`next`より前に登録する。
    await $.tool.register(SEND_TO_USER_TOOL);
    const result = await next(e);
    // `atk agents-exit-session`の終了要求を受け付ける準備ができたことを示す印を書き、前の要求を消費済みにする。
    const sessionId = await $.session.id();
    const configured = await $.env.get("CLAUDE_CONFIG_DIR");
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const marker = exitStatePath(sessionId, configured, home, "marker");
    const request = exitStatePath(sessionId, configured, home, "request");
    if (marker && request) {
      await $.fs.write(request, "consumed");
      await $.fs.write(marker, "ready");
    }
    return result;
  });
  registerSessionExit(on);
  registerSendToUser(on);
};

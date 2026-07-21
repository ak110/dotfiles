//! Claude Code statusLine/subagentStatusLine 用CLIエントリポイント。
//!
//! 第1引数でモードを選択する。単一バイナリに2モードを持たせる理由は、GitHub Releaseの
//! 配布アセットを1個に抑え、post_apply側のダウンロード・配置ロジックを単純化するため。
//!
//! - `statusline`: `scripts/claude_status_line.py`の後継
//! - `subagent-statusline`: `scripts/claude_subagent_status_line.py`の後継

mod statusline;
mod subagent;

use std::io::Read as _;

fn main() {
    let mode = std::env::args().nth(1);
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        return;
    }
    match mode.as_deref() {
        Some("statusline") => statusline::run(&raw),
        Some("subagent-statusline") => subagent::run(&raw),
        _ => {
            eprintln!("usage: claude-statusline <statusline|subagent-statusline>");
            std::process::exit(1);
        }
    }
}

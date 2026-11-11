//! agents_serverが出力した状態ファイルをClaude Codeのstatusline行へ変換する。
//!
//! 状態ディレクトリはPython側の`platformdirs.user_state_dir("agent-toolkit")`と合わせる。
//! Linuxは絶対パスの`XDG_STATE_HOME`を優先し、無ければ`HOME/.local/state`、
//! Windowsは`LOCALAPPDATA`の配下に`agent-toolkit`を結合する。

use chrono::Utc;
use serde_json::Value;

use crate::render::DEFAULT_COLUMNS;

mod rendering;
mod state;

pub(crate) use rendering::render_state_files;
pub(crate) use state::{read_state_files, state_directory};

const STATE_VERSION: u64 = 1;
const HEARTBEAT_EXPIRY_SECONDS: i64 = 120;
// Claude Codeの描画は先頭の字下げ2セルと行末の2セルを確保する。
// 確保幅は描画された行の表示幅とCOLUMNSの差から導出した。
const STATUSLINE_RESERVED_COLUMNS: usize = 4;

#[derive(Debug)]
pub(crate) struct StateFile {
    file_name: String,
    host_session_id: Option<String>,
    sessions: Vec<Session>,
}

#[derive(Debug)]
struct Session {
    session_id: String,
    engine: String,
    model: Option<String>,
    effort: Option<String>,
    fast_mode: Option<bool>,
    launch_kind: String,
    status: String,
    progress: String,
    last_action: String,
    label: String,
    started_at: String,
    api_error: Option<ApiError>,
    context_tokens: Option<Value>,
    context_window: Option<Value>,
}

#[derive(Debug)]
struct ApiError {
    error_type: String,
    http_status: Option<u64>,
    first_at: String,
    count: u64,
    /// Claude Codeの利用上限の解除待ちだけが持つ、利用枠の種類と解除予定時刻。
    limit_type: Option<String>,
    resets_at: Option<String>,
}

/// `api_error.type`のうち、Claude Codeの利用上限の解除待ちを表す値。
const USAGE_LIMIT_ERROR_TYPE: &str = "usage_limit";

#[derive(Debug)]
struct DisplaySession<'a> {
    session: &'a Session,
    depth: usize,
}

fn terminal_columns() -> usize {
    std::env::var("COLUMNS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(DEFAULT_COLUMNS)
}

fn statusline_columns(columns: usize) -> usize {
    columns.saturating_sub(STATUSLINE_RESERVED_COLUMNS)
}

/// 現在の環境から状態ファイルを読み、statuslineに追加する行を返す。
pub(crate) fn render_for_session(root_session_id: &str) -> Vec<String> {
    let Some(directory) = state_directory(
        std::env::consts::OS,
        |key| std::env::var(key).ok(),
        root_session_id,
    ) else {
        return Vec::new();
    };
    let files = read_state_files(&directory);
    render_state_files(&files, statusline_columns(terminal_columns()), Utc::now())
}

#[cfg(test)]
mod tests;

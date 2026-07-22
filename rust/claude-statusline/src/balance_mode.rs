//! レビューバランスモード（claude寄り/codex寄り）を使用量に基づき動的判定する。
//!
//! `~/.config/agent-toolkit/review-balance-mode.claude-heavy`フラグファイルの有無で
//! `careful-review`スキルの判定ロジック（`test -f`）と連携する。本モジュールはフラグの
//! 生成・削除のみを担い、判定ロジック自体は`careful-review/SKILL.md`側に委ねる。

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

const HYSTERESIS_THRESHOLD_PCT: f64 = 10.0;
const STALE_THRESHOLD_SECS: u64 = 7 * 24 * 3600;
const CODEX_CHECK_INTERVAL_SECS: u64 = 30;

struct ClaudeUsage {
    five_hour_used_pct: Option<f64>,
    seven_day_used_pct: Option<f64>,
    seven_day_resets_at_unix: Option<u64>,
    pay_as_you_go: bool,
}

struct CodexUsage {
    five_hour_used_pct: Option<f64>,
    seven_day_used_pct: Option<f64>,
    seven_day_resets_at_unix: Option<u64>,
    observed_at_unix: u64,
    scanned_file: Option<String>,
    scanned_mtime: Option<u64>,
    pay_as_you_go: bool,
}

/// statusline描画のたびに呼び出すエントリポイント。内部エラーは全て無視し、
/// 描画処理そのものへ影響を与えない（`home`未取得時は何もしない）。
pub fn update(data: &Value, home: Option<&str>) {
    let Some(home) = home else { return };
    let dir = PathBuf::from(home).join(".config").join("agent-toolkit");
    if fs::create_dir_all(&dir).is_err() {
        return;
    }

    let claude = read_claude_usage(data);
    let claude_path = dir.join("claude-usage.json");
    if claude_usage_changed(&claude_path, &claude) {
        let _ = write_json(&claude_path, &claude_usage_to_value(&claude));
    }

    let codex = read_codex_usage_cached(home, &dir.join("codex-usage-cache.json"));

    let flag_path = dir.join("review-balance-mode.claude-heavy");
    let current_present = flag_path.is_file();
    let desired_present = decide_mode(&claude, &codex, current_present);
    apply_flag(&flag_path, desired_present);
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn read_claude_usage(data: &Value) -> ClaudeUsage {
    let has_rate_limits = data.get("rate_limits").is_some();
    ClaudeUsage {
        five_hour_used_pct: data
            .pointer("/rate_limits/five_hour/used_percentage")
            .and_then(Value::as_f64),
        seven_day_used_pct: data
            .pointer("/rate_limits/seven_day/used_percentage")
            .and_then(Value::as_f64),
        seven_day_resets_at_unix: data
            .pointer("/rate_limits/seven_day/resets_at")
            .and_then(Value::as_u64),
        pay_as_you_go: !has_rate_limits,
    }
}

fn claude_usage_to_value(u: &ClaudeUsage) -> Value {
    serde_json::json!({
        "five_hour_used_pct": u.five_hour_used_pct,
        "seven_day_used_pct": u.seven_day_used_pct,
        "seven_day_resets_at_unix": u.seven_day_resets_at_unix,
        "observed_at_unix": now_unix(),
        "pay_as_you_go": u.pay_as_you_go,
    })
}

/// 直近の書き込み内容と比較し、意味のある値（`observed_at_unix`を除く）が
/// 変化した場合のみ書き込みが必要と判定する。statusline描画のたびの不要なI/Oを避ける。
fn claude_usage_changed(path: &Path, u: &ClaudeUsage) -> bool {
    let cached = read_value(path);
    cached.get("five_hour_used_pct").and_then(Value::as_f64) != u.five_hour_used_pct
        || cached.get("seven_day_used_pct").and_then(Value::as_f64) != u.seven_day_used_pct
        || cached
            .get("seven_day_resets_at_unix")
            .and_then(Value::as_u64)
            != u.seven_day_resets_at_unix
        || cached.get("pay_as_you_go").and_then(Value::as_bool) != Some(u.pay_as_you_go)
}

/// 一時ファイル名にプロセスIDを含め、statuslineの並行起動間で書き込み先が衝突しないようにする。
fn write_json(path: &Path, value: &Value) -> std::io::Result<()> {
    let tmp = path.with_extension(format!("tmp.{}", std::process::id()));
    fs::write(&tmp, value.to_string())?;
    fs::rename(&tmp, path)
}

fn read_value(path: &Path) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or(Value::Null)
}

/// キャッシュ済みJSON値から`CodexUsage`を組み立てる（新規走査を伴わない）。
fn cached_usage_from_value(v: &Value, pay_as_you_go: bool) -> CodexUsage {
    CodexUsage {
        five_hour_used_pct: v.get("five_hour_used_pct").and_then(Value::as_f64),
        seven_day_used_pct: v.get("seven_day_used_pct").and_then(Value::as_f64),
        seven_day_resets_at_unix: v.get("seven_day_resets_at_unix").and_then(Value::as_u64),
        observed_at_unix: v
            .get("observed_at_unix")
            .and_then(Value::as_u64)
            .unwrap_or(0),
        scanned_file: v
            .get("scanned_file")
            .and_then(Value::as_str)
            .map(String::from),
        scanned_mtime: v.get("scanned_mtime").and_then(Value::as_u64),
        pay_as_you_go,
    }
}

/// キャッシュ値の使用量フィールドを保持したまま`checked_at_unix`のみ更新して書き込み、
/// 更新後の値から`CodexUsage`を組み立てて返す（走査省略時の共通経路）。
fn touch_and_return(cached: &Value, pay_as_you_go: bool, cache_path: &Path) -> CodexUsage {
    let value = touch_checked_at(cached, pay_as_you_go);
    let _ = write_json(cache_path, &value);
    cached_usage_from_value(&value, pay_as_you_go)
}

/// Codex側の週間/5時間使用率をmtimeキャッシュ付きで取得する。
/// 最新rolloutファイルのmtimeがキャッシュと一致する場合は再走査しない。
/// `checked_at_unix`から`CODEX_CHECK_INTERVAL_SECS`未満しか経過していない場合は
/// ディレクトリ走査・`auth.json`読込み自体を省略し、statuslineの高頻度呼び出しでの
/// I/O負荷を抑える（`auth_mode`切替の反映は最大`CODEX_CHECK_INTERVAL_SECS`遅延し得る）。
fn read_codex_usage_cached(home: &str, cache_path: &Path) -> CodexUsage {
    let cached = read_value(cache_path);
    let cached_pay_as_you_go = cached
        .get("pay_as_you_go")
        .and_then(Value::as_bool)
        .unwrap_or(false);

    if let Some(checked_at) = cached.get("checked_at_unix").and_then(Value::as_u64) {
        if now_unix().saturating_sub(checked_at) < CODEX_CHECK_INTERVAL_SECS {
            return cached_usage_from_value(&cached, cached_pay_as_you_go);
        }
    }

    let sessions_dir = PathBuf::from(home).join(".codex").join("sessions");
    let auth_mode = read_codex_auth_mode(home);
    let pay_as_you_go = auth_mode.as_deref() != Some("chatgpt");

    // 最新日ディレクトリが空、または最新ファイルに有効なrate_limitsイベントが無い場合に備え、
    // 直近5ファイルまで新しい順に候補化し、有効なイベントが見つかるまで遡って走査する。
    let candidates = recent_rollout_files(&sessions_dir, 5);
    let Some(latest) = candidates.first().cloned() else {
        return touch_and_return(&cached, pay_as_you_go, cache_path);
    };
    // ナノ秒精度で保持し、同一秒内の追記も検知できるようにする（`as_secs()`は使わない）。
    let latest_mtime = fs::metadata(&latest)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_nanos() as u64);
    let latest_str = latest.to_str().map(String::from);

    if cached.get("scanned_file").and_then(Value::as_str) == latest_str.as_deref()
        && cached.get("scanned_mtime").and_then(Value::as_u64) == latest_mtime
    {
        return touch_and_return(&cached, pay_as_you_go, cache_path);
    }

    for candidate in &candidates {
        let Some((five_hour, seven_day, seven_day_resets_at, observed_at)) =
            scan_rollout_for_rate_limits(candidate)
        else {
            continue;
        };
        let usage = CodexUsage {
            five_hour_used_pct: five_hour,
            seven_day_used_pct: seven_day,
            seven_day_resets_at_unix: seven_day_resets_at,
            observed_at_unix: observed_at,
            scanned_file: candidate.to_str().map(String::from),
            scanned_mtime: latest_mtime,
            pay_as_you_go,
        };
        let value = serde_json::json!({
            "five_hour_used_pct": usage.five_hour_used_pct,
            "seven_day_used_pct": usage.seven_day_used_pct,
            "seven_day_resets_at_unix": usage.seven_day_resets_at_unix,
            "observed_at_unix": usage.observed_at_unix,
            "scanned_file": usage.scanned_file,
            "scanned_mtime": usage.scanned_mtime,
            "pay_as_you_go": usage.pay_as_you_go,
            "checked_at_unix": now_unix(),
        });
        let _ = write_json(cache_path, &value);
        return usage;
    }
    touch_and_return(&cached, pay_as_you_go, cache_path)
}

/// 既存キャッシュ値の使用量フィールドは保持したまま`pay_as_you_go`・`checked_at_unix`のみ更新する。
fn touch_checked_at(cached: &Value, pay_as_you_go: bool) -> Value {
    let mut value = if cached.is_object() {
        cached.clone()
    } else {
        serde_json::json!({})
    };
    value["pay_as_you_go"] = serde_json::json!(pay_as_you_go);
    value["checked_at_unix"] = serde_json::json!(now_unix());
    value
}

/// `sessions/<年>/<月>/<日>/rollout-*.jsonl`を年→月→日の降順で辿り、
/// 直近`limit`件のファイルを新しい順に列挙する。
/// 各日ディレクトリが空でも打ち切らず前日以前へ遡る（最新1ファイルのみに限定しない）。
fn recent_rollout_files(sessions_dir: &Path, limit: usize) -> Vec<PathBuf> {
    let mut result = Vec::new();
    for year_dir in sorted_subdirs_desc(sessions_dir) {
        for month_dir in sorted_subdirs_desc(&year_dir) {
            for day_dir in sorted_subdirs_desc(&month_dir) {
                let mut files = rollout_files_in_dir(&day_dir);
                files.reverse();
                for file in files {
                    result.push(file);
                    if result.len() >= limit {
                        return result;
                    }
                }
            }
        }
    }
    result
}

fn rollout_files_in_dir(day_dir: &Path) -> Vec<PathBuf> {
    let mut entries: Vec<PathBuf> = fs::read_dir(day_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name().and_then(|n| n.to_str()).map_or(false, |n| {
                n.starts_with("rollout-") && n.ends_with(".jsonl")
            })
        })
        .collect();
    entries.sort();
    entries
}

fn sorted_subdirs_desc(dir: &Path) -> Vec<PathBuf> {
    let mut entries: Vec<PathBuf> = fs::read_dir(dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.is_dir())
        .collect();
    entries.sort();
    entries.reverse();
    entries
}

/// `scan_rollout_for_rate_limits`の戻り値: `(five_hour_used_pct, seven_day_used_pct,
/// seven_day_resets_at_unix, observed_at_unix)`。
type RateLimitsScanResult = (Option<f64>, Option<f64>, Option<u64>, u64);

/// rolloutファイルの各行を末尾から走査し、直近の`token_count`イベントのうち
/// `payload.rate_limits.secondary`が非nullの最初の1件を採用する。
/// （`primary`のみ先行して充足し`secondary`が後から追加される実データを実機確認したため）
fn scan_rollout_for_rate_limits(path: &Path) -> Option<RateLimitsScanResult> {
    let content = fs::read_to_string(path).ok()?;
    for line in content.lines().rev() {
        let Ok(obj) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if obj.pointer("/payload/type").and_then(Value::as_str) != Some("token_count") {
            continue;
        }
        let Some(rate_limits) = obj.pointer("/payload/rate_limits") else {
            continue;
        };
        let seven_day = rate_limits
            .pointer("/secondary/used_percent")
            .and_then(Value::as_f64);
        if seven_day.is_none() {
            continue;
        }
        let five_hour = rate_limits
            .pointer("/primary/used_percent")
            .and_then(Value::as_f64);
        let seven_day_resets_at = rate_limits
            .pointer("/secondary/resets_at")
            .and_then(Value::as_u64);
        let observed_at = obj
            .get("timestamp")
            .and_then(Value::as_str)
            .and_then(parse_rfc3339_to_unix)
            .unwrap_or_else(now_unix);
        return Some((five_hour, seven_day, seven_day_resets_at, observed_at));
    }
    None
}

fn read_codex_auth_mode(home: &str) -> Option<String> {
    let path = PathBuf::from(home).join(".codex").join("auth.json");
    let content = fs::read_to_string(path).ok()?;
    let obj: Value = serde_json::from_str(&content).ok()?;
    obj.get("auth_mode")
        .and_then(Value::as_str)
        .map(String::from)
}

fn is_stale(observed_at_unix: u64) -> bool {
    now_unix().saturating_sub(observed_at_unix) > STALE_THRESHOLD_SECS
}

/// ヒステリシス付きでモード（claude寄りフラグを立てるか）を決定する。
/// 従量課金環境（Claude側`rate_limits`欠落、またはCodex側`auth_mode`が`chatgpt`以外）を
/// 検知した場合は比較をせずcodex寄り（false）へ固定する。
/// いずれかの週間使用率が欠落、または観測が陳腐化している場合も安全側のcodex寄り（false）を返す。
fn decide_mode(claude: &ClaudeUsage, codex: &CodexUsage, current_present: bool) -> bool {
    if claude.pay_as_you_go || codex.pay_as_you_go {
        return false;
    }
    let (Some(claude_pct), Some(codex_pct)) = (claude.seven_day_used_pct, codex.seven_day_used_pct)
    else {
        return false;
    };
    if is_stale(codex.observed_at_unix) {
        return false;
    }
    let headroom_diff = codex_pct - claude_pct; // 正: claude側がより余裕あり
    if current_present {
        headroom_diff >= -HYSTERESIS_THRESHOLD_PCT
    } else {
        headroom_diff > HYSTERESIS_THRESHOLD_PCT
    }
}

fn apply_flag(path: &Path, desired_present: bool) {
    let current_present = path.is_file();
    if desired_present == current_present {
        return;
    }
    if desired_present {
        let _ = fs::write(path, b"");
    } else {
        let _ = fs::remove_file(path);
    }
}

fn parse_rfc3339_to_unix(s: &str) -> Option<u64> {
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.timestamp().max(0) as u64)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn claude(pct: f64, pay_as_you_go: bool) -> ClaudeUsage {
        ClaudeUsage {
            five_hour_used_pct: None,
            seven_day_used_pct: Some(pct),
            seven_day_resets_at_unix: None,
            pay_as_you_go,
        }
    }

    fn codex(pct: f64, observed_at_unix: u64, pay_as_you_go: bool) -> CodexUsage {
        CodexUsage {
            five_hour_used_pct: None,
            seven_day_used_pct: Some(pct),
            seven_day_resets_at_unix: None,
            observed_at_unix,
            scanned_file: None,
            scanned_mtime: None,
            pay_as_you_go,
        }
    }

    #[test]
    fn hysteresis_keeps_codex_mode_within_band() {
        assert!(!decide_mode(
            &claude(30.0, false),
            &codex(35.0, now_unix(), false),
            false
        ));
    }

    #[test]
    fn hysteresis_switches_to_claude_heavy_beyond_threshold() {
        assert!(decide_mode(
            &claude(10.0, false),
            &codex(25.0, now_unix(), false),
            false
        ));
    }

    #[test]
    fn hysteresis_keeps_claude_heavy_within_band_before_switching_back() {
        assert!(decide_mode(
            &claude(28.0, false),
            &codex(30.0, now_unix(), false),
            true
        ));
    }

    #[test]
    fn hysteresis_exact_upper_threshold_does_not_switch() {
        // headroom_diff == HYSTERESIS_THRESHOLD_PCT（10.0）ちょうどは`>`で判定するため切替しない。
        assert!(!decide_mode(
            &claude(20.0, false),
            &codex(30.0, now_unix(), false),
            false
        ));
    }

    #[test]
    fn hysteresis_exact_lower_threshold_stays_claude_heavy() {
        // headroom_diff == -HYSTERESIS_THRESHOLD_PCT（-10.0）ちょうどは`>=`で判定するため維持する。
        assert!(decide_mode(
            &claude(20.0, false),
            &codex(10.0, now_unix(), false),
            true
        ));
    }

    #[test]
    fn hysteresis_below_lower_threshold_switches_back_to_codex_heavy() {
        // headroom_diff < -HYSTERESIS_THRESHOLD_PCTでは維持条件を満たさず切替に転じる。
        assert!(!decide_mode(
            &claude(21.0, false),
            &codex(10.0, now_unix(), false),
            true
        ));
    }

    #[test]
    fn pay_as_you_go_forces_codex_heavy() {
        assert!(!decide_mode(
            &claude(0.0, true),
            &codex(5.0, now_unix(), false),
            true
        ));
    }

    #[test]
    fn stale_codex_observation_forces_codex_heavy() {
        assert!(!decide_mode(
            &claude(5.0, false),
            &codex(50.0, 0, false),
            true
        ));
    }

    fn make_test_dir(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("balance_mode_test_{}_{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn recent_rollout_files_skips_empty_latest_day() {
        let sessions = make_test_dir("skips_empty_latest_day");
        let day1 = sessions.join("2026").join("07").join("13");
        let day2 = sessions.join("2026").join("07").join("14");
        fs::create_dir_all(&day1).unwrap();
        fs::create_dir_all(&day2).unwrap();
        let expected = day1.join("rollout-2026-07-13T00-00-00-aaa.jsonl");
        fs::write(&expected, "").unwrap();

        let files = recent_rollout_files(&sessions, 5);

        assert_eq!(files, vec![expected]);
        let _ = fs::remove_dir_all(&sessions);
    }

    #[test]
    fn scan_rollout_for_rate_limits_skips_event_without_secondary() {
        let sessions = make_test_dir("skips_event_without_secondary");
        let file = sessions.join("rollout-test.jsonl");
        let without_secondary = serde_json::json!({
            "timestamp": "2026-07-13T00:00:00Z",
            "type": "event_msg",
            "payload": {"type": "token_count", "rate_limits": {"primary": {"used_percent": 10.0}, "secondary": null}}
        });
        let with_secondary = serde_json::json!({
            "timestamp": "2026-07-13T00:01:00Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 12.0},
                    "secondary": {"used_percent": 20.0, "resets_at": 1784000000},
                },
            },
        });
        fs::write(
            &file,
            format!("{}\n{}\n", without_secondary, with_secondary),
        )
        .unwrap();

        let (five_hour, seven_day, resets_at, observed_at) =
            scan_rollout_for_rate_limits(&file).unwrap();

        assert_eq!(five_hour, Some(12.0));
        assert_eq!(seven_day, Some(20.0));
        assert_eq!(resets_at, Some(1784000000));
        assert_eq!(
            observed_at,
            parse_rfc3339_to_unix("2026-07-13T00:01:00Z").unwrap()
        );
        let _ = fs::remove_dir_all(&sessions);
    }

    #[test]
    fn update_writes_claude_usage_and_leaves_flag_absent_without_codex_home() {
        // `~/.codex`が存在しない環境（Codex側`auth.json`読込み失敗）ではpay_as_you_go扱いとなり、
        // 比較をせずフラグ不在（codex寄り）へ固定される。`update`のエントリポイント全体
        // （ディレクトリ作成・claude-usage.json書き込み・codex-usage-cache.json書き込み・フラグ判定）を検証する。
        let home = make_test_dir("update_writes_claude_usage");
        let data = serde_json::json!({
            "rate_limits": {
                "five_hour": {"used_percentage": 12.0},
                "seven_day": {"used_percentage": 40.0, "resets_at": 1_784_000_000_u64},
            },
        });

        update(&data, home.to_str());

        let config_dir = home.join(".config").join("agent-toolkit");
        let claude_usage: Value = serde_json::from_str(
            &fs::read_to_string(config_dir.join("claude-usage.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(
            claude_usage
                .get("seven_day_used_pct")
                .and_then(Value::as_f64),
            Some(40.0)
        );
        assert_eq!(
            claude_usage.get("pay_as_you_go").and_then(Value::as_bool),
            Some(false)
        );

        let codex_cache: Value = serde_json::from_str(
            &fs::read_to_string(config_dir.join("codex-usage-cache.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(
            codex_cache.get("pay_as_you_go").and_then(Value::as_bool),
            Some(true)
        );

        assert!(!config_dir
            .join("review-balance-mode.claude-heavy")
            .is_file());
        let _ = fs::remove_dir_all(&home);
    }
}

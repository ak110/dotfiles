use super::*;

#[test]
fn speed_suffix_follows_codex_state_and_preserves_legacy_rows() {
    for (engine, fast, effort, expected) in [
        (
            "codex",
            Some(true),
            Some("medium"),
            "label (codex:model/medium@fast)",
        ),
        ("codex", Some(true), None, "label (codex:model@fast)"),
        (
            "codex",
            Some(false),
            Some("medium"),
            "label (codex:model/medium)",
        ),
        ("codex", None, Some("medium"), "label (codex:model/medium)"),
        (
            "claude",
            Some(true),
            Some("medium"),
            "label (claude:model/medium)",
        ),
        (
            "agy",
            Some(true),
            Some("medium"),
            "label (agy:model/medium)",
        ),
    ] {
        let mut row = session(
            "speed",
            engine,
            Value::String("model".into()),
            ("execute", "delegate"),
            ("", "label"),
            "2025-12-31T23:59:30+00:00",
        );
        row["effort"] = serde_json::json!(effort);
        if let Some(enabled) = fast {
            row["fast_mode"] = serde_json::json!(enabled);
        }
        let parsed = parse_session(&row).unwrap();
        assert_eq!(display_name(&parsed, 0), expected);
        let file = state_file("root.json", Value::Null, serde_json::json!([row]));
        let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
            .unwrap()
            .with_timezone(&Utc);
        let rendered = render_state_files(&[file], DEFAULT_COLUMNS, now);
        assert_eq!(rendered.len(), 1);
        assert!(rendered[0].contains(expected));
    }
}

#[test]
fn shared_context_usage_examples_reach_all_rendered_statuses() {
    let contract = serde_json::from_str::<Value>(STATE_CONTRACT).unwrap();
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);
    for case in contract["context_usage_cases"].as_array().unwrap() {
        for status in ["running", "completed", "failed", "interrupted"] {
            let mut row = status_session("usage", status, "2025-12-31T23:58:55+00:00");
            row["context_tokens"] = case["tokens"].clone();
            row["context_window"] = case["window"].clone();
            let file = state_file("root.json", Value::Null, serde_json::json!([row]));
            let lines = render_state_files(&[file], 160, now);
            if let Some(expected) = case["formatted"].as_str() {
                assert!(
                    lines[0].ends_with(&format!("1m5s · {expected} · {status}")),
                    "{lines:?}"
                );
            } else {
                assert!(lines[0].ends_with(&format!("1m5s · {status}")), "{lines:?}");
            }
        }
    }
    for error_type in ["rate_limit_error", "usage_limit"] {
        let mut row = status_session("retry", "running", "2025-12-31T23:58:55+00:00");
        row["context_tokens"] = serde_json::json!(53500);
        row["context_window"] = serde_json::json!(1000000);
        row["api_error"] = serde_json::json!({
            "type": error_type, "http_status": 429,
            "first_at": "2025-12-31T23:59:00+00:00", "count": 2,
            "limit_type": "seven_day", "resets_at": "2026-01-01T01:00:00+00:00"
        });
        for width in [40, 80, 140] {
            let file = state_file("root.json", Value::Null, serde_json::json!([row.clone()]));
            let lines = render_state_files(&[file], width, now);
            assert!(display_width(&lines[0]) <= width);
            if width >= 80 {
                assert!(lines[0].ends_with("53.5k/5%"), "{lines:?}");
                assert!(
                    lines[0].contains(if error_type == "usage_limit" {
                        "解除まで"
                    } else {
                        "HTTP 429"
                    }),
                    "{lines:?}"
                );
            }
        }
    }
}

#[test]
fn statusline_rendering_fits_available_columns() {
    let mut row = status_session("session-id", "running", "2025-12-31T23:59:00+00:00");
    row["label"] = serde_json::json!(format!("session-id {}", "long-label".repeat(20)));
    let files = [state_file(
        "root.json",
        Value::Null,
        serde_json::json!([row]),
    )];
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);
    for width in [1, 3, 4, 40, 80] {
        let lines = render_state_files(&files, statusline_columns(width), now);
        // ホストは字下げ2セルと行末2セルを消費する。製品側の予約定数とは独立した描画契約。
        let host_cells = width.min(4);
        assert!(
            lines
                .iter()
                .all(|line| display_width(line) + host_cells <= width),
            "{lines:?}"
        );
        if width >= 40 {
            assert_eq!(lines.len(), 1);
            assert!(lines[0].contains("session-id"), "{lines:?}");
            assert!(lines[0].contains("1m0s"), "{lines:?}");
        }
    }
}

#[test]
fn rendering_uses_configured_model_format_and_shows_label_once() {
    let mut selected = session(
        "selected",
        "codex",
        Value::String("gpt-5.6-terra".to_string()),
        ("review", "delegate"),
        ("", "pick-wi"),
        "2025-12-31T23:59:30+00:00",
    );
    selected["effort"] = Value::String("medium".to_string());
    let root = state_file("root.json", Value::Null, serde_json::json!([selected]));
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);

    let lines = render_state_files(&[root], 140, now);

    assert_eq!(lines.len(), 1);
    assert!(lines[0].starts_with("pick-wi (codex:gpt-5.6-terra/medium)"));
    assert_eq!(lines[0].matches("pick-wi").count(), 1);
    assert!(lines[0].ends_with("30s · running"));
}

#[test]
fn rendering_prefers_last_action_over_progress() {
    let mut with_action = session(
        "with-action",
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("latest progress", "fallback label"),
        "2025-12-31T23:59:30+00:00",
    );
    with_action["last_action"] = Value::String("Bash".to_string());
    let mut empty_action = session(
        "empty-action",
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("latest progress", "fallback label"),
        "2025-12-31T23:59:31+00:00",
    );
    empty_action["last_action"] = Value::String(String::new());
    let without_action = session(
        "without-action",
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("", "fallback label"),
        "2025-12-31T23:59:32+00:00",
    );
    let root = state_file(
        "root.json",
        Value::Null,
        serde_json::json!([with_action, empty_action, without_action]),
    );
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);

    let lines = render_state_files(&[root], 200, now);

    assert_eq!(lines.len(), 3, "{lines:?}");
    assert!(lines[0].contains("Bash"), "{lines:?}");
    assert!(!lines[0].contains("latest progress"), "{lines:?}");
    assert!(lines[1].contains("latest progress"), "{lines:?}");
    assert!(!lines[2].contains("Bash"), "{lines:?}");
    assert!(!lines[2].contains("latest progress"), "{lines:?}");
}

#[test]
fn rendering_api_error_from_state_file_shows_retry_and_recovers() {
    let mut retrying = session(
        "retrying",
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("latest progress", "lane-01"),
        "2025-12-31T23:59:00+00:00",
    );
    retrying["last_action"] = Value::String("Bash".to_string());
    retrying["api_error"] = serde_json::json!({
        "type": "rate_limit_error",
        "http_status": 429,
        "first_at": "2025-12-31T23:59:20+00:00",
        "count": 3,
    });
    let mut recovered = retrying.clone();
    recovered["session_id"] = Value::String("recovered".to_string());
    recovered.as_object_mut().unwrap().remove("api_error");
    let mut overloaded = retrying.clone();
    overloaded["session_id"] = Value::String("overloaded".to_string());
    overloaded["engine"] = Value::String("codex".to_string());
    overloaded["api_error"] = serde_json::json!({
        "type": "serverOverloaded",
        "http_status": null,
        "first_at": "2025-12-31T23:59:30+00:00",
        "count": 2,
    });
    let mut terminal = retrying.clone();
    terminal["session_id"] = Value::String("terminal".to_string());
    terminal["status"] = Value::String("completed".to_string());
    let mut malformed = retrying.clone();
    malformed["session_id"] = Value::String("malformed".to_string());
    malformed["api_error"]["count"] = Value::String("3".to_string());
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);

    let files = [
        state_file("retrying.json", Value::Null, serde_json::json!([retrying])),
        state_file(
            "recovered.json",
            Value::Null,
            serde_json::json!([recovered]),
        ),
        state_file(
            "overloaded.json",
            Value::Null,
            serde_json::json!([overloaded]),
        ),
        state_file("terminal.json", Value::Null, serde_json::json!([terminal])),
        state_file(
            "malformed.json",
            Value::Null,
            serde_json::json!([malformed]),
        ),
    ];
    let lines = render_state_files(&files, 160, now);

    assert_eq!(lines.len(), 5);
    assert!(lines[0].contains("API再試行 rate_limit_error"));
    assert!(lines[0].contains("HTTP 429"));
    assert!(lines[0].contains("40s"));
    assert!(lines[0].contains("3回"));
    assert!(lines[0].contains("対応不要"));
    assert!(lines[0].contains("自動再試行中"));
    assert!(lines.iter().all(|line| display_width(line) <= 160));
    assert!(!lines[0].contains("Bash"));
    let overloaded_line = lines
        .iter()
        .find(|line| line.contains("serverOverloaded"))
        .expect("Codexの過負荷の待機を表示する");
    assert!(overloaded_line.contains("API再試行 serverOverloaded"));
    assert!(overloaded_line.contains("HTTP ?"));
    assert!(overloaded_line.contains("2回"));
    assert!(overloaded_line.contains("対応不要"));
    let others: Vec<_> = lines[1..]
        .iter()
        .filter(|line| !line.contains("serverOverloaded"))
        .collect();
    assert_eq!(others.len(), 3);
    assert!(others.iter().all(|line| !line.contains("API再試行")));
    assert!(others
        .iter()
        .filter(|line| !line.contains("completed"))
        .all(|line| line.contains("Bash")));
    let terminal_line = others
        .iter()
        .find(|line| line.contains("completed"))
        .unwrap();
    assert!(terminal_line.contains("対応不要"));
    assert!(terminal_line.contains("結果回収・子孫session終了後に非表示"));
}

#[test]
fn rendering_usage_limit_wait_differs_from_api_retry() {
    let mut waiting = session(
        "waiting",
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("latest progress", "lane-01"),
        "2025-12-31T23:00:00+00:00",
    );
    waiting["api_error"] = serde_json::json!({
        "type": "usage_limit",
        "http_status": 429,
        "first_at": "2025-12-31T23:30:00+00:00",
        "count": 2,
        "limit_type": "seven_day",
        "resets_at": "2026-01-01T02:05:00+00:00",
    });
    let mut unknown_reset = waiting.clone();
    unknown_reset["session_id"] = Value::String("unknown-reset".to_string());
    unknown_reset["api_error"]["limit_type"] = Value::String("five_hour".to_string());
    unknown_reset["api_error"]
        .as_object_mut()
        .unwrap()
        .remove("resets_at");
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);

    let files = [
        state_file("waiting.json", Value::Null, serde_json::json!([waiting])),
        state_file(
            "unknown.json",
            Value::Null,
            serde_json::json!([unknown_reset]),
        ),
    ];
    let lines = render_state_files(&files, 180, now);

    assert_eq!(lines.len(), 2);
    let waiting_line = lines
        .iter()
        .find(|line| line.contains("seven_day"))
        .expect("Weekly limitの解除待ちを表示する");
    assert!(
        waiting_line.contains("利用上限の解除待ち seven_day"),
        "{lines:?}"
    );
    assert!(waiting_line.contains("解除まで2h5m"), "{lines:?}");
    assert!(waiting_line.contains("30m0s"), "{lines:?}");
    assert!(waiting_line.contains("対応不要"), "{lines:?}");
    assert!(
        waiting_line.contains("解除後に同じsessionを自動再開"),
        "{lines:?}"
    );
    assert!(!waiting_line.contains("API再試行"), "{lines:?}");
    let unknown_line = lines
        .iter()
        .find(|line| line.contains("five_hour"))
        .expect("5時間の利用上限の解除待ちを表示する");
    assert!(unknown_line.contains("解除時刻確認中"), "{lines:?}");
    assert!(unknown_line.contains("対応不要"), "{lines:?}");
    assert!(lines.iter().all(|line| display_width(line) <= 180));
}

#[test]
fn label_on_left_keeps_elapsed_at_right() {
    let short = session(
        "short",
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("", "lane-02"),
        "2025-12-31T23:59:30+00:00",
    );
    let long = session(
        "long",
        "claude",
        Value::Null,
        ("impl", "delegate"),
        ("", "とても長い名前を持つセッション"),
        "2025-12-31T23:59:30+00:00",
    );
    let root = state_file("root.json", Value::Null, serde_json::json!([short, long]));
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);

    let lines = render_state_files(&[root], 200, now);

    assert_eq!(lines.len(), 2, "{lines:?}");
    for line in &lines {
        assert!(line.ends_with("30s · running"), "{lines:?}");
    }
    assert!(lines[0].contains("lane-02"), "{lines:?}");
    assert!(lines[0].starts_with("lane-02 (claude)"), "{lines:?}");
    assert!(
        lines[1].starts_with("とても長い名前を持つセッション (claude)"),
        "{lines:?}"
    );
    assert!(lines.iter().all(|line| display_width(line) <= 200));
}

#[test]
fn waiting_and_terminal_rows_keep_identity_elapsed_and_guidance_at_80_columns() {
    let now = DateTime::parse_from_rfc3339("2026-01-01T00:00:00+00:00")
        .unwrap()
        .with_timezone(&Utc);
    for state in [
        "usage_limit",
        "rate_limit_error",
        "completed",
        "failed",
        "interrupted",
    ] {
        let terminal = matches!(state, "completed" | "failed" | "interrupted");
        let status = if terminal { state } else { "running" };
        let mut row = status_session("session-id", status, "2025-12-31T23:59:00+00:00");
        if !terminal {
            row["api_error"] = serde_json::json!({
                "type": state, "http_status": 429,
                "first_at": "2025-12-31T23:59:00+00:00", "count": 3,
                "limit_type": "seven_day", "resets_at": "2026-01-01T01:00:00+00:00"
            });
        }
        let files = [state_file(
            "root.json",
            Value::Null,
            serde_json::json!([row]),
        )];
        let lines = render_state_files(&files, 80, now);
        assert!(lines[0].starts_with("session-id (claude)"), "{lines:?}");
        assert!(lines[0].contains("対応不要"), "{lines:?}");
        assert!(lines[0].contains("1m0s"), "{lines:?}");
        if terminal {
            assert!(lines[0].ends_with(status), "{lines:?}");
        } else if state == "usage_limit" {
            assert!(lines[0].contains("解除まで1h0m"), "{lines:?}");
        } else {
            assert!(lines[0].ends_with("HTTP 429 · 1m0s · 3回"), "{lines:?}");
        }
        assert!(display_width(&lines[0]) <= 80, "{lines:?}");
    }
}

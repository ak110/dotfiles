from agent_toolkit._common.output_policy import rules


def assert_output_policy(profile: str, text: str) -> None:
    check = rules()["acceptance"][profile]
    assert len(text) <= check["max_chars"], (profile, text)
    assert all(word in text for word in check["includes"]), (profile, text)
    assert not any(word in text for word in check["excludes"]), (profile, text)

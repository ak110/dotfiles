from agent_toolkit._atk import config_settings

from pytools._internal import post_apply_outcome


def run() -> post_apply_outcome.PostApplyOutcome:

    settings = config_settings.load_config_strict()
    defaults = {"usage_auto_allocation": "true", "usage_activity_mode": "intermittent"}
    missing = {key: value for key, value in defaults.items() if key not in settings}
    if not missing:
        return post_apply_outcome.PostApplyOutcome()
    settings.update(missing)
    config_settings.save_config(settings)
    return post_apply_outcome.PostApplyOutcome(changed=True)

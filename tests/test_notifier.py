from lastwar_bot.config import OpenClawConfig
from lastwar_bot.notifier import OpenClawNotifier


def test_build_payload_renders_variables():
    notifier = OpenClawNotifier(
        OpenClawConfig(
            url="http://127.0.0.1:18789/message",
            payload_template={"message": "{message}", "event": "{event}", "meta": {"source": "bot"}},
        )
    )

    payload = notifier.build_payload("hello", event="dig_up_treasure")

    assert payload == {"message": "hello", "event": "dig_up_treasure", "meta": {"source": "bot"}}

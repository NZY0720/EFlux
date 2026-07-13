from eflux.agents.reflective.chat import (
    CHAT_DIRECTIONS,
    build_chat_messages,
    chat_direction,
    chat_line_is_repetitive,
    default_chat_style,
)


def test_chat_direction_rotates_and_default_voices_are_stable():
    assert [chat_direction(i) for i in range(len(CHAT_DIRECTIONS))] == list(CHAT_DIRECTIONS)
    assert chat_direction(len(CHAT_DIRECTIONS)) == CHAT_DIRECTIONS[0]
    assert default_chat_style("solar-alpha") == default_chat_style("solar-alpha")
    assert len({default_chat_style(name) for name in ("solar-alpha", "wind-beta", "battery-gamma", "load-delta")}) >= 3


def test_chat_prompt_carries_distinct_voice_and_turn_direction():
    messages = build_chat_messages(
        name="battery-gamma",
        persona="Protect SOC before the peak.",
        context={"market_last_price": 52.0},
        recent_chat=[{"name": "wind-beta", "text": "Price is flat again."}],
        style="terse risk manager",
        direction="Ask a context-grounded question.",
    )

    system = messages[0]["content"]
    assert "terse risk manager" in system
    assert "Ask a context-grounded question." in system
    assert "Vary sentence shape" in system


def test_repetitive_chat_filter_blocks_rephrased_echoes_but_allows_new_topic():
    recent = [{"name": "wind-beta", "text": "Battery spread looks thin near the evening peak"}]

    assert chat_line_is_repetitive(
        "Battery spread looks thin near the evening peak!",
        recent,
    )
    assert not chat_line_is_repetitive(
        "My SOC is 42%; I am saving headroom for the next ramp.",
        recent,
    )

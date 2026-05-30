"""Local intent gate tests."""

from relaycli.intent import local_reply_for


def test_greeting_gets_local_guide_reply():
    reply = local_reply_for("halo")
    assert reply is not None
    assert reply.reason == "greeting"
    assert "siap bantu" in reply.text


def test_vague_short_input_asks_for_context():
    reply = local_reply_for("bantu dong")
    assert reply is not None
    assert reply.reason == "vague"
    assert "target" in reply.text


def test_clear_work_requests_pass_through():
    assert local_reply_for("run tests") is None
    assert local_reply_for("fix test yang gagal") is None
    assert local_reply_for("jelaskan repo ini") is None


def test_long_context_passes_through_even_with_greeting():
    text = (
        "halo, output planner terlalu panjang waktu user cuma menyapa; "
        "tolong bikin filter intent dan rapikan tampilan web"
    )
    assert local_reply_for(text) is None


def test_slash_palette_hint_is_local():
    reply = local_reply_for("/")
    assert reply is not None
    assert "/setup" in reply.text

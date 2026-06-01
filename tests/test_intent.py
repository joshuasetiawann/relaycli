"""Local intent gate tests."""

from relaycli.intent import continuation_for, is_permissive_followup, local_reply_for


def test_greeting_gets_local_guide_reply():
    reply = local_reply_for("halo")
    assert reply is not None
    assert reply.reason == "greeting"
    assert "siap bantu" in reply.text
    assert "bahasa bebas" in reply.text


def test_stretched_greeting_stays_local():
    reply = local_reply_for("halooows")
    assert reply is not None
    assert reply.reason == "greeting"


def test_vague_short_input_asks_for_context():
    reply = local_reply_for("bantu dong")
    assert reply is not None
    assert reply.reason == "vague"
    assert "target" in reply.text
    assert "bahasa biasa" in reply.text


def test_clear_work_requests_pass_through():
    assert local_reply_for("run tests") is None
    assert local_reply_for("fix test yang gagal") is None
    assert local_reply_for("jelaskan repo ini") is None
    assert local_reply_for("jelasin repo ini") is None


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


def test_capability_question_gets_local_reply():
    reply = local_reply_for("kamu bisa apa aja")
    assert reply is not None
    assert reply.reason == "capability"
    assert "baca repo" in reply.text
    assert "run test" in reply.text


def test_permissive_followup_continues_previous_request():
    previous = "buatkan saya web toko kaya shope, di folder baru namanya shooooi"
    assert is_permissive_followup("apa aja, buat di folder baru ya")
    merged = continuation_for("apa aja, buat di folder baru ya", previous)
    assert merged is not None
    assert previous in merged
    assert "reasonable defaults" in merged
    assert "shooooi" in merged


def test_permissive_followup_needs_actionable_previous_request():
    assert continuation_for("apa aja", "halo") is None
    assert continuation_for("apa aja", None) is None

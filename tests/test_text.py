from voiceinput.text import clean


def test_strips_leading_space_and_joins_lines():
    assert clean(" hello there\n world\n") == "hello there world"


def test_drops_annotation_lines():
    assert clean("[BLANK_AUDIO]\nreal words\n(music)") == "real words"


def test_pure_annotation_becomes_empty():
    assert clean("[ Silence ]\n") == ""


def test_keeps_brackets_inside_a_sentence():
    assert clean("call him [name] tomorrow") == "call him [name] tomorrow"


def test_collapses_whitespace():
    assert clean("too    many\t\tspaces") == "too many spaces"


def test_known_hallucination_is_dropped():
    assert clean(" Thank you.") == ""
    assert clean("Субтитры сделал DimaTorzok") == ""


def test_preserves_non_latin_text():
    assert clean(" Привет, как дела?\n") == "Привет, как дела?"
    assert clean(" Բարև ձեզ\n") == "Բարև ձեզ"


def test_empty_input():
    assert clean("") == ""


# --- repetition-loop backstop -------------------------------------------------

from voiceinput.text import has_repetition_loop


def test_detects_short_phrase_loop():
    assert has_repetition_loop("Codort banana. Codort banana. Codort banana.")


def test_detects_doubled_sentence():
    assert has_repetition_loop(
        "The important part is at the very end. The secret word is pomegranate. "
        "The secret word is pomegranate."
    )


def test_clean_speech_is_not_flagged():
    assert not has_repetition_loop(
        "Send him the invoice tomorrow morning and copy the accountant."
    )
    assert not has_repetition_loop("Привет, это проверка голосового ввода.")


def test_single_word_stutter_is_not_flagged():
    # natural speech; too small to be the encoder failure mode
    assert not has_repetition_loop("I really really want this")


def test_short_text_is_safe():
    assert not has_repetition_loop("")
    assert not has_repetition_loop("hello")

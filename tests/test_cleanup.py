from qool.cleanup import Segment, clean_text, is_meaningless, join_segments


def test_negation_preserved():
    s = "ما تبعتش العرض قبل ما أراجع السعر"
    assert clean_text(s) == s + "."


def test_negation_variants_preserved():
    for s in ["مش عايز أروح", "ماكنتش أعرف", "لا لا مش كده", "مفيش مشكلة خالص"]:
        out = clean_text(s)
        assert out.rstrip(".") == s


def test_numbers_and_units_preserved():
    s = "السعر 1,250 جنيه والوزن 3.5 كيلو و 12% خصم يوم 14/9"
    assert clean_text(s).rstrip(".") == s


def test_english_terms_preserved():
    s = "افتح Excel وابعت الـ API key لـ Claude على claude.ai"
    assert clean_text(s).rstrip(".") == s


def test_hesitation_removed():
    assert clean_text("اممم أنا عايز أعمل تطبيق") == "أنا عايز أعمل تطبيق."
    assert clean_text("um I think ممم كده") == "I think كده."


def test_meaningful_words_not_removed():
    s = "يعني بص آه أنا موافق"
    assert clean_text(s).rstrip(".") == s
    # «أمم» كلمة حقيقية (جمع أمة)
    assert "أمم" in clean_text("الأمم المتحدة وكل أمم العالم")


def test_intentional_repetition_kept():
    for s in ["لا لا", "جدا جدا حلو", "بسرعة بسرعة", "هو هو نفس الكلام"]:
        assert clean_text(s).rstrip(".") == s


def test_function_word_stutter_removed():
    assert clean_text("أنا رحت في في الشركة") == "أنا رحت في الشركة."


def test_marked_stutter_removed():
    assert clean_text("أنا ع- عايز أروح") == "أنا عايز أروح."


def test_punctuation_spacing_and_arabic_marks():
    assert clean_text("إزيك , عامل إيه ?") == "إزيك، عامل إيه؟"


def test_hallucination_phrase_removed():
    assert clean_text("تمام كده. ترجمة نانسي قنقر") == "تمام كده."
    assert is_meaningless(clean_text("ترجمة نانسي قنقر"))


def test_existing_terminal_punctuation_not_doubled():
    assert clean_text("تمام؟") == "تمام؟"
    assert clean_text("تمام.") == "تمام."


def test_join_segments_paragraph_only_after_long_pause_and_sentence_end():
    segs = [Segment("أول جملة.", 0, 2), Segment("تانية", 6, 8), Segment("وتالتة", 12, 13)]
    assert join_segments(segs, 3.0) == "أول جملة.\nتانية وتالتة"
    assert join_segments(segs, 0) == "أول جملة. تانية وتالتة"


def test_empty():
    assert clean_text("") == ""
    assert is_meaningless("  ، . ")

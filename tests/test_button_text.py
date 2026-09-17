import pytest

from app.services.buttons import button_words


@pytest.mark.parametrize(
    ("raw", "text", "word", "normalized"),
    [
        ("  Walkies!  ", "Walkies!", "walkies", "walk"),
        ("Outside (back door)", "Outside (back door)", "outside", "outside"),
        ("Play?", "Play?", "play", "play"),
        ("🐶 Eat", "🐶 Eat", "eat", "food"),
        ("I   love  you", "I love you", "i love you", "love you"),
        ("Mommy 🥰", "Mommy 🥰", "mommy", "mom"),
        ("inaudible", "inaudible", "inaudible", "inaudible"),
        ("Hmmm", "Hmmm", "hmm?", "hmm?"),
    ],
)
def test_button_words(raw, text, word, normalized):
    assert button_words(raw) == (text, word, normalized)

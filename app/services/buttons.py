import re
import unicodedata

SYNONYMS = {
    "walkies": "walk",
    "pets": "scritches",
    "scratches": "scritches",
    "eat": "food",
    "mommy": "mom",
    "mama": "mom",
    "mum": "mom",
    "daddy": "dad",
    "dada": "dad",
    "papa": "dad",
    "cuddles": "cuddle",
    "i love you": "love you",
    "bye bye": "bye",
    "byebye": "bye",
    "come here": "come",
    "hello": "hi",
}
_WS = re.compile(r"\s+")
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)?\s*$")
_PUNCT = re.compile(r"[!?.]")
_HMM = re.compile(r"^hm+$")


def strip_emoji(s: str) -> str:
    return "".join(
        c for c in s if unicodedata.category(c) not in ("So", "Sk", "Cf", "Cs", "Co") and c != "️"
    )


def button_words(raw: str) -> tuple[str, str, str]:
    """-> (text, word, normalized_word) per PRD §7 'Button text'."""
    text = _WS.sub(" ", raw).strip()
    word = _WS.sub(" ", strip_emoji(text).lower()).strip()
    word = _PUNCT.sub("", _TRAILING_PAREN.sub("", word)).strip()
    word = _HMM.sub("hmm?", word)
    return text, word, SYNONYMS.get(word, word)

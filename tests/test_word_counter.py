"""Тесты word_counter."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from utils.word_counter import count_words


def test_basic():
    assert count_words("Привет мир") == 2

def test_empty():
    assert count_words("") == 0
    assert count_words("   ") == 0
    assert count_words(None) == 0

def test_numbers_not_counted():
    assert count_words("Стоит 100 рублей") == 2  # только "Стоит" и "рублей"

def test_punctuation_not_counted():
    assert count_words("Привет, мир!") == 2

def test_hyphenated_word():
    assert count_words("само-достаточный") == 1

def test_emoji_not_counted():
    assert count_words("Привет 👋 мир") == 2

def test_mixed():
    text = "Это тест. Здесь 3 слова и 100% правды!"
    # "Это", "тест", "Здесь", "слова", "и", "правды" = 6
    assert count_words(text) == 6

def test_multiline():
    text = "Первая строка\nВторая строка"
    assert count_words(text) == 4

def test_latin():
    assert count_words("Hello world") == 2

def test_mixed_lang():
    assert count_words("Hello мир") == 2

print("Все тесты word_counter пройдены ✅")

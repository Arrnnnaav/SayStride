import unittest
from unittest.mock import patch

import requests

from saystride import rules
from saystride.models import Polisher


class Conformance(unittest.TestCase):
    def test_list_survives_cleanup_model_failure(self):
        config = {"dictionary": [], "chinese_script": "simplified", "tones": {"casual": "clean text"},
                  "learned_signals": [], "cloud_dictation": "off"}
        with patch.object(Polisher, "_complete", side_effect=requests.RequestException("offline")):
            text = Polisher(config).polish("make a shopping list first buying apples, second buying potatoes",
                                          "casual", {})
        self.assertIn("1. buying apples", text)
        self.assertIn("2. buying potatoes", text)

    def test_fast_cleanup_skips_slow_model(self):
        config = {"dictionary": [], "chinese_script": "simplified", "tones": {"casual": "clean text"},
                  "learned_signals": [], "cloud_dictation": "off", "cleanup_mode": "fast"}
        with patch.object(Polisher, "_complete", side_effect=AssertionError("model should not run")):
            text = Polisher(config).polish("make a list first apples, second potatoes", "casual", {})
        self.assertIn("1. apples", text)

    def test_spoken_commands(self):
        self.assertEqual(rules.apply_commands("first line new line second line, new paragraph third"),
                         "first line\nsecond line\n\nthird")
        self.assertEqual(rules.apply_commands("send it to sam at example dot com today"),
                         "send it to sam@example.com today")
        self.assertEqual(rules.apply_commands("that was so funny laugh cry emoji, anyway see you"),
                         "that was so funny 😂 anyway see you")

    def test_restarts(self):
        self.assertEqual(rules.strip_fillers("Um, so, tomorrow is fine. See you then."),
                         "so, tomorrow is fine. See you then.")
        self.assertEqual(rules.strip_fillers("today's weather is very quiet sorry today's weather is very sunny"),
                         "today's weather is very sunny")
        self.assertEqual(rules.strip_fillers("This is fair a fair bit of work"), "This is a fair bit of work")
        self.assertEqual(rules.strip_fillers("bring 5 no 6 chairs"), "bring 6 chairs")
        self.assertEqual(rules.strip_fillers("we we we ship on tuesday tomorrow, tomorrow we look"),
                         "we ship on tuesday tomorrow we look")
        self.assertEqual(rules.strip_fillers("Hey so tomorrow, tomorrow is fine, actually scratch that, let's do Thursday instead."),
                         "let's do Thursday instead.")
        self.assertEqual(rules.strip_fillers("i caught a cold from my partner and yeah she actually he was also sick"),
                         "i caught a cold from my partner and yeah he was also sick")
        self.assertEqual(rules.strip_fillers("look mate i have a thesing what thief i have a few things i want to discuss"),
                         "look mate i have a few things i want to discuss")

    def test_numbers_dictionary_and_layout(self):
        self.assertEqual(rules.apply_numbers("two billion dollars and one point five billion"),
                         "$2 billion and 1.5 billion")
        self.assertEqual(rules.apply_dictionary("zentrow and whisper flow", ["Zentro: zentrow", "Wispr Flow: whisper flow"]),
                         "Zentro and Wispr Flow")
        self.assertEqual(rules.counted_lists("Three things: it's fast, it's accurate, it gets names right."),
                         "Three things:\n1. it's fast\n2. it's accurate\n3. it gets names right")
        self.assertEqual(rules.apply_lists("buy first tomatoes, second potatoes, third an apple"),
                         "buy\n1. tomatoes\n2. potatoes\n3. an apple")
        self.assertEqual(rules.apply_lists("create a list first? buying apple, second buying ice cream"),
                         "create a list\n1. buying apple\n2. buying ice cream")
        self.assertTrue(rules.question_marks("are you coming").endswith("?"))
        self.assertEqual(rules.layout_message("dear robin, no worries at all, i can deal with it this weekend. kind regards, jess"),
                         "dear robin,\n\nno worries at all, i can deal with it this weekend.\n\nkind regards,\njess")
        self.assertEqual(rules.casual("Sounds good, see you at eight. Bring the charger.", [], True),
                         "sounds good see you at eight. bring the charger")

    def test_learning_and_whisper(self):
        self.assertEqual(rules.learn_names("i've done zentrow", "i've done Zentro"), [("Zentro", "zentrow")])
        self.assertEqual(rules.merge_dictionary(["Zentro: zentrow"], [("Zentro", "zen tro")]),
                         ["Zentro: zentrow, zen tro"])
        self.assertEqual(rules.filter_whisper("Thank you.", None, 3), "")

    def test_guard_and_wer(self):
        self.assertTrue(rules.looks_unfaithful("we ship on thursday and then do the reel for instagram",
                                               "Sure, here is a cleaned version of your message.", []))
        self.assertEqual(rules.word_error_rate("Hello world", "hello world"), 0)
        self.assertEqual(rules.word_error_rate("we ship on thursday", "we ship on tuesday tomorrow"), .5)


if __name__ == "__main__":
    unittest.main()

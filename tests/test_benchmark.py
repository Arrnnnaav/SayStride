import unittest

from benchmark_asr import term_counts


class BenchmarkTest(unittest.TestCase):
    def test_technical_term_recall_counts_expected_mentions(self):
        self.assertEqual(
            term_counts("Open VS Code and run kubectl. Then run kubectl again.",
                        "Open VS Code and run cube control. Then run kubectl again.",
                        ["VS Code", "kubectl"]),
            (2, 3),
        )


if __name__ == "__main__":
    unittest.main()

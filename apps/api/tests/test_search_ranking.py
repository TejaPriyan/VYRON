import unittest

from vyron_api.graph.search import MIN_SCORE, normalize, score_entity, score_text


class NormalizeTest(unittest.TestCase):
    def test_normalization(self) -> None:
        cases = {
            "Node.js": "node js",
            "  Next.JS ": "next js",
            "C++": "cplusplus",
            "C#": "csharp",
            "C": "c",
            "Café": "cafe",
            "scikit-learn": "scikit learn",
            "": "",
            "!!!": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize(raw), expected)

    def test_cpp_and_c_stay_distinct(self) -> None:
        self.assertNotEqual(normalize("C++"), normalize("C"))
        self.assertNotEqual(normalize("C#"), normalize("C"))


class ScoreTextTest(unittest.TestCase):
    def test_tiers_are_ordered(self) -> None:
        exact = score_text("python", "python")
        compact = score_text("nextjs", "next js")
        prefix = score_text("pyth", "python")
        tokens = score_text("js", "next js")
        substring = score_text("ytho", "python")
        fuzzy = score_text("pyhton", "python")
        self.assertEqual(exact, 100.0)
        self.assertEqual(compact, 98.0)
        self.assertEqual(prefix, 80.0)
        self.assertEqual(tokens, 70.0)
        self.assertEqual(substring, 40.0)
        self.assertTrue(20.0 <= fuzzy < 40.0)

    def test_no_match(self) -> None:
        self.assertEqual(score_text("zzz", "python"), 0.0)
        self.assertEqual(score_text("", "python"), 0.0)
        self.assertEqual(score_text("python", ""), 0.0)

    def test_short_queries_do_not_fuzzy_match(self) -> None:
        self.assertEqual(score_text("pz", "python"), 0.0)


class ScoreEntityTest(unittest.TestCase):
    def test_alias_and_slug_matches(self) -> None:
        self.assertEqual(score_entity("k8s", "Kubernetes", ["K8s"]), 95.0)
        self.assertEqual(score_entity("next-js", "Next.js"), 100.0)  # name already matches
        self.assertEqual(score_entity("next-js", "Unrelated Name", [], "next-js"), 90.0)

    def test_below_threshold_is_zero(self) -> None:
        self.assertEqual(score_entity("qqqq", "Python"), 0.0)
        self.assertGreaterEqual(score_entity("pyhton", "Python"), MIN_SCORE)

    def test_blank_query_matches_nothing(self) -> None:
        self.assertEqual(score_entity("   ", "Python"), 0.0)
        self.assertEqual(score_entity("---", "Python"), 0.0)


if __name__ == "__main__":
    unittest.main()

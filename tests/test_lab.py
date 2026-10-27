import unittest

from lab.measure import run_lab


class LabTest(unittest.TestCase):
    def test_short_lab_meets_criterion(self):
        res = run_lab(bug_runs=1, clean_runs=3)
        self.assertGreaterEqual(res["detected_all_runs"], 7, res["bugs"])
        self.assertEqual(res["false_positives"], 0, res["clean_non_pass"])


if __name__ == "__main__":
    unittest.main()

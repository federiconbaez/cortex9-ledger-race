"""Run the whole suite (python -m tests.run); a skipped test is a failure."""
import sys
import unittest

result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover("tests", top_level_dir="."))
print(f"tests: {result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)}"
      f"/{result.testsRun} passed, skipped={len(result.skipped)}")
sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import doc_review  # noqa: E402

DECL = {"README.md": ["plugins/**", "Makefile"], "plugins/x/README.md": ["plugins/x/**"]}


class ReviewLinesTests(unittest.TestCase):
    def test_match(self):
        out = doc_review.review_lines(["plugins/a/b.py", "plugins/a/c.py", "Makefile"], DECL)
        self.assertEqual(out, ["review: README.md describes 3 changed files (e.g. Makefile, plugins/a/b.py); "
                               "read it and confirm it is current, or update it"])

    def test_no_match(self):
        self.assertEqual(doc_review.review_lines(["doc/other.md"], DECL), [])

    def test_doc_excluded_from_own_globs(self):
        self.assertEqual(doc_review.review_lines(["plugins/x/README.md"], DECL),
                         ["review: README.md describes 1 changed files (e.g. plugins/x/README.md); "
                          "read it and confirm it is current, or update it"])

    def test_no_main(self):
        orig = doc_review.changed_paths
        doc_review.changed_paths = lambda: None
        try:
            self.assertEqual(doc_review.main(), 0)
        finally:
            doc_review.changed_paths = orig


if __name__ == "__main__":
    unittest.main()

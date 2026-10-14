#!/usr/bin/env python3
"""check_generated_docs.image_problems: `make docs-check` fails when README names an image under doc/images/ that does
not exist (a check, not a reminder; nothing is rendered)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_generated_docs import image_problems  # noqa: E402


class ImageProblemsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "doc/images").mkdir(parents=True)
        (self.root / "doc/images/there.png").write_bytes(b"png")

    def problems(self, text: str) -> list[str]:
        (self.root / "README.md").write_text(text)
        return image_problems(self.root / "README.md", self.root)

    def test_every_referenced_image_exists_is_fine(self):
        self.assertEqual(self.problems('<source srcset="doc/images/there.png">\n![x](doc/images/there.png)\n<img src="./doc/images/there.png">'), [])

    def test_a_missing_image_is_named_in_any_form(self):
        for text in ['<source srcset="doc/images/gone.png" media="x">', "![alt](doc/images/gone.png)", '<img src="./doc/images/gone.png" alt="">']:
            with self.subTest(text=text):
                got = self.problems(text)
                self.assertEqual(len(got), 1)
                self.assertIn("doc/images/gone.png", got[0])

    def test_the_repository_readme_names_only_images_that_exist(self):
        repo = Path(__file__).resolve().parents[2]
        self.assertEqual(image_problems(repo / "README.md", repo), [])


if __name__ == "__main__":
    unittest.main()

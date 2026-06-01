import unittest
from pathlib import Path


class DeployHygieneTests(unittest.TestCase):
    def test_gcloudignore_excludes_local_secrets_and_review_artifacts(self) -> None:
        ignore = Path(".gcloudignore").read_text(encoding="utf-8").splitlines()

        for pattern in {
            ".env",
            ".env.*",
            "!.env.example",
            "traceguard-server-*.log",
            "traceguard-start.*",
            "traceguard-ui-review.*.log",
            "ui-review-artifacts/",
        }:
            self.assertIn(pattern, ignore)

    def test_dockerignore_and_gitignore_exclude_local_review_artifacts(self) -> None:
        for path in (".dockerignore", ".gitignore"):
            ignore = Path(path).read_text(encoding="utf-8").splitlines()
            for pattern in {
                ".env",
                ".env.*",
                ".gcloud/",
                ".secrets/",
                "traceguard-start.*",
                "traceguard-ui-review.*.log",
                "ui-review-artifacts/",
            }:
                self.assertIn(pattern, ignore, f"{pattern} missing from {path}")


if __name__ == "__main__":
    unittest.main()

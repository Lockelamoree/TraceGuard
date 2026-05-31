import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


class IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.classes: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)
            if name == "class" and value:
                self.classes.extend(value.split())


class FrontendStaticTests(unittest.TestCase):
    def test_judge_proof_sections_and_runtime_badges_are_present(self) -> None:
        html = Path("web/index.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        parser = IdCollector()
        parser.feed(html)

        self.assertIn("proof-strip", parser.classes)
        self.assertIn("workflow-strip", parser.classes)
        self.assertIn("scoreboard", parser.classes)
        self.assertIn("Security triage agent", html)
        self.assertIn("proofScoreboard", parser.ids)
        self.assertIn("MCP live query", app_js)
        self.assertIn("Phoenix OTEL live", app_js)
        self.assertIn("Gemini live", app_js)
        self.assertIn("unsupported_confirmed_claims", app_js)

    def test_frontend_ids_are_unique_and_script_selectors_exist(self) -> None:
        html = Path("web/index.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        parser = IdCollector()
        parser.feed(html)

        ids = parser.ids
        self.assertEqual(len(ids), len(set(ids)))

        referenced_ids = set(re.findall(r'querySelector\("#([A-Za-z0-9_-]+)"\)', app_js))
        missing = referenced_ids - set(ids)
        self.assertFalse(missing, f"Missing DOM ids referenced by app.js: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()

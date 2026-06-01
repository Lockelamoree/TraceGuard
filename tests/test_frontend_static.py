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

        self.assertIn("context-popover", parser.classes)
        self.assertIn("context-trigger", parser.classes)
        self.assertIn("context-tooltip", parser.classes)
        self.assertIn("receipt-popover", parser.classes)
        self.assertIn("receipt-trigger", parser.classes)
        self.assertIn("receipt-tooltip", parser.classes)
        self.assertIn("story-strip", parser.classes)
        self.assertIn("criteria-rail", parser.classes)
        self.assertIn("receipt-grid", parser.classes)
        self.assertIn("demo-steps", parser.classes)
        self.assertIn("scoreboard", parser.classes)
        self.assertIn("arize-loop", parser.classes)
        self.assertIn("Security triage agent", html)
        self.assertIn("30 second judge story", html)
        self.assertIn("Cloud Run + Vertex AI Gemini", html)
        self.assertIn("ADK-compatible root_agent", html)
        self.assertIn("Phoenix OTEL + MCP receipts", html)
        self.assertIn("Unsupported claims stay out", html)
        self.assertIn("Judge context", html)
        self.assertIn("Judge receipt", html)
        self.assertIn("role=\"tooltip\"", html)
        self.assertIn("aria-describedby=\"judgeContextTooltip\"", html)
        self.assertIn("Demo path", html)
        self.assertIn("Phoenix status receipt", html)
        self.assertIn("Grounding receipt", html)
        self.assertIn("judgeContextButton", parser.ids)
        self.assertIn("judgeContextTooltip", parser.ids)
        self.assertIn("judgeEvidenceReceipt", parser.ids)
        self.assertIn("judgeGroundingReceipt", parser.ids)
        self.assertIn("judgePhoenixReceipt", parser.ids)
        self.assertIn("proofScoreboard", parser.ids)
        self.assertIn("arizeLoop", parser.ids)
        self.assertIn("evidenceStats", parser.ids)
        self.assertIn("customSampleButton", parser.ids)
        self.assertIn("customSampleFile", parser.ids)
        self.assertIn("customSampleStatus", parser.ids)
        self.assertIn("Upload sample", html)
        self.assertIn("type=\"file\"", html)
        self.assertIn("aria-describedby=\"customSampleStatus\"", html)
        self.assertIn("Phoenix status receipt", html)
        self.assertIn("next-run improvement", html)
        self.assertIn("renderArizeLoop", app_js)
        self.assertIn("renderLoopCard", app_js)
        self.assertIn("Improvement receipt", app_js)
        self.assertIn("improvementReceipt", app_js)
        self.assertIn("Improve plan", app_js)
        self.assertIn("observability_derived", app_js)
        self.assertIn("next_run_change", app_js)
        self.assertIn("Run receipt", app_js)
        self.assertIn("Phoenix MCP", app_js)
        self.assertIn("phoenix_project", app_js)
        self.assertIn("MCP live query", app_js)
        self.assertIn("Phoenix OTEL live", app_js)
        self.assertIn("Gemini live", app_js)
        self.assertIn("unsupported_confirmed_claims", app_js)
        self.assertIn("loadProofReceipt", app_js)
        self.assertIn("updateJudgeReceiptFromProof", app_js)
        self.assertIn("updateJudgeReceiptFromResult", app_js)
        self.assertNotIn("94% eval avg", html)
        self.assertNotIn("0 unsupported claims", html)
        self.assertIn("aria-busy=\"false\"", html)
        self.assertIn("aria-live=\"polite\"", html)
        self.assertIn("class=\"field-label\" for=\"evidence\"", html)
        self.assertIn("aria-describedby=\"evidenceHint evidenceStats\"", html)
        self.assertIn("Run baseline", html)
        self.assertNotIn("class=\"subtitle\"", html)

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

    def test_stale_results_clipboard_and_mobile_fixes_are_present(self) -> None:
        app_js = Path("web/app.js").read_text(encoding="utf-8")
        css = Path("web/styles.css").read_text(encoding="utf-8")

        self.assertIn("findings.innerHTML = \"\";", app_js)
        self.assertIn("evals.innerHTML = \"\";", app_js)
        self.assertIn("findingCount.textContent = \"0 findings\";", app_js)
        self.assertIn("Clipboard blocked by the browser", app_js)
        self.assertIn("runConsole.setAttribute(\"aria-busy\"", app_js)
        self.assertIn("markRunPending", app_js)
        self.assertIn("copyReportButton.disabled = isBusy || !lastReport", app_js)
        self.assertIn("CUSTOM_SAMPLE_MAX_BYTES", app_js)
        self.assertIn("CUSTOM_SAMPLE_ALLOWED_EXTENSIONS", app_js)
        self.assertIn("CUSTOM_SAMPLE_SECRET_PATTERNS", app_js)
        self.assertIn("handleCustomSampleUpload", app_js)
        self.assertIn("validateCustomSampleFile", app_js)
        self.assertIn("validateCustomSampleText", app_js)
        self.assertIn("findLikelySecret", app_js)
        self.assertIn("Upload blocked: possible", app_js)
        self.assertIn(".visually-hidden", css)
        self.assertIn(".skip-link", css)
        self.assertIn(".upload-status", css)
        self.assertIn(".context-trigger:hover + .context-tooltip", css)
        self.assertIn(".context-trigger:focus-visible + .context-tooltip", css)
        self.assertIn(".context-tooltip .receipt-tooltip", css)
        self.assertIn(".story-strip", css)
        self.assertIn(".criteria-rail", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn(".field-guide", css)
        self.assertIn(".tooltip-trigger", css)
        self.assertIn(".receipt-grid", css)
        self.assertNotIn("scroll-snap-type: x proximity", css)
        self.assertIn("min-height: 220px", css)
        self.assertIn("updateEvidenceStats", app_js)


if __name__ == "__main__":
    unittest.main()

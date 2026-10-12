"""Deterministic, narrow QA checks for the editorial portfolio.

Important: passing this check NEVER substitutes for rendered screenshot review,
client art-direction approval, performance testing, or accessibility testing.
"""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parent
html=(ROOT/"index.html").read_text(encoding="utf-8")
css=(ROOT/"style.css").read_text(encoding="utf-8")
js=(ROOT/"app.js").read_text(encoding="utf-8")
motion=(ROOT/"motion.js").read_text(encoding="utf-8")

class Collect(HTMLParser):
    def __init__(self):
        super().__init__();self.tags=[];self.attrs=[];self.ids=set()
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag); d=dict(attrs);self.attrs.append((tag,d))
        if d.get("id"):
            if d["id"] in self.ids: raise AssertionError("Duplicate id: "+d["id"])
            self.ids.add(d["id"])
doc=Collect();doc.feed(html)
required={"main","nav","h1","h2","button","section","svg"}
assert required.issubset(set(doc.tags))
for key in ("top","main","proof-one","proof-two","paper","layer-panel","decision-word","guard-verdict","read-progress"):
    assert key in doc.ids,key
for path in ("style.css","app.js","motion.js"):
    assert (ROOT/path).is_file(), path
    assert "./"+path in html, path
assert "prefers-reduced-motion" in css
assert "focus-visible" in css and "skip" in css
assert 'aria-live="polite"' in html
assert 'aria-label="Primary navigation"' in html
assert "aria-controls" in html and "ArrowRight" in js and "ArrowLeft" in js
assert "aria-pressed" in js and "model" not in js.split("// A local visual demonstration")[0].lower()
assert "0 denied host dispatches" in js
assert "isMobile" not in css
assert "480px" not in css or "@media" in css
assert "src=\"./" in html
assert 'target="_blank" rel="noopener noreferrer"' in html
assert "14 LAYERS" in html and "7 LOOPS" in html
assert "kavi-capability-compiler/blob/main/examples/recruiter_demo.py" in html
assert "spectraflow/blob/main/examples/reliability_interview_demo.py" in html
assert "ANATOMY_OF_PRODUCTION_AGENTIC_SYSTEMS_2026.md" in html
assert "tiny-orbit" not in html, "Decorative orbit returned"
assert "data-operation=\"read\"" in html and "data-operation=\"delete\"" in html
assert "data-layer=\"control\"" in html and "data-layer=\"knowledge\"" in html
assert len([t for t,d in doc.attrs if t=="a" and (d.get("href") or "").startswith("#")])>=5
external=[d.get("href") for t,d in doc.attrs if t=="a" and (d.get("href") or "").startswith("https://")]
assert external and all(urlparse(x).scheme=="https" for x in external)
assert not any("higgsfield" in x.lower() for x in external)
print("KAVI_V2_STRUCTURE_CHECKS: PASS")
print("Visual screenshot QA + interaction walkthrough still REQUIRED before production.")

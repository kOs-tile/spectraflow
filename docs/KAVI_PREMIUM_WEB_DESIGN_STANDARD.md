# KAVI Premium Web — Anti-Slop Design & Release Contract v1
**2026-10-12 · applies to all future KAVI premium websites and cinematic portfolios**

## The failure that prompted this
The original engineering portfolio launched from a generic SaaS recipe: left-aligned hero + decorative orbit; feature-card pair; monochrome accent; general-purpose reveal animations. CI was green and links worked, but the visual language carried no unique narrative, no identifiable brand signature, and no scene-to-scene continuity. That **must not be judged as a premium design just because it is functional**.

## Absolute gates

1. **Direction before markup.** Write a one-sentence visual thesis, what makes the site specific to that customer/research, and explicitly reject at least two tempting stock design solutions.
2. **Storyboarding before motion.** For each scene define: evidence/content job, spatial composition, dominant type scale, primary visual, scroll timing/hold, entry/exit, and physical handoff into the next scene.
3. **No uncontrolled pattern reuse.** Flag generic SaaS/HUD motifs: orbit-donut graphic with no information role; two/three generic feature cards; product UI dashboards as decoration; stock gradients/particles; repeated chip badges; isolated fade-up boxes; overused black/neon AI style. Some may be justified for a specific client; none may be default.
4. **Motion is causality.** Motion should communicate a decision, transformation, dependency, or proof, not simply announce arrival. Keep composition continuity. Use progress-controlled reversibility for scroll choreography when complex, with reduced-motion fallbacks.
5. **Source-backed claims.** Separate synthetic demos, historical trace evidence, and production results. No decorative numerical KPIs.
6. **Rendered visual QA is mandatory.** Capture and **inspect** desktop 1440x900 and mobile 390x844 screenshots at the top, midpoint and ending, plus a short scroll recording. Test interactive states and keyboard focus. Broken layering, clipping, lines too long, giant dead areas, or card template repetition block promotion.
7. **Functional QA is different.** Test links, 404s, console errors, source references, responsive overflow, reduced motion, basic contrast/semantics, and performance. Successful syntax/unit tests are not visual acceptance.
8. **No public overwrite without review.** Push redesign to isolated PR/preview. Only replace approved customer production (e.g. Pulse V22) or the established KAVI homepage when a captured visual comparison meets the client-specific target and is approved. Preserve the last good deployment and easy rollback.
9. **No paid generation without approval.** In particular, do not spend Higgsfield credits without user approval. Prefer deterministic SVG, CSS, JS, GSAP/ScrollTrigger and Remotion.
10. **Design sign-off by visible evidence.** A credible reviewer can describe the bespoke visual thesis without reading the source. If the rendered prototype could pass for an arbitrary AI startup, return to art direction and rebuild—not a color change.

## Design thesis for this specific KAVI portfolio V2

**"An annotated engineering dossier in which every animation represents an execution boundary."**

Audience: hiring engineers, founders and technically literate reviewers. Primary job: understand KCC authorization and SPECTRAFLOW reliability in under three minutes, access executable evidence, then read the 14-layer architecture research.

### Rejected directions
- SaaS dashboard / dark HUD / generic gradient + card grid.
- Pure kinetic typography with no inspectable engineering demos.
- Fake 3D brain / particle neural network.

### Scene contracts (approved as implementation **hypotheses**, not visual QA approval)

| Scene | Content job | Composition and motion | Transition |
|---|---|---|---|
| 00 Intro | Establish the precise dual question: authorization and verified completion | Monumental editorial serif/sans typographic folds, one purposeful loop tied to reading progress; deliberate hold | Conceptual hinge: "permission ≠ intelligence" |
| 01 Manifesto | Create technical context without marketing | Large dark-on-paper typography, narrow margin annotation, two supporting paragraphs | Chapter index opens the first apparatus |
| 02 KCC authority | Make denied dispatch legible | Bespoke node/wire drawing and allowed vs denied request toggle; the packet physically stops at the Guard | Signed authority leads into effect ambiguity |
| 03 Interlude | Transition between permission and reliability | One oversized statement and a held breath; no card | Reveals lost acknowledgment |
| 04 Reliability | Show real failure mode | Two tracks share a timeline; blind second commit vs idem reused receipt, accessible replay | Evidence requires a real verification contract |
| 05 Research | Show complete system as bounded domains | Four-choice interactive architecture diagram, followed by memory/graph distinction | Thesis becomes imperative |
| 06 Close | Help reviewer take action | Source links, no unsupported claims | Clear conclusion |

### Acceptance matrix
- Visual: independent screenshot review, no generic hero orbit, no two-card default, no cropping at mobile widths.
- UX: mobile source links are readable and reachable; interactive states visibly distinct; tab arrows and buttons keyboard operable.
- Accessibility: prefers-reduced-motion; contrast review; meaningful SVG title; focus and skip links.
- Engineering: isolated static page; no third-party runtime dependencies; source links verified; no live runtime mutation.
- Content: every claim aligned to repository evidence; no claim that 80 live experiments passed.
- Release: production currently **unchanged**, PR preview only until visual acceptance.

## Process change for every future KAVI site
**Brief → 3 contrasting directions → explicit art direction → scene storyboard → visual keyframes → implementation → contact sheets / desktop-mobile QA → critical independent review → user approval → publish.**

Do not compensate for missing art direction by multiplying CSS animations.

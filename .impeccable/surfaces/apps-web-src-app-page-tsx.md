---
version: 1
slug: "apps-web-src-app-page-tsx"
primary_target: "apps/web/src/app/page.tsx"
related_targets: ["apps/web/src/app/kitchen/page.tsx","apps/web/src/app/console/page.tsx","apps/web/src/app/order-status/[orderId]/page.tsx"]
---

# Surface: COUNTER web app (call + menu board, customer status, kitchen, console, login, menu import)

Scope: all routes in apps/web. Modes: call page Persuade-leaning Operate (judges + callers); kitchen, console, onboarding Operate; status Read/Operate.
Audience: callers ordering by voice; cook at a kitchen screen; owner at a laptop; hackathon judges (product first, must land at first sight).
Constraints: real API data only; PayPal checkout on PayPal; stand-in photos (Wikimedia Commons, credited) listed in report; WCAG 2.1 AA.

## Direction contract

THESIS: A takeaway's order board, not a SaaS dashboard: the menu is a Pinterest-style board of photo pins, and the order is a carbon ticket clipped to a steel rail that only gets its PAID stamp when PayPal confirms. Refuses the cream-serif-terracotta "artisan" default and the grey card dashboard.

OWN-WORLD: White board ground, near-black ink, cool steel greys; pins are full-bleed photos with 20px radius and the name/price set under them; tickets are pale canary carbon paper (#FFF3B0) with a perforated top edge hanging from a brushed-steel rail; state colour only where it means money: paid green, pending amber, declined red; PayPal's own yellow button. Type: Schibsted Grotesk (bold, tight) for everything; Azeret Mono only for ticket numerals and amounts. Pill buttons in ink.

STORY: The caller sees the real menu as photographs, taps a pin to say it, talks to the agent, watches their ticket fill line by line, pays on PayPal, and sees the ticket stamped PAID. The cook sees only stamped tickets on the rail. The owner sees every ticket's truth.

FIRST VIEWPORT: Call page at 1440: slim white top bar (wordmark left, nav right). Left two-thirds: one-line headline "Order by voice. Paid before the oven's lit." and a short fact line, then the masonry board of menu pins starting above the fold. Right third, sticky: the call panel (number field → transcript → input with mic) above the ticket hanging from the rail; primary action "Start the call" in ink pill.

FORM: Pinned by the user brief (Pinterest-like, photo-led), so no concept roll was run (a brief-pinned direction beats the roll); signature move from the takeaway's world: the kitchen ticket rail with the PAID stamp. Kitchen adaptation: dark steel ground for a screen read at a distance in a bright kitchen. seed: none (direction pinned by brief; roll not run)

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

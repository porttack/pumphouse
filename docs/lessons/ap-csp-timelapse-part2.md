# AP CSP Lesson Unit — Part 2 of 2: From Single Server to Global Edge Infrastructure
### A Real-World Project-Based Unit on Computer Science Principles

**Course:** AP Computer Science Principles (CS50-based)
**Total Time:** 4 sessions × 30 minutes
**Continues from [Part 1](ap-csp-timelapse-part1.md)**
**Central Project:** A Raspberry Pi sunset timelapse system that grew from one image into a
full CDN-backed, edge-computed, Zero Trust video platform.
**Driving Question:** *How does a single photo become a globally distributed, edge-computed,
cached media platform — and what CS concepts explain every step?*

**AP CSP Big Ideas Covered:** CSN · IOC · DAT · distributed systems · IaC

**Project arc (this part — steps 8–15):**

8. Domain purchase, DNS, Cloudflare as CDN
9. Cloudflare Zero Trust Tunnel (outbound-only, no open ports, identity-verified)
10. Cloudflare Workers (edge compute intercepting ratings API calls)
11. Cloudflare KV store (distributed key-value store for star ratings)
12. Cache-Control headers strategy (immutable past pages, short TTL for today)
13. Client-side vs. server-side rendering tradeoffs (driven by cacheability requirements)
14. Security: rotating API tokens, `.gitignore` secrets, firewall hardening
15. Infrastructure as Code (Terraform managing all Cloudflare configuration)

---

## SIDEBAR: Zero Trust — What It Means and Why It Matters

### The Old Model: Castle and Moat

Traditional network security assumed that everything *inside* your network perimeter was safe.
The firewall was the moat. If you got inside — as an employee, an authorized device, or an attacker
who found one open port — you were largely trusted to talk to any resource on the internal network.

This works until it doesn't. One compromised device inside the perimeter can move laterally to attack
everything else. One misconfigured firewall rule exposes a service. One credential stolen over VPN
gives an attacker the keys to the kingdom.

### Zero Trust: "Never Trust, Always Verify"

Zero Trust (a term coined by Forrester Research, popularized by Google's "BeyondCorp" project) inverts
the assumption. **No network location is inherently trusted** — not your home network, not your
corporate LAN, not even localhost. Every connection must:

- **Identify** itself (who are you?)
- **Authenticate** (prove it)
- **Be authorized** for the specific resource requested (are you allowed to do this?)

### How the Timelapse Project Uses Zero Trust

| Traditional (Port Forwarding) | Zero Trust (Cloudflare Tunnel) |
|-------------------------------|-------------------------------|
| Port 443 open on router | No ports open anywhere |
| Anyone on the internet can attempt a connection | Only Cloudflare's servers can reach the Pi |
| Pi must evaluate every inbound request | Cloudflare filters before traffic reaches Pi |
| Home IP address visible to the world | Home IP address never exposed |
| DDoS attack hits the Pi directly | DDoS absorbed by Cloudflare's infrastructure |

The Pi runs `cloudflared` — a small daemon that makes a persistent **outbound** connection to
Cloudflare. Cloudflare forwards authenticated requests *through* that tunnel. An attacker on the
internet cannot initiate a connection to the Pi at all — there is no port to knock on.

This is the Zero Trust principle applied at the network layer: *the Pi does not trust inbound
connections, so it accepts none.*

### Zero Trust at the Application Layer

The ratings Worker also embodies Zero Trust at the application layer:
- Every rating submission is validated (correct date format? valid star value?)
- Cookie-based deduplication prevents one user from submitting unlimited ratings
- The Worker rejects anything that doesn't match the expected schema

Zero Trust isn't a single product — it's a design philosophy. Ask: *"What is the minimum access
this component needs, and how do we verify every request rather than trusting network location?"*

### Discussion Questions

1. Your school's network probably uses the castle-and-moat model (a firewall at the edge, trust
   inside). What are the risks of that model for a school environment?
2. Google requires employees to authenticate to every internal service individually, even on the
   corporate network. What is the security benefit? What is the usability cost?
3. The Cloudflare Tunnel means Cloudflare can see all traffic to the Pi. We traded one trust
   assumption (open ports) for another (trusting Cloudflare). Was that a good trade?

---

---

## Session 5 (30 min): "What Is a CDN and How Does It Protect the Pi?"

**Learning Objectives:**
- Explain what a CDN is and why latency decreases when content is served from the nearest edge node
- Describe what a DoS attack is and explain why a residential Pi is vulnerable
- Explain how cache-busting works and what the "Ignore Query String" rule mitigates

**AP CSP Alignment:** CSN-1.A, CSN-1.E, IOC-2.A

### Hook (3 min)

"What happens if a journalist tweets this link? Or a bored teenager decides to hit the 'Now' button 10,000 times?"

---

### Beat 1 — Physics of a CDN (6 min)

- Speed of light in fiber: ~200,000 km/s. Oregon to London is ~8,000 km = ~40ms one-way minimum.
- Without a CDN, every London visitor waits ~80ms just for the first byte — before any processing.
- A CDN puts a copy of the content in London (and Tokyo, New York, etc.). London user → London edge = ~1ms.
- Cloudflare uses **Anycast routing**: the same IP address is announced from 200+ cities simultaneously. Your request goes to the closest one automatically.

**Whiteboard:** Draw a world map with Pi in Oregon and edge nodes in 6 cities. Trace request paths.

---

### Beat 2 — The Cache (6 min)

- Cloudflare stores a copy of each file at each edge node. Cache hit: Pi not contacted. Cache miss: Pi fetched once, result stored.
- `Cache-Control: max-age=31536000` on past MP4s means Cloudflare stores them for 1 year — effectively forever. Pi serves each MP4 once per edge node per year.
- HTML pages are different: Cloudflare doesn't cache `text/html` by default (it assumes HTML is dynamic). The "Cache Everything" rule overrides this for `/timelapse/20*` pages.
- The timelapse Pi sends different TTLs: 5 minutes for today/yesterday (content changes during recording), 1 hour for older pages (stable). Cloudflare respects these.
- The `/snapshot` route: the Worker enforces a 5-min TTL and strips `Cache-Control: no-cache` headers that browsers send — so even a hard-refresh cannot force a new Pi hit within the 5-min window.

---

### Beat 3 — DoS: What It Is and Why the Pi Is Vulnerable (8 min)

- **DoS (Denial of Service):** sending so much traffic to a server that it can't respond to legitimate users.
- The Pi has a residential upload speed of roughly 10–50 Mbps. A single timelapse MP4 is ~4 MB.
- **Math problem for students:** If 100 requests/second each require serving a 4 MB file, how much bandwidth does the Pi need? (Answer: 400 MB/s = 3,200 Mbps — 64× more than available.)
- So a CDN with caching is the main defense: if Cloudflare serves from cache, the Pi serves 0 bytes.
- But there is a subtle attack: **cache-busting with query strings.** Cloudflare's default cache key includes the full URL. `/video.mp4?x=1` and `/video.mp4?x=2` are DIFFERENT cache entries. An attacker appending random `?` params bypasses the cache entirely on every request.
- Show the fix: a Cloudflare Cache Rule with "Ignore Query String" makes all variants resolve to the same cache entry. This is documented in the project's `docs/timelapse.md`.

---

### Discussion (5 min)

1. A CDN serves cached copies. If the Pi crashes, what happens to visitors? What's the difference between a cache hit and a cache miss scenario?
2. The "Ignore Query String" rule is applied to `/timelapse/*` but NOT to `/snapshot`. Why might `/snapshot?info=0` (raw JPEG) and `/snapshot?info=1` (HTML page) need to remain separate cache entries?
3. Cloudflare absorbs DDoS attacks before they reach the Pi. But Cloudflare has had its own outages. Is trading one point of failure for another a good deal?

**AP Exam tie-in:** CSN-1.E (fault tolerance and redundancy); DDoS definition; cache and latency tradeoffs.

---

## Session 6 (30 min): "Edge Computing — Code Running in 200 Cities Simultaneously"

**Learning Objectives:**
- Describe the difference between server-side, serverless, and edge compute
- Read and explain a short Cloudflare Worker at a conceptual level
- Explain what Infrastructure as Code is and why it matters for reproducibility

**AP CSP Alignment:** CSN-1.A, DAT-2.C, IOC-1.A

### Hook (2 min)

"What if your code ran in London before the request even reached Oregon? What if it ran in London, Tokyo, and São Paulo simultaneously, with no servers to manage?"

---

### Beat 1 — Three Models of Running Code (7 min)

- **Traditional server:** code runs on one machine. Fast for local users; slow for far-away users. If the machine crashes, the service is down.
- **Cloud functions (Lambda/serverless):** code runs on-demand in a data center — one region. Still has a "cold start" problem: spinning up a container takes 100–1,000ms for the first request.
- **Edge compute:** code runs at CDN edge nodes. Cloudflare Workers use V8 JavaScript isolates (the same engine as Chrome) — no container, no cold start, ~0ms startup. Deployed to 200+ cities.
- Limitations: no filesystem access, 30ms CPU per request, no long-running processes. Workers are request interceptors, not general-purpose servers.

---

### Beat 2 — How a Cloudflare Worker Works (8 min)

A Worker is a JavaScript function: `fetch(request, env) → Response`

It receives every HTTP request before it reaches the Pi tunnel. It can: return a Response itself (handle the request completely), or call `fetch(request)` to pass through to the Pi.

The four routes our Worker handles:

1. `GET /snapshot` or `/frame` → fetch from Pi with `cf.cacheEverything: true`; strip cache-bypass headers so browsers can't force a Pi hit; add `X-Snapshot-Worker: v2` header for debugging
2. `GET /api/ratings/YYYY-MM-DD` → read from Cloudflare KV; return JSON; Pi not contacted
3. `POST /timelapse/YYYY-MM-DD/rate` → check cookie (already rated?); if not, read KV, add rating, write back; set cookie; return updated stats
4. Everything else → `return fetch(request)` (pass through to tunnel)

Simplified routing logic from the actual Worker:

```javascript
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/snapshot' && request.method === 'GET') {
      return handleSnapshot(request, url);        // 5-min CDN cache
    }
    if (url.pathname.match(/^\/api\/ratings\//)) {
      return handleGet(dateStr, env);             // read from KV
    }
    if (url.pathname.match(/^\/timelapse\/.*\/rate$/)) {
      return handlePost(dateStr, request, env);   // write to KV
    }
    return fetch(request);                        // pass through to Pi
  }
};
```

---

### Beat 3 — Cloudflare KV: Distributed State (5 min)

- KV = key-value store. Key: `"2026-02-15"`. Value: `{"count": 7, "sum": 34}`.
- Distributed: written in one city, readable from all 200+ cities (eventually consistent — see glossary).
- Why not a SQL database? For ratings, we only ever look up by date — no complex queries needed. KV is faster for exact key lookups.
- The Pi also maintains a local `ratings.json` mirror for when accessed directly (without Cloudflare).
- Cookie deduplication: the Worker checks `tl_rated_YYYY-MM-DD` cookie. Already set? Return current stats without writing. Not set? Write rating, set cookie (1-year expiry). Simple, not unbreakable — but appropriate for a sunset rating site.

---

### Beat 4 — Infrastructure as Code: Terraform (7 min)

**Problem:** You set up your Cloudflare account by clicking through dashboards. Six months later, you accidentally delete a cache rule. How do you recreate it exactly? Or you want to teach a student to build the same thing from scratch.

**IaC solution:** describe your infrastructure in code files. Run `terraform apply` and Cloudflare creates/updates everything to match.

The actual cache rule for "Ignore Query String" from `rules.tf`:

```hcl
rules {
  description = "Ignore query string (DoS mitigation)"
  expression  = "starts_with(http.request.uri.path, \"/timelapse/\")"
  action      = "set_cache_settings"

  action_parameters {
    cache_key {
      custom_key {
        query_string { exclude { all = true } }
      }
    }
  }
}
```

- **Declarative vs. imperative:** you describe WHAT you want ("a cache rule that ignores query strings on /timelapse/"), not HOW to create it (no "click Caching, then Cache Rules, then..."). Terraform calls Cloudflare's API to make it happen.
- **Idempotency:** run `terraform apply` 100 times, same result. It compares current state to desired state and only changes what differs.
- **State file:** Terraform remembers what it created in `terraform.tfstate`. This file is git-ignored (it contains secrets) but must be kept safe.
- **Why this matters for AP CSP:** version control for infrastructure. `git log` shows when each rule was added and why. If the Pi were replaced, `terraform apply` rebuilds the Cloudflare side in minutes.

---

### Discussion (3 min)

1. The Worker runs your JavaScript on Cloudflare's servers. Cloudflare can read your code and the data flowing through it. What are the privacy implications?
2. The state file is git-ignored. If it's lost or corrupted, what happens? Is there a recovery path?
3. Compare: writing infrastructure in code vs. clicking through a dashboard. Which is easier to learn? Which is easier to reproduce a year later?

---

## Session 7 (30 min): "What Could Go Wrong?"

**AP CSP Big Idea:** IOC + CSN security · **Learning Objectives:** IOC-2.A, IOC-2.B

### Opening: The Leak (3 min)

Show an example of an exposed API key in a public GitHub repo (real or constructed):
```
# Found in commit history:
CLOUDFLARE_API_TOKEN=v1.0-abc123def456...
```
"This happens thousands of times a day. Within minutes, attackers scrape new commits and use the key."

### Threat Model Walkthrough (12 min)

Draw the full system. For each component:

- **Pi / SSH:** Password "password123" → brute force. Fix: key authentication, fail2ban.
- **Secrets in code:** Hardcoding API tokens → commits leak them. Fix: environment variables,
  `.env` files, `.gitignore`. But: committing once means it's in history forever — `git filter-repo`
  needed for full removal.
- **Port forwarding vs. Tunnel:** Open port = attackers can knock. Tunnel = no port, no knocking.
  But: Cloudflare now sees everything. Different trust, not zero trust.
- **Cookie deduplication for ratings:** Prevents trivial spam. Doesn't prevent determined attackers.
  Discuss: what threat model justifies a cookie check for a sunset rating site?

### Activity: Threat Model Exercise (10 min)

Groups of 3–4. Each group gets a scenario:
- **A:** School grade-checking website. List 5 threats + 1 mitigation each.
- **B:** Open-sourcing home automation code. What belongs in the repo? What doesn't?
- **C:** Friend says "I don't need HTTPS, it's just a text site." Convince them or explain why they might be right.
- **D:** A Cloudflare Worker with an infinite loop bug. Who pays? What happens?

### AP Exam Focus (5 min)

Key terms: symmetric vs. asymmetric encryption, certificate authority, PII, phishing vs. malware vs.
DDoS. Work through one multiple-choice question on data protection.

### Beat: One Specific Attack, One Specific Defense (3 min)

Return to the cache-busting DoS vector from Session 5. The cache rule in `rules.tf` protects against one specific attack: an attacker appending random query strings to bypass Cloudflare's cache and force every request to hit the Pi directly.

Ask students: this rule was added after the engineer thought about what could go wrong. Security is never done — it's a series of specific threat identifications and specific responses. What other query-string attacks might still work? What would you add next?

"That's the lesson: security is not a feature you add at the end. It's a series of decisions you make every time you design a system."

---

## Session 8 (30 min): "Should You Build This?"

**AP CSP Big Idea:** IOC · **Learning Objectives:** IOC-1.A, IOC-1.B, IOC-2.A, IOC-2.B, IOC-2.C

### Discussion Arc 1: Access and Power (8 min)

"Cloudflare's free tier lets an individual developer serve content globally at the same speed as Netflix."

- Who could actually build this project? (Prerequisites: programming, English docs, credit card, hardware)
- Cloudflare dropped service to a website under public pressure in 2019. Should a private company
  have that power over internet access?
- The Pi films continuous video of the neighborhood. Did the neighbors consent?

### Discussion Arc 2: Every Decision Has Values (8 min)

| Decision | Convenience Gained | What Was Given Up |
|---|---|---|
| Cloudflare CDN | Global speed, DDoS protection | Cloudflare sees all traffic |
| Cloudflare Workers | No server to manage | Code runs on third-party infrastructure |
| NWS/Open-Meteo APIs | Rich data, free | Dependency on external services |
| Home Pi vs. cloud VM | Low cost, physical control | Home bandwidth limits, IP exposure |
| Cookies for deduplication | Simple spam prevention | Implicit user tracking |
| Zero Trust Tunnel | No open ports | Must trust Cloudflare instead |
| Terraform IaC | Infrastructure reproducible, documented, version-controlled | State file contains secrets; must be managed carefully |

"There is no purely good option. Every architectural decision is also an ethical decision."

### Activity: AP-Style Written Response (12 min)

> *"The timelapse system uses a Cloudflare Tunnel to route all web traffic through Cloudflare's servers.
> Identify one beneficial effect and one potentially harmful effect of this design decision, and explain
> whether the beneficial effect justifies the harmful effect."*

**Scoring criteria:**
- Must identify a *specific* benefit (not vague: "it's faster")
- Must identify a *specific* harm (not vague: "privacy issues")
- Must make a reasoned judgment connecting the two
- Responses that only list facts without taking a position score lower

---

## AP CSP Alignment Map

| Session | Topic | Big Ideas | Key Learning Objectives |
|---------|-------|-----------|------------------------|
| 5 | CDN, caching, DoS | CSN, IOC | CSN-1.A, CSN-1.E, IOC-2.A |
| 6 | Edge compute, Workers, IaC | CSN, DAT, IOC | CSN-1.A, DAT-2.C, IOC-1.A |
| 7 | Security and threat modeling | IOC, CSN | IOC-2.A, IOC-2.B |
| 8 | Impact and tradeoffs | IOC | IOC-1.A, IOC-1.B, IOC-2.C |

---

## Appendix A: Vocabulary Glossary

Terms introduced in Part 2. The Session 5 security terms from Part 1 (symmetric encryption, SSH, .gitignore, etc.) also apply to Session 7 and are reproduced in the Part 1 glossary.

| Term | Definition |
|------|------------|
| **DoS** | Denial of Service. An attack that overwhelms a server with requests, making it unavailable. A single Pi on a residential connection is easily DoS'd; a CDN like Cloudflare absorbs the traffic before it reaches the Pi. |
| **DDoS** | Distributed Denial of Service. An attack that floods a server with traffic from many sources simultaneously, making it unavailable to legitimate users. "Distributed" means the attack originates from many machines (often a botnet), making it harder to block by IP. CDNs like Cloudflare absorb DDoS traffic before it reaches the origin. |
| **Cache Busting** | Bypassing a cache by making each request appear unique, e.g., by appending random query parameters. `/video.mp4?x=1` and `/video.mp4?x=2` are treated as different URLs by the cache. Mitigated by an "Ignore Query String" rule. |
| **Anycast** | A network routing method where the same IP address is announced from multiple geographic locations simultaneously. Your request is automatically routed to the nearest one. Cloudflare uses Anycast so every user connects to the closest edge node. |
| **Edge Compute** | Running code at CDN edge nodes (close to users) rather than at a central server. Reduces latency. Cloudflare Workers are JavaScript functions deployed to 200+ edge locations. |
| **Cloudflare Worker** | A JavaScript function deployed to Cloudflare's 200+ edge locations. Intercepts HTTP requests, can respond directly or pass through to the origin. Uses V8 isolates — no cold start, ~0ms latency overhead. |
| **V8 Isolate** | A lightweight JavaScript execution context (the same engine used in Chrome). Workers use isolates instead of containers, enabling near-zero cold-start times. |
| **Serverless** | A cloud model where you deploy code without managing servers. The provider runs your function on-demand and charges per invocation. "Serverless" doesn't mean no servers — it means you don't manage them. |
| **Cloudflare KV** | Cloudflare's distributed key-value store. Data written at one edge node is eventually replicated globally. Used in this project to store per-day sunset ratings. |
| **Eventual Consistency** | A distributed system property where all copies of data will *eventually* agree, but may temporarily differ. Cloudflare KV is eventually consistent — a rating written in Tokyo may not immediately appear in London. |
| **Zero Trust** | A security model where no network location is inherently trusted. Every connection must authenticate and be explicitly authorized, regardless of whether it originates inside or outside the network. |
| **Perimeter Security** | The traditional "castle and moat" model: trust everything inside the firewall, block everything outside. Fails when an attacker gets inside or when users work remotely. |
| **Cloudflare Tunnel** | A daemon (`cloudflared`) running on the Pi that makes an outbound connection to Cloudflare. Cloudflare forwards requests through it. No inbound ports need to be open. |
| **Infrastructure as Code (IaC)** | Describing infrastructure (servers, DNS records, firewall rules, cache rules) in version-controlled code files rather than through manual UI clicks. Enables reproducibility, auditability, and disaster recovery. Terraform is a popular IaC tool. |
| **Terraform** | An open-source IaC tool that reads `.tf` files describing desired infrastructure and calls cloud provider APIs to create or update resources to match. Declarative: you describe WHAT, Terraform figures out HOW. |
| **Declarative Programming** | Specifying the desired end state without prescribing the exact steps to get there. SQL ("give me all users over 18") is declarative. Terraform is declarative. Contrast with imperative: explicit step-by-step instructions. |
| **State File** | A file Terraform maintains (`terraform.tfstate`) recording what infrastructure it has created and its current configuration. Required for Terraform to detect drift and know what to change. Contains sensitive data — never committed to git. |
| **Idempotent** | An operation that produces the same result no matter how many times it is applied. `terraform apply` is idempotent: run it 10 times, the infrastructure stays the same. Important for automation and safety. |
| **Cookie** | A small piece of data stored by a browser and sent with every request to the same domain. Used for session management, authentication, and in this project, deduplication (detecting if a user already rated a sunset). |

---

## Appendix B: Turning This Document Into a Presentation

This markdown file is structured to be directly usable as input for AI-assisted slide generation.
The hierarchy maps naturally: `##` headings → sections or divider slides, `###` headings → individual
slides, bullet lists → slide content, tables → slide tables or comparison graphics.

### Option 1: Gamma.app (Recommended — Fastest)

[Gamma](https://gamma.app) takes text or markdown and generates a designed slide deck in seconds.

1. Go to **gamma.app** → **Create** → **Generate from text**
2. Paste the full contents of this file (or a selected session)
3. Gamma will propose a slide structure — you can edit before generating
4. Choose a theme, review generated slides, and export to PDF or PowerPoint

**Tips for best results:**
- Paste one session at a time for a focused deck (e.g., Session 5 only for the CDN lesson)
- Paste the entire file for a complete Part 2 unit overview deck
- Tell Gamma: *"This is a lesson plan for a high school CS class. Make slides suitable for teacher
  use: one concept per slide, discussion questions as bullet points, vocabulary as a table."*
- The vocabulary appendix will generate clean comparison/definition slides automatically

**Gamma is free** for a generous number of AI generations per month. Export to PPTX is available
on the free tier.

---

### Option 2: ChatGPT with Code Interpreter (Most Customizable)

ChatGPT (GPT-4o with the data analysis tool enabled) can write and execute Python to generate a
real `.pptx` file using the `python-pptx` library.

**Prompt to use:**

> "I'm going to paste a markdown lesson plan. Please generate a Python script using python-pptx
> that creates a PowerPoint presentation from this content. Use one slide per `###` heading.
> Bullet points become slide body text. Tables become two-column text slides. Include a title slide
> and a divider slide for each `##` section. Style: dark background (#1a1a2e), white title text,
> light grey body text, accent color #4CAF50.
>
> [paste this file]"

ChatGPT will generate and execute the Python, then offer a `.pptx` download. You can iterate:
*"Make the font larger"*, *"Add slide numbers"*, *"Use a different color for vocabulary slides."*

---

### Option 3: Claude (This Tool) Generating python-pptx Code

Ask Claude to write `python-pptx` code, run it locally:

```bash
pip install python-pptx
python3 generate_slides.py
```

This gives the most control — you can edit the Python to match your exact school's slide template
or branding. Good choice if you have a required PowerPoint theme from your district.

---

### Option 4: Google Slides via SlidesAI

[SlidesAI.io](https://slidesai.io) is a Google Slides add-on that generates slides from pasted text.

1. Install SlidesAI from the Google Workspace Marketplace
2. Open a new Google Slides deck
3. Extensions → SlidesAI → Generate slides → paste text
4. Free tier allows a limited number of generations per month

Less powerful than Gamma but stays within Google Workspace if your school requires it.

---

### Suggested Slide Decks to Generate

Rather than one giant deck, consider generating these separately:

| Deck | Content to Paste | Audience | Length |
|------|-----------------|----------|--------|
| **Session 5 (CDN + DoS) deck** | Session 5 section only | Students (CDN day) | ~12 slides |
| **Session 6 (Edge + IaC) deck** | Session 6 section only | Students (Workers/Terraform day) | ~15 slides |
| **Full Part 2 deck** | Entire Part 2 document | Teacher planning / admin | ~30 slides |
| **Security deep-dive deck** | Session 7 + Zero Trust sidebar | Students (session 7) | ~18 slides |

---

### Prompt Engineering Tips for Best Slides

When prompting any AI to generate slides from this document:

- **Specify the audience:** "high school students, AP CS Principles"
- **Specify the use:** "teacher-led discussion slides, not student self-study"
- **Name the discussion questions explicitly:** "Put each discussion question on its own slide
  with the question large and space below for student responses"
- **Handle tables:** "Convert comparison tables into side-by-side text boxes, not embedded table objects"
- **Vocabulary slides:** "One term per slide with definition and a one-sentence example"
- **Activity slides:** "Activity instructions in a numbered list; put timing and group size prominently"
- **Code blocks:** "Display code in a monospace font on a dark background; annotate each line with a comment explaining what it does"

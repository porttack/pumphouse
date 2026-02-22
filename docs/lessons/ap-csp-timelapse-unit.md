# AP CSP Lesson Unit: From a Single JPEG to a Global CDN
### A Real-World Project-Based Unit on Computer Science Principles

**Course:** AP Computer Science Principles (CS50-based)
**Total Time:** 6 sessions × 30 minutes
**Central Project:** A Raspberry Pi sunset timelapse system that grew from one image into a
full CDN-backed, edge-computed, Zero Trust video platform.
**Driving Question:** *How does a single photo become a globally distributed, edge-computed,
cached media platform — and what CS concepts explain every step?*

**AP CSP Big Ideas Covered:** CRD · DAT · AAP · CSN · IOC

---

## Unit Overview

The project was built incrementally — each new feature was driven by a problem. That problem→solution arc is the spine of every session. Students follow one engineer's real decisions, not a textbook abstraction.

**Project arc (chronological):**

1. Camera serving a JPEG over HTTPS on the Pi (valid TLS cert, router port forwarding)
2. Discovering and consuming an RTSP stream from the IP camera
3. Capturing frames from RTSP with OpenCV/ffmpeg
4. Assembling frames into MP4 files (H.264, CRF quality, FPS parameters)
5. Flask web server on the Pi serving the MP4s and a viewer page
6. Fetching weather data from NWS and Open-Meteo REST APIs; caching JSON results
7. HTML/CSS/JavaScript viewer — speed controls, keyboard shortcuts, touch swipe navigation
8. Domain purchase, DNS, Cloudflare as CDN
9. **Cloudflare Zero Trust Tunnel** (outbound-only, no open ports, identity-verified)
10. Cloudflare Workers (edge compute intercepting ratings API calls)
11. Cloudflare KV store (distributed key-value store for star ratings)
12. Cache-Control headers strategy (immutable past pages, short TTL for today)
13. Client-side vs. server-side rendering tradeoffs (driven by cacheability requirements)
14. Security: rotating API tokens, `.gitignore` secrets, firewall hardening

---

## Session 1 (Introductory, 30 min): "One Photo, One Problem at a Time"

**Learning Objectives:**
- Explain how HTTP/HTTPS serves content over a network
- Describe why TLS certificates matter for trust
- Identify at least three AP CSP big ideas embedded in a real project
- Articulate the "problem → solution → new problem" pattern that drives engineering

**AP CSP Alignment:** CSN-1.A, CSN-1.B, CSN-1.D · IOC-1.A · DAT-1.A

---

### Hook (3 min)

Project a single sunset JPEG. Ask:

> "This photo was taken automatically at 7:42 PM last Tuesday by a $35 computer sitting on
> a windowsill in Oregon. Nobody pressed a button. The camera doesn't belong to that
> computer. The image was encrypted in transit, cached on servers in 12 cities worldwide,
> and anyone on Earth can see it in under 50 milliseconds. How many separate computer
> science concepts do you think are buried in that sentence?"

Take 4–5 student guesses. Write them on the board without evaluating. Tell students:
*"By the end of this unit, every single one of those will have a name, and you'll understand exactly how it works."*

---

### Beat 1 — The Simplest Version (5 min)

**Narrative:** The Pi runs a tiny web server. HTTP is just a text message: *"give me this file."*
But the Pi is behind a home router with one public IP. Solution: **port forwarding** — the router
sends any request arriving on port 443 to the Pi. The Pi has a **TLS certificate** so browsers
don't show a warning and the connection is encrypted.

**Whiteboard:** `[Phone] → [Internet] → [Router: Public IP] →(port forward)→ [Pi: 192.168.1.42]`

**Discussion:**
1. Why do we have private IP addresses inside a home network? What problem do they solve?
2. What is the difference between port 80 and port 443? Why does the port number matter?
3. What does TLS *actually* protect? Who can still see that you visited a site, even with HTTPS?

**AP CSP tie-in:** CSN-1.D (IP addresses, packets); standard exam question: *"Which best explains
why a packet may take a different route to the same destination?"*

---

### Beat 2 — More Data, More Problems (5 min)

**Narrative:** One JPEG is fine; a timelapse needs hundreds of frames. The camera speaks **RTSP**
(Real Time Streaming Protocol). Python + OpenCV connects to the stream and saves every Nth frame.
`ffmpeg` encodes the frames into **H.264 MP4** — storing not every pixel but *differences between
frames*. A 200-frame JPEG sequence ≈ 40 MB; the H.264 MP4 ≈ 800 KB.

```
ffmpeg -framerate 24 -pattern_type glob -i '*.jpg' -c:v libx264 -crf 23 output.mp4
```

**CRF** (Constant Rate Factor) trades file size for quality. CRF 18 ≈ excellent. CRF 51 ≈ unwatchable.
This is a **compression parameter** — a real algorithm design decision.

**Discussion:**
1. H.264 stores differences between frames. What kinds of videos would compress *poorly*?
2. CRF 23 = 1 MB; CRF 18 = 4 MB. Most users are on mobile. Which do you choose?
3. The codec is an abstraction. What are the tradeoffs of using an abstraction you don't fully understand?

**AP CSP tie-in:** DAT-1.B (compression, lossy vs. lossless)

---

### Beat 3 — Serving It + Adding Data (4 min)

**Narrative:** Flask serves the MP4s and a webpage. But what about weather? Two **REST APIs**
(NWS, Open-Meteo) return **JSON** — structured text, human-readable, parseable by any language.
Calling the API on every page load is slow and risks hitting rate limits, so results are **cached**
to a local JSON file. Before calling again, check: is the file less than 60 minutes old?

```json
{ "temperature": 68.4, "windspeed": 12.1, "weathercode": 3, "sunset": "2024-07-15T20:42:00" }
```

**Discussion:**
1. What is JSON? Why is a standard text format more useful than a binary format?
2. If you cache weather for 1 hour, what's the worst that could happen?
3. Who made these APIs? Why would a government agency give away weather data for free?

**AP CSP tie-in:** CSN-2.C (APIs, HTTP requests/responses); DAT-2.C (key-value pairs)

---

### Beat 4 — Scale Problem and the CDN (4 min)

**Narrative:** Share the link publicly and traffic spikes. A $35 Pi on a home connection won't survive.
Enter **Cloudflare CDN**: traffic hits Cloudflare's edge servers near each user. London user? London
server serves the video. Tokyo? Tokyo server. The Pi is only contacted on a cache miss.

**Whiteboard:**
```
User in London → Cloudflare London Edge → (cache hit: done)
                                        → (miss) → Cloudflare Tunnel → Pi at home
```

**Discussion:**
1. What is a CDN solving, technically? (Hint: think about physics — speed of light and distance)
2. When the CDN serves a cached copy, the Pi gets zero traffic. Who pays for Cloudflare's servers?
3. If Cloudflare goes down, what happens to the site? Is that an acceptable risk?

**AP CSP tie-in:** CSN-1.A (routing and latency); CSN-1.E (redundancy and fault tolerance)

---

### Beat 5 — Zero Trust and Edge Compute (4 min)

**Narrative:** The switch from port forwarding to a **Cloudflare Tunnel** is more than a convenience —
it's a **Zero Trust** architecture. Instead of opening a port and trusting that only legitimate traffic
arrives, the Pi makes an *outbound* connection to Cloudflare. No port is open. No inbound connection
is ever accepted. Every request is verified at Cloudflare's edge before it reaches the Pi. *(See the
Zero Trust explainer section below.)*

Ratings are stored in **Cloudflare KV** — a distributed key-value store. A **Cloudflare Worker**
(30 lines of JavaScript running in 200+ cities simultaneously) intercepts rating submissions, reads
the current average from KV, updates it, and writes back. The Pi is never involved.

**Discussion:**
1. Why is "never trust, always verify" safer than "trust everything inside the network"?
2. The KV store is "eventually consistent" — two simultaneous ratings from different continents
   might briefly be out of sync. Is that acceptable here? When would it *not* be?
3. Is it strange that the rating data lives on Cloudflare's servers, not the Pi?

**AP CSP tie-in:** DAT-2.C (key-value stores); IOC-2.B (privacy and data ownership)

---

### Closing (4 min)

> "Every step was driven by a problem. HTTP because we needed to share a file. Compression because
> 40 MB was too big. APIs because we wanted real data. CDN because one Pi couldn't scale. Zero Trust
> because open ports are attack surfaces. That's how real engineering works — not design-everything-up-front,
> but solve-the-next-problem."

**Exit Ticket:** Name one concept from today and explain in your own words what problem it solved.

---

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

## Session 2 (30 min): "How Does a Packet Find Your Pi?"

**AP CSP Big Idea:** CSN · **Learning Objectives:** CSN-1.A, CSN-1.B, CSN-1.C, CSN-1.D, CSN-1.E

### Direct Instruction: The Journey of a Request (10 min)

Trace a browser request step by step on the whiteboard:

1. **DNS Resolution** — Browser asks: "What IP address is `sunsets.example.com`?" A distributed
   directory of name→IP mappings answers. Cloudflare manages the entry.
2. **TCP Connection + TLS Handshake** — TCP guarantees ordered, reliable delivery. TLS exchanges
   certificates and negotiates an encryption key. Neither party can be impersonated.
3. **HTTP Request** — Inside the encrypted tunnel: `GET /video/2024-07-15.mp4 HTTP/2`
4. **CDN Cache Check** — Cloudflare: "Do I have this at this edge location?" Hit → serve immediately.
   Miss → fetch from Pi via tunnel.
5. **Response** — MP4 arrives in chunks. HTTP range requests let playback begin before download ends.

### Activity: Packet Routing Simulation (15 min)

Assign student roles (index cards): 2 browser clients (London, Tokyo), 3 Cloudflare edge nodes,
2 tunnel relay students, 1 Pi, 1 DNS server, remaining = internet routers.

- **Round 1:** London requests the video. Cold cache. Trace the path physically — card passes
  from client → edge → tunnel → Pi → back; edge caches it.
- **Round 2:** Tokyo requests same video. Also cold. Pi involved again.
- **Round 3:** Both clients request simultaneously. Both served from local cache. Pi untouched.

**Debrief:** What if the Pi went offline after Round 2? What if one router student sits down mid-transfer?

### AP Exam Question (5 min)

Work through a sample question about routing, redundancy, and why packets may take different paths.

---

## Session 3 (30 min): "What Is Data, Actually?"

**AP CSP Big Idea:** DAT · **Learning Objectives:** DAT-1.A, DAT-1.B, DAT-2.B, DAT-2.C

### Direct Instruction: APIs and JSON (10 min)

- **REST API:** A defined URL format for requesting data. `GET /v1/forecast?lat=37.7&lon=-122.4`
- **JSON:** Text format. Curly braces = objects. Square brackets = arrays. Key-value pairs.
- **Key-Value Store vs. SQL table:** Same data, different models. KV = fast lookup by key.
  SQL = flexible queries. When do you choose each?

### Activity: Live API Exploration (12 min)

**With computers:** Students open `api.open-meteo.com` directly in the browser. Annotate the raw
JSON: circle keys, underline values, box nested objects.

**Without computers:** Print a sample response. Same annotation exercise in pairs.

### Caching Discussion (8 min)

- Why not call the API on every page load?
- What is the tradeoff between cache duration and data freshness?
- Where else do you encounter caching? (Browser, CDN, your phone's app data)

---

## Session 4 (30 min): "How Does Compression Actually Work?"

**AP CSP Big Ideas:** AAP · DAT · **Learning Objectives:** AAP-2.A, AAP-2.B, DAT-1.B, DAT-1.C

### Direct Instruction: Frame Selection Algorithm (8 min)

Uniform sampling: save every Nth frame. O(total_frames). Simple, but ignores rate of change.

Adaptive sampling: compare adjacent frames; sample more when the scene changes rapidly. Better output,
more complex, more potential for bugs. Classic tradeoff: simplicity vs. quality.

### Direct Instruction: H.264 Conceptually (10 min)

- **Spatial compression:** Find redundant patterns within one frame (similar to JPEG).
- **Temporal compression:** Store one full frame (I-frame), then only *what changed* between frames.
  A static camera filming a slow sunset: only sky color changes. Trees, buildings, horizon → same every frame.
  H.264 stores motion vectors and delta values, not raw pixels.
- **CRF parameter:** Controls aggressiveness. 0 = lossless. 51 = terrible. 23 = default.

### Activity: CRF Tradeoff Analysis (10 min)

| CRF | File Size | Quality Score | Perceived Quality |
|-----|-----------|---------------|-------------------|
| 18  | 8.2 MB    | 46.2 dB       | Excellent         |
| 23  | 3.1 MB    | 41.8 dB       | Good              |
| 28  | 1.4 MB    | 37.3 dB       | Acceptable        |
| 35  | 0.6 MB    | 30.1 dB       | Noticeably degraded |
| 45  | 0.2 MB    | 20.4 dB       | Poor              |

Students answer in pairs:
1. Where is the "knee" in this curve — the point of diminishing returns?
2. Your users are on mobile 4G with 10 GB/month data caps. Which CRF?
3. PSNR is a mathematical quality score. Why might a video that scores well mathematically still
   look bad to a human?
4. Choosing a parameter vs. writing the algorithm: what is the difference in terms of responsibility?

---

## Session 5 (30 min): "What Could Go Wrong?"

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

---

## Session 6 (30 min): "Should You Build This?"

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
| 1 | Full system narrative | CSN, DAT, AAP, IOC | CSN-1.D, DAT-1.A, IOC-1.A |
| 2 | Internet and networking | CSN | CSN-1.A–1.E |
| 3 | Data, APIs, caching | DAT | DAT-1.A, DAT-2.B, DAT-2.C |
| 4 | Algorithms and compression | AAP, DAT | AAP-2.A, AAP-2.B, DAT-1.B |
| 5 | Security | IOC, CSN | IOC-2.A, IOC-2.B |
| 6 | Impact and tradeoffs | IOC | IOC-1.A, IOC-1.B, IOC-2.C |

---

## Differentiation Notes

**Students who are ahead:** Find the CRF of a video they own (file size ÷ duration ÷ resolution).
Read Cloudflare Workers documentation and explain "Service Worker" in plain English.

**Students who are struggling:** The packet routing simulation (Session 2) is kinesthetic — works
well for students who disengage from lecture. Emphasize the CRF table over the DCT math in Session 4.

**English Language Learners:** The problem→solution arc is a universal story structure. Emphasize
the story, not the jargon. Provide a vocabulary list: protocol, abstraction, cache, codec, API,
latency, key-value, certificate, tunnel, edge, Zero Trust.

---

## Common Student Misconceptions to Preempt

1. **"HTTPS means the website is safe."** — HTTPS means the *connection* is encrypted. Phishing sites use HTTPS too.
2. **"The CDN is a backup in case my server fails."** — It's primarily a performance layer. It may
   not store data durably.
3. **"Deleting a file from GitHub removes it."** — Git history is permanent unless rewritten with
   tools like `git filter-repo`.
4. **"Compression always makes things smaller."** — Compressing an already-compressed file (MP4 in
   a ZIP) often makes it *larger*.
5. **"A private IP means I'm anonymous."** — Private IP is only within your network. Your public IP
   is visible to every server you connect to.
6. **"Zero Trust means you trust nobody ever."** — It means no *implicit* trust based on network
   location. You still trust after explicit authentication and authorization.

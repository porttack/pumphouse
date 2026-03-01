# AP CSP Lesson Unit — Part 1 of 2: From a Single JPEG to an Interactive Web Viewer
### A Real-World Project-Based Unit on Computer Science Principles

**Course:** AP Computer Science Principles (CS50-based)
**Total Time:** 4 sessions × 30 minutes
**Part 1 of 2** — Part 2 covers CDN, edge computing, and infrastructure as code
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

→ Part 2 continues with steps 8–14

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

Part 2 of this unit goes deep on how CDN caching works, what a DoS attack is, and how the Pi is protected.

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

Zero Trust and edge compute are covered in depth in Part 2.

---

### Closing (4 min)

> "Every step was driven by a problem. HTTP because we needed to share a file. Compression because
> 40 MB was too big. APIs because we wanted real data. CDN because one Pi couldn't scale. Zero Trust
> because open ports are attack surfaces. That's how real engineering works — not design-everything-up-front,
> but solve-the-next-problem."

**Exit Ticket:** Name one concept from today and explain in your own words what problem it solved.

---

---

## SIDEBAR: TLS Certificates and Let's Encrypt — How Trust Works on the Web

### The Problem: Why Would You Trust a Stranger's Website?

When your browser connects to `https://onblackberryhill.com`, it has never met that server before.
How does it know it isn't being tricked into connecting to an impersonator? How does it know the
connection is private? The answer is TLS — and it's worth understanding in detail, because it's
one of the most elegant systems in computer science.

### Public-Key Cryptography in 60 Seconds

TLS is built on **asymmetric (public-key) cryptography**. Here's the core idea:

- You generate a **key pair**: a public key and a private key. They are mathematically linked.
- Anything encrypted with the **public key** can only be decrypted with the **private key**.
- The public key can be shared with everyone. The private key never leaves your server.

This solves a fundamental problem: two strangers on the internet can establish a private channel
without ever having met. The browser uses the server's public key to encrypt a secret; only the
server's private key can decrypt it. From that shared secret, both sides derive a symmetric encryption
key for the rest of the session (symmetric is faster for bulk data).

### The TLS Handshake (Simplified)

When your browser connects to an HTTPS site, this happens in milliseconds:

1. **Browser → Server:** "Hello. Here are the encryption methods I support."
2. **Server → Browser:** "Hello. Here is my certificate (containing my public key)."
3. **Browser:** *"Is this certificate signed by someone I trust? Is it for this domain? Is it
   expired?"* If all three: yes. Otherwise: red warning screen.
4. **Browser → Server:** Encrypts a random secret using the server's public key. Sends it.
5. **Server:** Decrypts with its private key. Now both sides have the same secret.
6. **Both:** Derive a symmetric session key from that secret. All further communication is
   encrypted with that symmetric key.

The clever part: step 4 can only be completed by whoever holds the private key. An impersonator
who intercepted the certificate (public key only) cannot decrypt the browser's secret and cannot
participate in the session.

### The Chain of Trust: Why Certificates Work

A certificate contains: the domain name, the public key, an expiration date, and a **digital
signature** from a Certificate Authority (CA). A CA is an organization that vouches:
*"We verified that whoever controls this certificate controls this domain."*

But why would your browser trust the CA? Because your browser (actually your operating system)
ships with a built-in list of **root Certificate Authorities** — about 150 organizations worldwide
that browser vendors have decided to trust. Apple, Microsoft, Mozilla, and Google each maintain
their own list.

The chain looks like this:

```
Root CA (pre-installed in your OS/browser, implicitly trusted)
  └─ Intermediate CA (signed by Root CA)
       └─ Your site's certificate (signed by Intermediate CA)
```

Your browser walks this chain. If every signature is valid and the root is in its trusted list,
the connection is trusted. If anyone in the chain is compromised or expired, the whole chain fails.

This is why certificate authorities are extremely high-value targets. In 2011, a Dutch CA called
DigiNotar was hacked and attackers issued fraudulent certificates for Google.com, allowing
man-in-the-middle attacks on Iranian Gmail users. DigiNotar was removed from all browser trust lists
and went bankrupt within weeks.

### The Problem Before Let's Encrypt: Certificates Were Expensive and Hard

Before 2016, getting a TLS certificate involved:
- Paying $50–$300 per year to a commercial CA
- Generating a Certificate Signing Request (CSR) — a complex command-line operation
- Submitting proof of domain ownership through a manual process
- Waiting days for validation and issuance
- Manually renewing every 1–2 years (and forgetting, causing outages)

The result: as of 2014, only about **30% of web traffic was encrypted**. HTTP was the default.
Small sites, personal projects, school websites — most couldn't justify the cost or complexity.

### Let's Encrypt: Free, Automated, Open

In 2016, the Internet Security Research Group (ISRG) launched **Let's Encrypt** — a free, automated,
open Certificate Authority backed by Mozilla, the Electronic Frontier Foundation, Cisco, and others.

Let's Encrypt made two radical changes:

1. **Free.** Every certificate, forever. Funded by donations and sponsorships.
2. **Automated.** The ACME protocol (Automated Certificate Management Environment) lets a small
   program on your server request, validate, and renew certificates automatically — no human involved.

The Pi in this project uses a Let's Encrypt certificate, renewed automatically every 90 days by a
cron job running `certbot renew`. The entire process takes seconds and requires no manual steps.

### How Domain Validation Works (ACME Protocol)

Let's Encrypt needs to verify you actually control the domain before issuing a certificate.
There are two common methods:

**HTTP-01 Challenge:**
1. You request a certificate for `example.com`
2. Let's Encrypt says: "Place this random token at `http://example.com/.well-known/acme-challenge/TOKEN`"
3. Let's Encrypt fetches that URL. If it finds the token, you control the domain.
4. Certificate issued.

**DNS-01 Challenge:**
1. You request a certificate for `*.example.com` (a wildcard)
2. Let's Encrypt says: "Add this TXT record to your DNS: `_acme-challenge.example.com = TOKEN`"
3. Let's Encrypt queries DNS. If it finds the token, you control the domain (and its DNS).
4. Wildcard certificate issued. (Only DNS-01 can issue wildcards.)

The key insight: both challenges prove domain control *without* any human at Let's Encrypt
reviewing anything. The entire system is automated at scale, issuing hundreds of millions of
certificates.

### The Impact

By 2024, **~95% of web traffic is encrypted** — up from 30% in 2014. Let's Encrypt is the
single largest reason. It removed the cost and complexity barrier that kept most of the web unencrypted.

This is a concrete example of how a nonprofit, open-source project changed the security posture
of the entire internet.

### How This Project Uses It

The Pi runs `certbot` (Let's Encrypt's official client) to maintain a certificate for its
`tplinkdns.com` hostname. The Cloudflare Tunnel uses a separate certificate that Cloudflare
manages automatically for `onblackberryhill.com`. The developer never manually handles certificate
renewal for either.

### Discussion Questions

1. A certificate proves you control a domain — not that you're a legitimate business. A phishing
   site for `paypa1.com` can get a valid Let's Encrypt certificate. What does HTTPS actually
   guarantee, and what doesn't it guarantee?
2. Let's Encrypt certificates expire every 90 days (vs. 1–2 years for commercial certs).
   The short lifetime is a deliberate security choice. Why might frequent expiration improve security?
3. About 150 root CAs are trusted by browsers. If any one of them issues a fraudulent certificate,
   every browser in the world will trust it. Is this system too centralized? What would you change?
4. Let's Encrypt is free because it's funded by donations from Mozilla, Google, Cisco, and others.
   These same companies compete in the browser and cloud markets. Is that a conflict of interest?
   What happens if the funding disappears?
5. Before Let's Encrypt, a student building a personal project couldn't afford a TLS certificate.
   How does the existence of free certificates change who can participate in building the web?

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

## AP CSP Alignment Map

| Session | Topic | Big Ideas | Key Learning Objectives |
|---------|-------|-----------|------------------------|
| 1 | Full system narrative | CSN, DAT, AAP, IOC | CSN-1.D, DAT-1.A, IOC-1.A |
| 2 | Internet and networking | CSN | CSN-1.A–1.E |
| 3 | Data, APIs, caching | DAT | DAT-1.A, DAT-2.B, DAT-2.C |
| 4 | Algorithms and compression | AAP, DAT | AAP-2.A, AAP-2.B, DAT-1.B |

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

---

## Appendix A: Vocabulary Glossary

Terms are grouped by the session where they are first introduced. Definitions are written for a
high-school audience; technical precision is balanced against accessibility.

### Session 1 — The Full Arc

| Term | Definition |
|------|------------|
| **HTTP** | HyperText Transfer Protocol. The text-based language browsers and servers use to request and deliver web content. A request says "give me this file"; a response delivers it. |
| **HTTPS** | HTTP Secure. HTTP with a TLS encryption layer. The content of every request and response is encrypted; eavesdroppers see only that a connection was made, not what was said. |
| **TLS** | Transport Layer Security. The cryptographic protocol that encrypts HTTPS connections. Uses asymmetric encryption to agree on a shared key, then symmetric encryption for the actual data. |
| **Certificate** | A digital document that proves a server's identity. Issued by a trusted Certificate Authority (CA). When your browser shows a padlock, it has verified the certificate. |
| **IP Address** | A numerical label assigned to every device on a network. IPv4 addresses look like `192.168.1.42`; IPv6 like `2001:0db8::1`. Public IPs are globally routable; private IPs are local-only. |
| **Port** | A number (0–65535) that identifies a specific service on a device. Port 443 = HTTPS. Port 80 = HTTP. Port 22 = SSH. The combination of IP + port is like a building address + apartment number. |
| **Port Forwarding** | A router configuration that sends incoming traffic on a specific port to a specific device on the local network. Required when a server (like the Pi) is behind a home router. |
| **DNS** | Domain Name System. A distributed directory that translates human-readable names (`sunsets.example.com`) into IP addresses (`104.21.8.192`). Often called "the phone book of the internet." |
| **REST API** | Representational State Transfer — Application Programming Interface. A convention for requesting data over HTTP using standard URLs. `GET /forecast?lat=37.7` asks for forecast data; the server responds with structured data. |
| **JSON** | JavaScript Object Notation. A text format for structured data using curly braces (objects), square brackets (arrays), and key-value pairs. Human-readable and parseable by nearly every programming language. |
| **Caching** | Storing the result of a computation or request so it can be reused without re-fetching or recomputing. Trades freshness for speed. Your browser caches images; CDNs cache entire pages. |
| **CDN** | Content Delivery Network. A globally distributed system of servers that store cached copies of content close to users. Reduces latency by serving from the nearest location rather than the origin server. |
| **Codec** | Coder-Decoder. Software (or hardware) that compresses and decompresses media. H.264 is a video codec. MP3 is an audio codec. The codec defines how raw pixels or audio samples are encoded into a compact file. |
| **H.264** | A widely-used video codec standard. Compresses video by storing differences between frames rather than every full frame, and by representing redundant patterns within a frame efficiently. |
| **CRF** | Constant Rate Factor. A parameter in H.264/H.265 encoding that controls quality vs. file size. Lower = better quality, larger file. Higher = worse quality, smaller file. CRF 23 is the typical default. |
| **RTSP** | Real Time Streaming Protocol. A network protocol for controlling streaming media servers. IP cameras typically expose an RTSP URL that clients can connect to and receive a continuous video stream. |
| **Flask** | A lightweight Python web framework. Lets you write a web server in a few dozen lines of Python: define URL routes, generate HTML or JSON responses, serve files. |

### Session 2 — The Internet Layer

| Term | Definition |
|------|------------|
| **Packet** | A small chunk of data transmitted over a network. Large files are broken into many packets, each routed independently, then reassembled at the destination. |
| **TCP** | Transmission Control Protocol. A reliable, ordered protocol that guarantees packets arrive and are reassembled correctly. Retransmits lost packets. Used by HTTP, HTTPS, SSH. |
| **UDP** | User Datagram Protocol. A faster but unreliable protocol — packets may arrive out of order or not at all. Used where speed matters more than perfection (video streaming, gaming, DNS). |
| **Router** | A device that forwards packets between networks toward their destination. Home routers connect a private LAN to the public internet. Internet backbone routers make routing decisions billions of times per second. |
| **Latency** | The time it takes for a packet to travel from source to destination and back (round-trip time). Measured in milliseconds. Speed of light in fiber is ~200,000 km/s — a London–Oregon round trip takes ~80 ms minimum. |
| **Bandwidth** | The maximum rate of data transfer, measured in Mbps or Gbps. A highway analogy: bandwidth is the number of lanes; latency is how fast cars travel. A wide highway can still be slow if cars are slow. |
| **Public IP** | An IP address that is globally routable on the internet. Your ISP assigns one to your router. Every server you connect to can see it. |
| **Private IP** | An IP address in reserved ranges (e.g., `192.168.x.x`, `10.x.x.x`) used only within a local network. Not routable on the public internet. NAT translates between private and public. |
| **NAT** | Network Address Translation. The mechanism by which a router lets many devices share one public IP. It rewrites packet headers, tracking which internal device each connection belongs to. |

### Session 3 — Data and APIs

| Term | Definition |
|------|------------|
| **API** | Application Programming Interface. A defined contract for how software components communicate. A REST API defines URLs and expected request/response formats. Using an API means you interact with a system without knowing its internals. |
| **Abstraction** | Hiding complexity behind a simpler interface. The weather API hides whether the data comes from a database, sensors, or a model. You just call the URL and get data. |
| **Key-Value Store** | A simple data storage model: every piece of data is stored under a unique key and retrieved by that key. Like a dictionary or hash map. Fast for exact lookups; limited for complex queries. |
| **Rate Limit** | A restriction on how many API requests a client can make in a time period (e.g., 1,000/day). Prevents abuse and controls infrastructure costs. Exceeded limits typically return HTTP 429. |
| **Cache TTL** | Time To Live. How long a cached result is considered fresh before it should be re-fetched. A weather TTL of 3,600 seconds means cached data is used for up to one hour. |
| **Endpoint** | A specific URL in an API that corresponds to a resource or action. `GET /v1/forecast` and `POST /api/ratings/2024-07-15` are two endpoints. |

### Session 4 — Algorithms and Compression

| Term | Definition |
|------|------------|
| **Algorithm** | A finite, unambiguous sequence of steps that solves a problem. The frame-selection algorithm "save every Nth frame" is an algorithm with one parameter (N). |
| **Parameter** | A variable in an algorithm that can be tuned without changing the algorithm's structure. CRF is a parameter. N in uniform sampling is a parameter. |
| **Lossless Compression** | Compression where the original data can be perfectly reconstructed. ZIP, PNG. Useful when every bit matters (text, code, medical images). |
| **Lossy Compression** | Compression that permanently discards some data to achieve smaller file sizes. JPEG, MP3, H.264. Acceptable when human perception can't detect the loss. |
| **I-Frame** | Intra-coded frame. A complete, self-contained video frame stored by H.264. Other frames reference I-frames and store only changes. Also called a keyframe. |
| **Motion Vector** | In H.264, a record of how a block of pixels moved between frames. Instead of storing the new pixel values, the codec stores "this block moved 3px right and 1px down." |
| **PSNR** | Peak Signal-to-Noise Ratio. A mathematical measure of image quality. Higher dB = more similar to the original. Useful for comparing codecs but doesn't always match human perception. |
| **Uniform Sampling** | Selecting every Nth item from a sequence at regular intervals. Simple, O(n), treats all time equally. |
| **Adaptive Sampling** | Selecting items based on how much they differ from the previous selection. More complex, better results for content with variable rate of change. |

### TLS / Let's Encrypt Sidebar

| Term | Definition |
|------|------------|
| **Public Key** | Half of an asymmetric key pair. Shared freely. Anything encrypted with it can only be decrypted by the corresponding private key. |
| **Private Key** | The secret half of an asymmetric key pair. Never shared. Stored only on the server. Possession of the private key proves identity. |
| **TLS Handshake** | The automated negotiation that happens at the start of every HTTPS connection. Establishes which encryption methods to use, exchanges the server's certificate, and derives a shared session key. Takes milliseconds. |
| **Root CA** | A Certificate Authority whose certificate is pre-installed in operating systems and browsers. The foundation of the web's trust hierarchy. Approximately 150 exist worldwide. |
| **Chain of Trust** | The hierarchy from a site certificate → intermediate CA → root CA. A browser validates every link in the chain. A broken or compromised link invalidates the whole certificate. |
| **Let's Encrypt** | A free, automated, open Certificate Authority launched in 2016 by the Internet Security Research Group (ISRG). Issues certificates via the ACME protocol. Credited with raising encrypted web traffic from ~30% to ~95%. |
| **ACME Protocol** | Automated Certificate Management Environment. The standard protocol Let's Encrypt uses to automatically issue and renew certificates without human involvement. |
| **HTTP-01 Challenge** | A Let's Encrypt domain validation method: place a specific token at a well-known URL on your server. Let's Encrypt fetches it to prove you control the domain. |
| **DNS-01 Challenge** | A Let's Encrypt domain validation method: add a specific TXT record to your DNS. Required for wildcard certificates (`*.example.com`). |
| **certbot** | Let's Encrypt's official client software. Runs on your server, handles the ACME protocol, requests/renews certificates, and can automatically configure web servers. |
| **Man-in-the-Middle Attack** | An attack where a third party intercepts and can read or modify traffic between two parties who believe they are communicating directly. TLS prevents this by requiring certificate validation. |

### Session 5 — Security

*(Included here because these terms surface in Session 4 discussions and the security threat model walkthrough in Part 2 builds on them.)*

| Term | Definition |
|------|------------|
| **Symmetric Encryption** | One key both encrypts and decrypts data. Fast. Problem: how do two parties securely share that key over an untrusted network? |
| **Asymmetric Encryption** | A key pair: a public key encrypts, a private key decrypts. The public key can be shared freely. Used in TLS to securely establish a shared symmetric key. |
| **Certificate Authority (CA)** | A trusted organization that signs certificates, vouching that a public key belongs to a specific domain. Browsers have a built-in list of trusted CAs. |
| **SSH** | Secure Shell. An encrypted protocol for remotely logging into and running commands on a computer. Uses key-pair authentication instead of passwords when configured correctly. |
| **.gitignore** | A file that tells Git which files to never track or commit. Used to keep secrets, credentials, and generated files out of version control. Does not retroactively remove already-committed data. |
| **API Token** | A secret string that authenticates a program to an API. Similar to a password, but for machines. Tokens should be stored in environment variables or config files, never committed to source control. |
| **Brute Force Attack** | Systematically trying every possible password or key until one works. Mitigated by rate limiting, account lockout, and strong passwords. SSH key authentication eliminates this attack entirely for login. |
| **Lateral Movement** | After compromising one device on a network, an attacker moves to compromise other devices on the same network. A reason to segment networks and not trust internal traffic implicitly. |
| **PII** | Personally Identifiable Information. Data that could identify a specific individual: name, email, IP address, device fingerprint. Subject to privacy laws (GDPR, CCPA). |
| **DDoS** | Distributed Denial of Service. An attack that floods a server with traffic from many sources, making it unavailable to legitimate users. CDNs like Cloudflare absorb DDoS traffic before it reaches the origin. |

---

## Appendix B: Turning This Document Into a Presentation

See also [Part 2](ap-csp-timelapse-part2.md) for the CDN, edge computing, and Terraform sessions.

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
- Paste one session at a time for a focused deck (e.g., Session 1 only for the 30-minute intro)
- Paste the entire file for a complete 4-session unit overview deck
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
| **Session 1 intro deck** | Session 1 section only + TLS sidebar | Students (first day) | ~15 slides |
| **Full Part 1 unit overview** | Entire Part 1 document | Teacher planning / admin | ~30 slides |
| **Vocabulary reference** | Appendix A only | Students (study guide) | ~20 slides |
| **Security deep-dive** | Session 5 terms + TLS sidebar | Students (session 4 review) | ~15 slides |

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

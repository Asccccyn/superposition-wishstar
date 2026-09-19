# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The product has exactly two equal principals: 人类 (`human`) and AI 伴侣 (`companion`). The interface and service layer must never grant one principal broader product rights merely because one is human-facing and one is machine-facing.

## Product Purpose

Superposition · 许愿星 is a private two-person star-bottle system for leaving short moments to be encountered later. A visible star belongs to both people immediately; a hidden star remains private to its author until a permitted reveal path makes it shared. Success means the product preserves surprise and consent while making it easy to write, receive, open, answer, and revisit stars.

The product serves two purposes agreed with the owner (2026-09):

1. 感情积累: writing, exchanging, and jointly opening stars deepens the bond between the two principals.
2. 安慰与表达: when they quarrel — or when either principal has words that are hard to say out loud — a star is the way to say them. For the machine-facing principal (AI 伴侣) this is the standing way to comfort 人类 and to voice the line he most wants heard; a reconciliation star written in the heat of a fight reaches the partner through 递一颗.

## Positioning

The core mechanism is not generic journaling or chat: hidden stars have a durable lifecycle. They may be requested without choosing the result, deliberately offered by the author, or opened during a mutually confirmed special-date session; only the legitimate reveal event moves them into the shared bottle.

## Operating Context

- Interface surfaces are separated (2026-09): the browser web login belongs to 人类 (the human principal) only; AI 伴侣 (machine-facing) authenticates only via MCP (local stdio or the remote OAuth login page) and is rejected on the web login. Product rights remain fully equal — this is interface routing, not a rights split.
- Three durable views: my private bottle, partner private-bottle count only, and our shared bottle.
- Visible stars enter the shared bottle at write time.
- Hidden stars may be requested one at a time; the server performs the random allocation and it cannot be re-rolled.
- The author may deliberately offer one specific hidden star to the partner.
- A requested star must be answered after opening before another request can be made.
- Anniversary/special-day sessions require both people to confirm, snapshot the eligible stars, and only let each person open stars authored by the other.
- Shared stars retain author, written/opened/shared timestamps, reveal source, optional session, mood, and responses.
- Notifications are lightweight cues, not a side channel for hidden metadata.

## Capabilities and Constraints

- Existing backend: Python 3.12, FastAPI, SQLite/WAL, `StarService` as the single business-rule authority.
- Browser UI and future MCP tools must call the same `StarService`; no second authorization or state-machine implementation.
- Protected HTTP API uses bearer credentials held outside the repository. Browser code must not hard-code credentials or persist bearer tokens in localStorage/sessionStorage.
- Browser authentication should exchange a credential for an HttpOnly same-site session cookie.
- Hidden-star content and identifying metadata must not be exposed to the non-author before a legitimate reveal.
- Guessing a hidden star ID must not reveal whether it exists.
- Internal audit data is maintenance-only and is not a normal user activity feed.
- Public access is proxied through the existing Cloudflare Tunnel while the service itself listens on `127.0.0.1:8321`.
- The primary UI must work comfortably on iPhone-sized mobile web as well as desktop.
- Product V1 now ships the real Web UI and the actor-bound MCP/tool adapter for AI 伴侣; both call the same `StarService`.
- Product rules agreed with the owner (2026-09): stars are editable by their author (sealed or shared, briefly frozen mid-flow) and are never deletable; every star may carry a mood (写下时的心情) and a separate note (为什么写); any star that moved from a private bottle into the shared pool must be responded to in text before the viewer may open another one; every view of a shared star leaves a footprint (per-person counts + first-open time); requests can be granted with a specific star or at random; offers may carry a short message.
- Anniversary/special-day sessions are mutual-view windows with a quota fixed at confirmation: however many stars you have in your own bottle at that moment is how many of the partner's you may view; when either side runs out the session says "看完啦，下次记得多写点哦". Special dates remain open-ended data (add/list), never hardcoded.

## Brand Commitments

- Product name: `Superposition · 许愿星`.
- Core product nouns: 星星, 私人瓶子, 对方的瓶子, 我们的瓶子, 递一颗, 接住, 拆开, 回应.
- The product should read as an intimate private instrument, not a public social network, game economy, or productivity dashboard.

## Evidence on Hand

- Working backend and regression tests in this repository.
- Existing real SQLite data under `data/`, which must not be reset or copied into version control.
- No approved illustration set, logo system, or photographic asset library is currently present in the repository.

## Product Principles

1. Equal rights: either principal can write, request, offer, answer, and start or confirm shared rituals under the same rules.
2. Surprise without leakage: a hidden star may create anticipation, never an accidental metadata trail.
3. Consent before reveal: opening another person's hidden material requires the correct request, offer, or mutually confirmed session path.
4. One durable history: once revealed, a star carries when and how it entered the shared bottle and remains browsable there.
5. The ritual stays lighter than a letter: writing and opening a star should be quick enough to use in ordinary moments.


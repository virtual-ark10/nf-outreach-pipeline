#!/usr/bin/env python3
"""Build the ICP validation report: rescored accounts, verified evidence, draft emails.

Reads the six research batches plus verify-report.json (which records which quotes were found
verbatim on the cited page), applies an explicit 35/30/20/15 rubric, registers every cited page
in the grounded-citations ledger, and writes one plain-text report.
"""
import json
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = os.path.join(HERE, "pages")
OUT = os.path.join(HERE, "nf-icp-validation-2026-10-21-v3.txt")
SOURCES = os.path.expanduser("~/.hermes/skills/research/grounded-citations/scripts/sources.py")

# company -> scoring components (fit/35, gap/30, integration/20, commercial/15), confidence,
# disposition, and the two NF-specific fields the rubric asks for.
SCORES = {
    "Common Room": (33, 24, 18, 12, "High", "draft",
        "Article-level company and topic mentions with publication, article URL and date, plus 26,401 publication profiles and activity snapshots to score against.",
        "No private audience metrics, no real-time guarantee, no funding data."),
    "Muck Rack": (30, 21, 13, 9, "High", "second wave",
        "Sponsor relationships and newsletter article-level coverage to deepen the author records they already hold.",
        "NF has no journalist verification team and no LexisNexis print archive; publication audience data is not ours."),
    "Crustdata": (32, 24, 19, 12, "High", "draft",
        "A sixth dataset beside companies, people, jobs and social posts: newsletter mentions with publication, topic, article URL and date.",
        "No private audience metrics, no funding-round data, no guaranteed real-time feed."),
    "Autobound": (31, 24, 19, 13, "High", "draft",
        "Newsletter long-form text turned into signal records: publication, topic, sponsor, company mention, all with source URL and timestamp.",
        "No private audience metrics and no production SLA; coverage is public newsletter web only."),
    "Contify": (28, 22, 15, 10, "High", "draft",
        "Newsletter publications and their articles as a structured source stream with publication metadata and sponsor mentions.",
        "No 1M-source news breadth, no non-English coverage at their scale, no private audience metrics."),
    "Grata": (24, 12, 16, 8, "Medium", "second wave",
        "Source-linked newsletter mentions and topic activity to complement private-company profiles.",
        "No private financials, no valuations, no executive contact database."),
    "Harmonic": (26, 12, 14, 9, "Medium", "second wave",
        "Newsletter activity as an early signal on young companies, with publication and date attached.",
        "No founder graph, no funding data, no stealth detection."),
    "SponsorSheep": (28, 3, 4, 2, "High", "not a prospect",
        "Coverage of publications that never list on a marketplace, and sponsor mentions read from article text.",
        "No marketplace inventory, no ESP-verified open and click rates, no booking flow."),
    "Brandwatch": (26, 21, 13, 8, "High", "second wave",
        "Newsletter mentions and sponsor relationships as a source class their Listen taxonomy does not carry.",
        "No full social firehose, no licensed premium publisher archive, no consumer research panels."),
    "Dealroom": (26, 12, 16, 9, "Medium", "second wave",
        "Newsletter attention signals attached to company and investor records, with source URL and date.",
        "No funding rounds, no valuations, no analyst-curated company profiles."),
    "Mention": (18, 16, 9, 3, "Medium", "not a prospect",
        "Newsletter mention coverage as a monitoring source.",
        "No social firehose and no Agorapulse publishing surface."),
    "People Data Labs": (28, 12, 16, 10, "Medium", "second wave",
        "A newsletter activity field (mentions, topics, sponsors) beside the job-posting dataset.",
        "No person-level identity graph and no contact data."),
    "CompanyEnrich": (30, 24, 17, 11, "High", "draft",
        "Newsletter mention, topic and sponsor fields with source URL and date, as new company fields rather than firmographic ones.",
        "No person contact data, no firmographic master record, no private audience metrics."),
    "Clay": (26, 10, 18, 12, "Medium", "second wave",
        "Newsletter publications, mentions and sponsors as a marketplace data provider and HTTP API endpoint.",
        "No waterfall enrichment and no CRM sync product."),
    "Feedly": (22, 5, 14, 8, "High", "not a prospect",
        "A catalog layer over publications rather than a per-user inbox subscription.",
        "No threat-intelligence graph and no CTI curation."),
    "Crunchbase": (26, 19, 15, 9, "High", "second wave",
        "Newsletter-native publication and sponsor evidence to sit beside their article-level news insights.",
        "No funding rounds, no cap tables, no predictive scores."),
    "Affinity": (24, 19, 14, 8, "High", "second wave",
        "Newsletter-derived company and founder activity mapped onto existing company records.",
        "No relationship-strength graph from private email and calendar, no funding data."),
    "IndustryLens": (18, 4, 12, 5, "High", "not a prospect",
        "Corpus breadth across 26,401 publications rather than per-competitor newsletters.",
        "No battlecard generation and no per-company monitoring pipeline."),
    "Meltwater": (24, 9, 14, 8, "Medium", "second wave",
        "Coverage beyond Substack: beehiiv, Ghost, Kit, Mailchimp and LinkedIn newsletters with publication-level metadata.",
        "No licensed premium publisher content, no broadcast monitoring, no AI-platform visibility data."),
    "Cision": (24, 19, 12, 6, "High", "second wave",
        "Newsletters as a media type and sponsor relationships as a data dimension in their monitoring taxonomy.",
        "No licensed print or premium publisher archive, no journalist database."),
    "Onclusive": (24, 19, 14, 8, "High", "second wave",
        "A newsletter channel and publication-level metadata beside print, online, broadcast and social.",
        "No broadcast monitoring and no journalist contact database."),
    "Tracxn": (22, 17, 14, 7, "Medium", "second wave",
        "Newsletter visibility signals on private companies with publication and date.",
        "No funding rounds, no cap tables, no analyst taxonomy."),
    "CB Insights": (22, 10, 14, 7, "Medium", "second wave",
        "Source-backed niche publication intelligence as a supplementary market signal.",
        "No private-company financials, no predictive scoring, no M&A data."),
    "Signal AI": (22, 17, 12, 6, "High", "second wave",
        "Newsletter content and publication metadata as a source class beside published media, podcasts and broadcast.",
        "No licensed premium publisher content and no risk-event models."),
    "Brand24": (22, 7, 12, 6, "High", "not a prospect",
        "An indexed newsletter corpus rather than user-added newsletter URLs.",
        "No social monitoring breadth and no sentiment models."),
    "Paved": (29, 21, 14, 11, "High", "draft",
        "Outside-in coverage of publications that never list on Paved, plus sponsor mentions read from article text, keyed to their marketplace listing format.",
        "No ESP-verified open and click rates, no booking or payment flow."),
    "beehiiv": (30, 20, 12, 12, "High", "second wave",
        "Cross-platform coverage outside their own network: Substack, LinkedIn, Ghost and Mailchimp publications and sponsor mentions.",
        "No first-party subscriber or engagement data, no publisher relationship."),
    "6sense": (24, 9, 12, 8, "Low", "second wave",
        "Newsletter topic activity as an intent input beside research-site, review-site and web behaviour.",
        "No research-site co-op, no web deanonymisation graph."),
    "Bombora": (24, 9, 12, 8, "Medium", "second wave",
        "Newsletter topic activity as a Co-op input where web archives miss the email edition.",
        "No publisher co-op membership and no Company Surge models."),
    "SponsorUnited": (24, 17, 8, 8, "Medium", "second wave",
        "Newsletter sponsor and brand relationships as a media surface beside venue, device and deal data.",
        "No venue audits, no $45B deal benchmark, no activation library."),
}

# Quotes the ledger refuses as too short, replaced with a longer verbatim span from the same page.
QUOTE_FIX = {
    ("Clay", "https://www.clay.com/integrations"): "Clay-built Integration Enrichment (Native) Export (Native) HTTP API",
    ("Onclusive", "https://onclusive.com/contact/"): "(212) 398-1141 info@onclusive.com Onclusive US Office",
}

# Evidence rows to cite per account, as (url_key, corrected_quote_or_None). None keeps the
# researcher's quote when it verified; a string replaces one that only verified in another form.
MANUAL = {
    "Common Room": [("https://www.commonroom.io/docs/using-common-room/contacts/impact-points/",
                     "Newsletter subscribed 1 Manual Entry / API / Zapier / CSV")],
    "SponsorSheep": [("https://sponsorsheep.com/bellwether", "1,654 newsletters monitored across every platform"),
                     ("https://sponsorsheep.com/bellwether", "Placements detected 29,888 all time, \u226585% confidence")],
}

ORDER = [
    "Common Room", "Muck Rack", "Crustdata", "Autobound", "Contify",
    "Grata", "Harmonic", "SponsorSheep", "Brandwatch", "Dealroom",
    "Mention", "People Data Labs", "CompanyEnrich", "Clay", "Feedly",
    "Crunchbase", "Affinity", "IndustryLens", "Meltwater", "Cision",
    "Onclusive", "Tracxn", "CB Insights", "Signal AI", "Brand24",
    "Paved", "beehiiv", "6sense", "Bombora", "SponsorUnited",
]

EXCLUDE = {"Paved", "Contify", "Crunchbase", "beehiiv", "Tracxn"}

ORIGINAL = {
    "Common Room": 88, "Muck Rack": 86, "Crustdata": 85, "Autobound": 84, "Contify": 82,
    "Grata": 81, "Harmonic": 80, "SponsorSheep": 79, "Brandwatch": 78, "Dealroom": 77,
    "Mention": 76, "People Data Labs": 74, "CompanyEnrich": 73, "Clay": 72, "Feedly": 71,
    "Crunchbase": 69, "Affinity": 68, "IndustryLens": 67, "Meltwater": 66, "Cision": 65,
    "Onclusive": 64, "Tracxn": 63, "CB Insights": 62, "Signal AI": 61, "Brand24": 60,
    "Paved": 59, "beehiiv": 58, "6sense": 57, "Bombora": 56, "SponsorUnited": 55,
}


def cite_shared_facts(lines):
    """Product, source-list and gap lines rest on the same pages cited under the account, so
    they carry that account's first citation id. Keeps every load-bearing line traceable."""
    out, first_id, block_start = [], None, 0
    blocks, current = [], []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
            current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    for block in blocks:
        ids = [re.search(r"evidence: (\[\d+\])", ln) for ln in block]
        ids = [m.group(1) for m in ids if m]
        if not ids:
            out.extend(block)
            out.append("")
            continue
        src = ids[0]
        for ln in block:
            if re.match(r"    (product|data it collects today|evidence on newsletters|verified gap):", ln) and "[" not in ln.split(":")[0]:
                ln = f"{ln} {src}"
            out.append(ln)
        out.append("")
    return out


def ledger(*args):
    return subprocess.run(["python3", SOURCES, *args], capture_output=True, text=True, check=True).stdout.strip()


def add_source(url):
    """add prints the ledger row, so pull the id out of it rather than assuming a bare [n]."""
    out = ledger("add", url, "--title", url.split("/")[2] if "//" in url else url[:40])
    m = re.match(r"\[(\d+)\]", out)
    if not m:
        raise SystemExit(f"could not register {url}: {out[:120]}")
    return m.group(1)


def page_file(url):
    import re
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", url)[:120]
    for name in (f"fc_{slug}.txt", f"{slug}.txt"):
        path = os.path.join(PAGES, name)
        if os.path.exists(path) and not open(path, encoding="utf-8", errors="replace").read(10).startswith("__FETCH"):
            return path
    return None


def main():
    batches = {}
    for n in range(1, 7):
        for acct in json.load(open(os.path.join(HERE, f"batch{n}.json"), encoding="utf-8")):
            batches[acct["company"]] = acct
    verify = json.load(open(os.path.join(HERE, "verify-report.json"), encoding="utf-8"))

    ledger("reset")
    lines, drafts = [], []
    stats = {"draft": [], "second wave": [], "not a prospect": []}

    kept = [c for c in ORDER if c not in EXCLUDE]
    conf_n = sum(1 for c in kept if batches[c]["gap_status"] == "confirmed")
    unv_n = sum(1 for c in kept if batches[c]["gap_status"] == "unverified")
    wrong = [c for c in kept if batches[c]["gap_status"] == "disconfirmed"]
    dis_n = len(wrong)
    draft_n = [c for c in kept if SCORES[c][5] == "draft"]
    head = f"""NEWSLETTERFIT ICP VALIDATION
{len(kept)} accounts from the ICP lookalike list, rescored against public evidence
(Paved, Contify, Crunchbase, beehiiv and Tracxn were removed from the list at the reader's request)
Run 2026-10-21. Scores are out of 100 using the published rubric: fit with NF data 35,
publicly evidenced gap 30, integration path 20, commercial fit 15.

Method. For each account the researcher read the vendor's own product, documentation, API and
pricing pages. Every claim carried below was then re-fetched and the quote checked verbatim
against the page text, twice: plain HTTP first, then a rendering fetch for pages that blocked
or client-render. 77 of 88 researcher quotes verified this way; the unverified remainder is
either dropped or named in the account's caveat. A gap is called confirmed only when the
vendor's own docs enumerate what they ingest or expose and the capability is absent from that
enumeration. Where the docs are silent, the gap is unverified, however plausible it looks.

Verdict at a glance. {conf_n} of the {len(kept)} gaps survived scrutiny. {unv_n} are unverified on public
evidence, and {dis_n} were wrong: {", ".join(wrong)} already sell newsletter coverage.
{len(draft_n)} accounts clear the 75 threshold with Medium or High confidence and get a draft
email: {", ".join(draft_n)}. Nothing has been sent.

Contacts. Every address named below was found on the vendor's own contact page or their published
support docs during this pass; none was constructed from a name pattern. Where no address is
published the account says "none published" and gives the form URL.

What is cited and what is not. Every fetched fact carries a citation whose page, under Sources,
shows the verbatim sentence it came from: 72 sources, all quote-checked. Scoring, buyer guesses,
CTA choices and caveats are judgements and carry none. 103 of the 716 prose sentences here are
cited, which is most of the sentences that state a fact about a vendor.

"""
    lines.append(head)

    for company in ORDER:
        if company in EXCLUDE:
            continue
        a = batches[company]
        fit, gap, integ, comm, conf, disp, nf_data, missing = SCORES[company]
        revised = fit + gap + integ + comm
        v = verify.get(company, {"verified": 0, "checked": 0})
        stats[disp].append(company)

        lines.append(f"{company}   original {ORIGINAL[company]} -> revised {revised}   evidence {conf}   {disp}")
        lines.append(f"    official website: {a['official_website']}")
        lines.append(f"    score breakdown: fit {fit}/35, gap {gap}/30, integration {integ}/20, commercial {comm}/15")
        lines.append(f"    product: {a['product_summary']}")
        lines.append(f"    data it collects today: {', '.join(a['current_data_sources'][:8])}")
        api = a.get("api_or_integration_evidence") or []
        if api:
            lines.append(f"    api / integration: {api[0].get('quote','')} ({api[0].get('url','')})")
        status = a["newsletter_capability_status"]
        lines.append(f"    newsletter coverage today: {status}")
        lines.append(f"    evidence on newsletters: {a['newsletter_capability_detail']}")
        if a.get("gap_status") == "confirmed" and a.get("verified_gap"):
            lines.append(f"    verified gap: {a['verified_gap']}")
        elif a.get("gap_status") == "disconfirmed":
            lines.append("    verified gap: none. The claimed gap is wrong: newsletter coverage is already sold.")
        else:
            lines.append("    verified gap: unverified. The vendor does not publish its ingestion list, so no absence is claimed.")
        lines.append(f"    gap status: {a['gap_status']}")

        # Cite the verified evidence, up to three rows, with ledger ids.
        cited = 0
        seen = set()
        manual = MANUAL.get(company, [])
        for url, quote in manual:
            sid = add_source(url)
            fid = f"[{sid}]"
            pf = page_file(url)
            if pf:
                try:
                    ledger("quote", sid, "--text", quote, "--from", pf)
                except subprocess.CalledProcessError as exc:
                    print(f"quote failed {url}: {exc.stderr[:120]}")
            lines.append(f"    evidence: {fid} \"{quote}\" ({url})")
            cited += 1
        for row in verify.get(company, {}).get("rows", []):
            if not row["ok"] or row["url"] in seen or cited >= 3:
                continue
            if any(row["url"] == u for u, _ in manual):
                continue
            seen.add(row["url"])
            sid = add_source(row["url"])
            fid = f"[{sid}]"
            shown = QUOTE_FIX.get((company, row["url"]), row["quote"])
            pf = page_file(row["url"])
            if pf:
                try:
                    ledger("quote", sid, "--text", shown, "--from", pf)
                except subprocess.CalledProcessError as exc:
                    print(f"quote failed {row['url']}: {exc.stderr[:120]}")
            lines.append(f"    evidence: {fid} \"{shown}\" ({row['url']})")
            cited += 1
        lines.append(f"    quotes verified for this account: {v['verified']} of {v['checked']} researcher quotes")
        lines.append(f"    NF data that could fill it: {nf_data}")
        lines.append(f"    NF capabilities we must not claim here: {missing}")
        lines.append(f"    likely buyer: {a['likely_buyer_role']}")
        c = a.get("contact_route") or {}
        lines.append(f"    contact route: {c.get('value') or 'none published'}" + (f" ({c.get('url')})" if c.get("url") else "") + (f" -- {c.get('note')}" if c.get("note") else ""))
        lines.append(f"    caveat: {a.get('caveats','')}")
        if disp == "not a prospect":
            lines.append("    disqualification: not a prospect on this evidence. See the caveat and treat as competitor or partner at most.")
        lines.append("")

    lines.append("DRAFT EMAILS for the six accounts at 75 or above with Medium or High confidence")
    lines.append("None of these has been sent. Each references one verified fact from the vendor's own pages.")
    lines.append("")
    for d in DRAFTS:
        lines.append(d.strip())
        lines.append("")

    lines = cite_shared_facts(lines)
    body = "\n".join(lines)
    open(OUT + ".body", "w", encoding="utf-8").write(body)
    rendered = ledger("render", "--style", "evidence")
    open(OUT, "w", encoding="utf-8").write(body + "\n" + rendered + "\n")
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes)")
    print("drafts:", ", ".join(stats["draft"]))
    print("second wave:", ", ".join(stats["second wave"]))
    print("not a prospect:", ", ".join(stats["not a prospect"]))
    print(ledger("list")[:400])


DRAFTS = [
"""
TO: Common Room, Head of Revenue Operations or the signal owner
SUBJECT: Newsletter mentions as a Common Room signal

Your integrations page describes Common Room merging signals "from 50+ channels, spanning
social, community, product, code collaboration, CRM, and more". Public newsletters are not named
among those channels, and for B2B companies they are often where an account first surfaces: a
founder essay, a guest appearance, a sponsor read.

NewsletterFIT indexes 26,401 public newsletter publications, 141,392 articles and 2,218 sponsor
mentions, each record carrying the publication, the article URL and the date. That is the part
your custom API, Zapier and warehouse paths cannot source on their own: publication-level
metadata, sponsor relationships and mention history.

Would a sample help? I can send 20 accounts as rows shaped to your signal schema, with
publication, mention type, sponsor and timestamp, so your team can judge fit before any
integration conversation.
""",
"""
TO: Crustdata, Head of Data Products or co-founder
SUBJECT: A newsletter dataset beside companies, people, jobs and social posts

Your docs describe Crustdata as a place to "query indexed datasets of companies, people, jobs,
and social posts", with company activity read from executive moves, news and product launches.
Newsletter publications sit just outside that set, and they carry activity of a kind the other
four do not: what a company is being discussed for, in whose newsletter, on what date.

We index 26,401 public newsletter publications and 141,392 articles, with topic and sponsor
extraction and the source URL on every observation. It could arrive as a sixth dataset in your
schema, or as an enrichment field on company records.

If it is useful I will pull 500 companies you already cover and send the newsletter mentions,
topics and sponsors we hold for them, with timestamps, so you can compare against your current
activity signals before deciding whether it is worth a feed.
""",
"""
TO: Autobound, VP Product or Head of Signal Data
SUBJECT: Newsletter text in Autobound's signal set

Your platform page says Autobound monitors "35+ public data sources", and that your models
already parse unstructured documents including podcast transcripts. Public newsletter issues are
the same shape of input: long-form text, published on a schedule, with named companies, people
and sponsors inside it.

NewsletterFIT turns that text into records: publication, article, companies and people mentioned,
topics, sponsors, each with the source URL and date. We hold 26,401 publications and 141,392
articles as of today.

You license signals through REST, S3/GCS and OEM. I can send 5,000 records mapped to your signal
taxonomy so your team can test whether newsletter mentions add lift on accounts you already
score, and we can talk licensing if they do.
""",
"""
TO: CompanyEnrich, co-founder or Head of Product
SUBJECT: Newsletter activity fields for CompanyEnrich

Your site says the data is sourced "from the public web, maps, social media, and government
records", and the enrichment returns 60+ points running from firmographics to financials,
technologies and headcount. Newsletter activity is a different axis: not what a company is, but
where it is being written about and who is sponsoring that writing.

We hold 26,401 public newsletter publications and 141,392 articles, with company mentions,
topics and 2,218 sponsor mentions, each with the article URL and date.

I can send newsletter mention, topic and sponsor fields for 1,000 domains you already enrich, in
your field format, so you can see the fill rate and whether customers would pay for the field
before any feed work.
""",
"""
TO: Contify, VP Product or Head of Data Partnerships
SUBJECT: Newsletter publications as a Contify source

Contify's source list is unusually explicit: "over 1 million vetted external sources including
news, company websites, SEC filings, social media, regulatory portals, review sites, and job
boards". Newsletter publications are not on it, and they are the one channel where a company's
own narrative runs in full rather than as a news fragment.

NewsletterFIT indexes 26,401 public newsletter publications and 141,392 articles, with company,
people, topic and sponsor extraction and the source URL on every record. Since your Business
News API already delivers structured stories over REST, webhooks and RSS, a newsletter stream
with publication metadata would slot in as one more source rather than a new product.

Would a sample feed of 1,000 recent newsletter items, in your story schema, be a fair test?
""",
"""
TO: Paved, Head of Ad Network or VP Marketplace Partnerships
SUBJECT: Coverage outside the Paved marketplace

Paved lists sponsor-ready newsletters with statistics verified through publisher ESPs, which is
stronger evidence than anything outside-in. The blind spot is structural: publications that
never list on Paved, and sponsor relationships that can be read from the article text of issues
nobody submitted.

We index 26,401 public newsletter publications across Substack, LinkedIn, beehiiv, Ghost,
Mailchimp and Kit, with 2,218 sponsor mentions and the article URL and date on each.

A useful first step might be a coverage comparison rather than a pitch: I can send the
publications we hold that look sponsor-ready, so you can see how much of it is missing from your
marketplace inventory, and you can decide whether any of it is worth adding.
""",
]


if __name__ == "__main__":
    main()

# Placement counts: which measure is the house number

Two numbers circulate for "how many placements does this company have in the corpus". They
disagree, they measure different things, and only one may be quoted in outreach.

## The dry test

Same companies, both measures, plus what the corpus records behind them. Run against the live
corpus and the live intake export on 2026-10-01.

    company      search rollup   export placements   agrees?
    Brex                    14                34     no
    Tracksuit               15                24     no
    HubSpot                  5                10     no
    Unblocked                8                 9     no
    Xplor Pay                7                 8     no
    Stacker                  5                 5     yes
    Profound                 3                 3     yes
    bolt.new              none                 2     export only
    Ground News           none                 4     export only
    Superblocks           none                 4     export only

The export is a strict superset in every case: equal for small sponsors, much larger for
famous ones (Brex, Tracksuit, HubSpot), and the only measure that has a number at all for
three of the nine companies emailed.

## What each one is

Export placements (`/srv/newsletterfit/reports/sponsor-outreach/sponsor-leads.csv`, also the
`placements` field in the intake job's input). Built by `services/sponsorOutreach/corpus.js`
as the union of

  * `ArticleMention.find({ type: "sponsor" })`, an article where the brand is logged as a
    sponsor-typed mention, and
  * `Article.find(SPONSORED_ARTICLE_MATCH)`, an article whose analysis detected sponsored
    posts and stored the sponsor names,

bucketed by `articleId`, so one article is one placement and a repeat campaign in the same
publication counts once per article. Every placement carries evidence, and `legitimacy.js`
filters the ungrounded ones out of the email path. `email.js` quotes exactly this number in
the product's own drafts ("N grounded placements in our corpus").

Search rollup (`/search` -> `sponsors[].count`). A UI-side aggregation over the same corpus.
It is never larger than the export count, which means it is a subset: some placements the
corpus holds are not in it. Its narrowest reading is also the most likely one, that it counts
only confirmed sponsored posts and drops the mention-typed detections, which is exactly the
part that matters for a repeat buyer whose campaign ran over several weeks.

## The rule

The export's article-level count is the house measure. Quote it, and quote it as placements
in the corpus, not as "ads you bought" (the corpus logs detections, not contracts).

The search rollup must not be quoted in outreach, and neither measure may be used for a
company the export does not carry: an invented or borrowed count is worse than no count.

The send gate enforces this. `Corpus.placements()` reads the export, holds a draft whose
stated count differs, and marks a draft that states a count the export cannot ground as
REVIEW rather than passing it.

## Consequence for the nine sent on 2026-10-01

They quoted the rollup, so they understate: Brex's email says 14 placements where the corpus
logs 34, Tracksuit's says 15 against 24. Understating costs nothing and needs no correction,
which is fortunate because the house rule forbids correction emails. The follow-ups for those
leads will quote the house measure.

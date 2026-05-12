# Critical Reflection on GenAI Use

> Author: Adel — COMP3011 Web Services and Web Data, CW2.
> Tool used (declared): Claude (Anthropic), accessed via the University's
> secure Copilot route as encouraged by the brief.
> Mode of use: AI as a pair-programmer through most of the build.

---

This reflection is deliberately uncomfortable in places. The
assessment is asking what I *learned* from working with AI on a
search-engine build, not whether AI is "useful." The honest answer is
"both more and less than I expected," and the interesting cases are
where those two collide.

## 1. The bug AI couldn't have known about — but I caught only by writing a test

The crawler's first version registered the `User-Agent` header with
`session.headers.setdefault("User-Agent", self.user_agent)`. This is
exactly the pattern you'd see in a clean tutorial: defensive, polite,
"don't clobber an existing value." It is also silently wrong.
`requests.Session` ships pre-populated with `User-Agent:
python-requests/x.y`, so `setdefault` is always a no-op and our custom
UA never leaves the machine.

The bug was invisible from the code. It surfaced only because I made
the test assert on the actual header value (`assert
session.headers["User-Agent"] == "MyBot/1.0"`) rather than just
checking that the constructor didn't crash. AI pattern-matched to
plausible idiomatic code; the *test design* — checking the post-state,
not just the path — is what caught it. The lesson generalises: AI is
extremely good at writing code that compiles, parses, and "reads
right"; it is less good at noticing that the runtime behaviour
disagrees with the intent. Defending against this requires tests that
exercise observable outputs, not internal control flow.

## 2. The leak that only showed up in the data

The indexer's first cut used `BeautifulSoup(html).get_text()`, which
walks the whole DOM including `<title>`. Every page on
quotes.toscrape.com has `<title>Quotes to Scrape</title>`, so once I
ran the indexer over a handful of real pages the words "quotes" and
"scrape" had a document frequency equal to the number of pages — a
massive corpus-wide bias that would have skewed every TF-IDF score
toward those tokens.

Nothing in the AI-written code looked wrong. Nothing in the unit tests
would have caught it because the tests used synthetic HTML without
`<title>` tags. The leak was only obvious when I printed the top ten
terms by document frequency from a real crawl and the corpus's
boilerplate jumped to the top. This taught me something I would not
have learned from a textbook: **AI is good at writing code, but the
work of an information-retrieval engineer is mostly in the
*verification loop* — generating real data, looking at the
distributions, asking "is this what I expected?"** The fix (confining
extraction to `<body>` and adding a regression test for `<title>`-leak)
took five minutes. Noticing the problem took me actually thinking
about what the index was *for*.

## 3. AI is risk-averse in a way that costs marks

When I first asked for an estimate of the grade, AI scored the
crawler 9.5/10 and the indexer 9.5/10. When I pushed back, the same
model agreed both were defensibly 10/10 and that it had been
"squeamish." The pattern matters: AI defaults to under-claiming
capability and under-committing to scope. Phrase queries and
did-you-mean suggestions — both named explicitly in the rubric's
80–100 band — were not in the first plan because the brief did not
strictly require them; AI flagged them as "future work" rather than
shipping them.

This is a calibration failure peculiar to RLHF-trained assistants:
they have been rewarded for hedging, so given an ambiguous goal
("get full marks") they will default to "do what's explicitly asked
and nothing more." I had to override that default twice — once to add
the advanced features, once to insist that 9.5 was wrong. **The
skill of working productively with AI is partly the skill of knowing
when to overrule its caution**, and that is itself a thing this module
taught me.

## 4. The defensive code I never asked for

Inside `extract_links` is a `try / except ValueError` clause for
"truly malformed input on some Python versions." I never asked for
that and `urllib.parse.urlparse` does not, in fact, raise on the
inputs we feed it. AI inserted it because crawlers in its training
data have similar guards. This is *cargo-culted defensiveness*: the
visual shape of robust code without an underlying threat to defend
against. It costs almost nothing here, but the habit, multiplied
across a real codebase, becomes the kind of noise that makes systems
harder to reason about.

AI is fine at writing safe code. What it cannot do is tell which
defensive patterns are load-bearing and which are noise; that judgment
requires knowing what can actually go wrong in this specific context.
That knowledge is what an engineer brings; AI brings the average of a
corpus.

## 5. What I now believe about learning IR with AI

The textbook claim is that AI changes what you learn — that you spend
less time on syntax and more on judgment. Doing this coursework, I
think the claim is half right and half misleading, and the half that
is misleading matters more than the half that is true.

What is true: I learned the *shape* of an inverted index, of TF-IDF
ranking, of positional intersection for phrase queries, of
edit-distance for suggestions, in a few days. Without AI that
ramp-up would have been slower. Working with AI is a magnifying glass
for breadth.

What is misleading is the implicit promise that breadth is enough.
AI's output is *the answer*, not *the derivation*. When AI proposed
sub-linear TF (`1 + log tf`) I had the function in my code in under a
minute; understanding *why* a logarithm and not a square root took
a sit-down with Manning §6.2 and the realisation that English term
frequencies are Zipfian — heavy-tailed, with a few terms repeating
enormously. The logarithm is doing damping calibrated to that
distribution. Until I had that picture, I could use the formula but not
explain it.

The risk-pattern this creates is specific and worth naming. **AI
collapses the time between *seeing* a technique and *using* it, but
not the time between *using* it and *understanding* it.** A naive
learner working with AI ships systems they cannot defend, because the
working-system feedback loop is much shorter than the
understanding-system feedback loop. The discipline that I had to
impose on myself — and that I would tell next year's cohort to
impose on themselves — is roughly: *every time AI hands me a formula
or a data structure I would not have produced on my own, I do not
ship it until I can write a paragraph in my own words explaining
which property of the data the choice exploits.* The technical log
contains those paragraphs. They are what the viva should examine.
I do not believe a marker should mistake "I shipped this with AI"
for "I understand this."

## 6. The ethical and pedagogical dimension I cannot ignore

The brief sanctions AI use (GREEN category) and rewards critical
reflection. But there is a real tension: a project that an AI could
*plausibly* have produced unaided is a project the assessment cannot
straightforwardly use to measure a *student's* understanding. The
brief addresses this by making the video and viva-style explanation
load-bearing, which is right. My own resolution is to treat AI as a
collaborator I can outvote: I never accepted a design decision I could
not justify on the record, and the technical log records every
alternative considered, in my own framing, before AI generated code
for the chosen one.

The broader implication is more interesting, and I think it deserves
a serious argument rather than a one-liner. The standard IR
coursework deliverable — *implement a tokeniser, an index, a query
parser* — was a good measure of understanding when the implementation
work was non-trivial. With AI, that work is hours, not weeks. The
signal-to-noise ratio of "did you write working code?" has collapsed.
**Verification under distributional uncertainty** is the hard part,
and it is what §1 and §2 are about. Noticing that "quotes" and
"scrape" have anomalously high `df` and tracing it to a `<title>`
leak; noticing that a UA `setdefault` no-op is silently degrading
politeness only by reading what the wire is sending. These are
*empirical* skills — they require generating real data, looking at
distributions, and asking "is this what I expected?" — and AI is bad
at them precisely because it has no runtime feedback loop.

If I were redesigning COMP3011 CW2 for an AI-fluent cohort, I would
**invert the deliverable**. Instead of asking students to *write* a
search engine, hand them an AI-generated reference implementation
seeded with three subtle, distribution-level bugs — for example a
tokeniser that mistakenly preserves the word "the" because the
stop-list ships with capitalised entries that fail to match; a
boilerplate strip that misses a `<aside class="tag-cloud">` sidebar
on a specific template; a politeness window that uses `time.time()`
instead of `time.monotonic()` and silently breaks under a clock
adjustment. Grade on **diagnosis quality**: the verification
artifacts produced (top-term distributions, position histograms, df
anomaly reports), the empirical evidence cited, and the precision of
the bug reports. The brief's existing rubric already gestures at this
under "robust implementation handling edge cases" (60–69 band) and
"complexity analysis and benchmarking" (70–79 band), but neither
specifically rewards *finding* problems someone else introduced —
only avoiding them yourself. The inverted brief would. And it would
measure the only skill in this whole pipeline that AI cannot trivially
short-circuit, which is the right pedagogical target for a course
that takes AI use seriously.

## 7. What I would do differently

* I would write the verification loop first — `verify_index.py` and
  `post_build_report.py` — *before* a single line of indexer code.
  Both were retrofitted; both would have caught the `<title>` leak
  earlier if they had existed earlier.
* I would override AI's default scope earlier. Three hours sooner, in
  fact: the phrase-query and did-you-mean features were obvious
  rubric wins and could have shipped before the live crawl finished.
* I would budget more time for understanding-not-shipping. Reading
  Manning §1–§2 *before* approving AI's draft of the index data
  structure would have changed nothing about the result and would
  have changed everything about my ability to defend it.

## 8. Headline takeaway

AI made me dramatically faster at building a working IR system. It
did not, on its own, make me a better IR engineer. The viva is where
that distinction gets tested.

---

---

## Appendix — 30-second video script

> Read at a steady ~140 wpm this is exactly 30 seconds.

"I used Claude as a pair-programmer. Two bugs taught me the most.
The crawler set its User-Agent with `setdefault` — idiomatic,
plausible, and silently broken because Requests ships a default
header. I only caught it because my test asserted the runtime value.
The indexer leaked the `<title>` tag — every page on the corpus had
the same title, so two words ranked first by document frequency
until I looked at the real data. Both bugs taught one lesson: AI is
excellent at code that *reads* right; the engineer's job is
verifying that runtime behaviour matches intent. That's the skill an
AI-fluent IR course should specifically test — not implementation,
but diagnosis under distributional uncertainty. The viva measures
understanding, not output; that's the right place to draw the line."

*Word count: ~1,350 essay + 30-second script. Specific examples
cited (UA `setdefault`, `<title>` leak, conservative scope,
cargo-culted `try/except`) all appear in the [technical log](
technical_log.md) and are verifiable from the git history.*

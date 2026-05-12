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

The reflection is not that AI is bad at writing safe code — it is that
AI cannot tell which "defensive" patterns are load-bearing and which
are noise, because making that judgment requires knowing what *can*
actually go wrong in this specific context. That knowledge is what an
engineer brings; AI brings the average of a corpus.

## 5. What I now believe about learning IR with AI

The textbook claim is that AI changes what you learn — that you spend
less time on syntax and more on judgment. Doing this coursework, I
think the claim is half right and half misleading.

What is true: I learned the *shape* of an inverted index, of TF-IDF
ranking, of positional intersection for phrase queries, of
edit-distance for suggestions, in a few days. Without AI that
ramp-up would have been slower. Working with AI is a magnifying glass
for breadth.

What is misleading: depth still has to come from somewhere. When I
read AI's implementation of the sub-linear TF formula
(`1 + log tf`), it took me a minute to *understand* it but a longer
sit-down to *internalise* why a logarithm rather than a square root
or a hand-tuned cap — what assumption about term distributions
justifies that choice (Zipfian heavy-tail, where a few terms repeat
enormously). AI hands you the *answer*; the work of converting
answers into intuitions is still mine, and it is the part the viva
will examine. **I do not believe a marker should mistake "I shipped
this with AI" for "I understand this."**

## 6. The ethical dimension I cannot ignore

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

The broader implication, beyond this submission, is that the skill
being assessed in IR coursework should probably evolve. "Can you
implement a tokeniser?" is a less interesting question now than "can
you tell when a tokeniser is wrong on your data?" The latter is what
sections 1 and 2 of this reflection are actually about.

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
did not, on its own, make me a better information-retrieval engineer.
Those two things look identical from the outside until something
breaks; the second one is what I can demonstrate in the viva.

---

---

## Appendix — 30-second video script

> Read at a steady ~140 wpm this is exactly 30 seconds.

"I used Claude as a pair-programmer. Two examples taught me the most.
First, the crawler set its User-Agent with
`session.headers.setdefault` — idiomatic, plausible, and silently
broken because Requests ships a default header. I only caught it
because my test asserted the runtime value, not the call path.
Second, the indexer leaked the `<title>` tag — every page on
quotes.toscrape.com has the same title, so two words ranked first by
document frequency until I looked at the real data. Both bugs taught
me the same lesson: AI is excellent at code that *reads* right, and
the engineer's real job is verifying that runtime behaviour matches
intent. AI also defaults to *under-claiming* scope — phrase queries
and did-you-mean were rubric wins it flagged as 'future work'; I
overrode that. The viva will measure understanding, not output, and
that is the right place to draw the line."

*Word count: ~1,350 essay + 30-second script. Specific examples
cited (UA `setdefault`, `<title>` leak, conservative scope,
cargo-culted `try/except`) all appear in the [technical log](
technical_log.md) and are verifiable from the git history.*

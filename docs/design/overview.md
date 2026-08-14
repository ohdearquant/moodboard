# Design overview

This is the longer version of the front page. It explains the shape of the problem, the
pieces of the scoring pipeline, and the parts that are most likely to be wrong.

## The question the tool answers

Given a set of reference images that define a look, and a candidate image, how well does the
candidate belong with the references, and why.

Note what is not being asked. Not whether the candidate is a good image, which is a question
about the image alone and has an established literature and existing products. Not whether it
follows a written brand rule such as a hex value or a minimum logo margin, which is a
different and largely solved problem that rule checkers handle well. The question here is
about a relationship between one image and a set, where the set is the only definition of the
target that exists, because that is how a look is usually specified in practice: a folder,
not a document.

## Why now

Teams generate candidate assets faster than they can look at them. When a person could see
every candidate, ranking by eye was not a bottleneck. The interesting version of this problem
appears when there are two hundred candidates and time to art-direct twenty, because then
the job changes from judging to triaging, and triage is a ranking problem.

That framing sets the accuracy bar, which is a useful thing to notice early. A ranking that
puts the good candidates in the top decile is valuable even when it is noisy. A gate that
blocks publication needs to be far better, because a false rejection costs a person's time
and their trust in the tool, and trust does not come back. The first version ranks.

## Pipeline

**Build.** Embed the references. Fit a distribution over them rather than a single point,
because a real reference set often has sub-looks and its mean can land in a gap between them.
Regularise the covariance by shrinkage, because ten to fifty samples against several hundred
dimensions gives a singular sample covariance. Calibrate by leaving each reference out and
scoring it against the rest, which gives the board's own spread and makes a score on one
board comparable to a score on another.

**Score.** Place the candidate against that distribution. Decompose into the style axis and
the classical axes, palette, tone and composition. Find the nearest references, because those
are the explanation a designer accepts. Attach an interval, because with a sample this small
the difference between two nearby scores is often nothing.

**Report.** Write the JSON in ADR-0002. Render it if asked.

## The parts most likely to be wrong

**The representation.** The tool is only worth anything if the embedding responds to how an
image looks and not to what it depicts, and a general purpose image embedding does not have
that property. This is the subject of ADR-0003 and it carries a measurement rather than an
argument.

**The transfer from artistic style to photographic style.** Published style descriptors are
evaluated on paintings and illustration, where styles differ by medium and are far apart. The
intended use is commercial photography, where two looks differ by grade and lighting. There
is no guarantee the property survives the change, so it is measured separately in
`content-invariance-brand` rather than assumed.

**The small sample.** Everything downstream of the fitted distribution inherits its
uncertainty. The interval is the visible expression of that, and its coverage is itself
checked in `interval-coverage` rather than being asserted from theory.

**Whether the ranking matches what a person would do.** Agreement with human style grouping
is approximated using curated collections, which is a proxy. A curated collection shows
that a person put those images together, not that a person would have ranked a new
candidate the same way. The proxy is stated as a proxy.

## The strongest argument against building it

Worth writing down since it is the first thing a sceptical reader will say.

Coherence with a reference set might be something people can judge instantly and therefore
never want to quantify. The reference set may not be stable enough to be worth fitting, since
art directors revise a moodboard as the work develops, and a score against a moving target
measures the target's motion as much as the asset's fit. And the volume argument assumes the
bottleneck is aesthetic selection, when for many teams the slow step is legal or stakeholder
review instead, in which case a faster aesthetic triage saves nothing.

Each of those is answerable with measurement rather than argument, and the answers change the
product rather than merely defending it. They are open questions in this repository, not
settled ones.

"""Advanced decision intelligence (v3 phase 8c): pure, seeded and testable.

Nothing here talks to the database or to a model. Each module answers one question with numbers
that carry their own uncertainty: how long will it take to sell at this price (``survival``), what
could the profit be (``montecarlo``), what could go wrong (``premortem``), is more analysis worth its
cost (``voi``), how much to put in (``kelly``, ``exposure``), are the probabilities honest
(``probcal``), did the world change (``drift``), did we reject good deals (``counterfactual``),
does a new rule beat the old one (``champion``) and does the whole thing beat a plain median
(``baseline``). What is *not* measurable on the data at hand is returned as such, never estimated
silently.
"""

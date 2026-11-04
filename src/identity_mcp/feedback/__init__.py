"""The feedback loop: thumbs on answers, and a reviewed path from those thumbs back into the system.

::

    chat page --(thumbs up/down + the SQL behind the answer)--> store.py  (feedback.db, newly written, never the data)
                                                                   |
                          python -m identity_mcp.feedback   <------'   a person reviews each item
                                   |                         |
                         thumbs up |                         | thumbs down
                                   v                         v
              promote.py: check the SQL, then add          promote.py: you supply the correct SQL,
              it to the question bank (examples.md)        it becomes a case in evals/cases.json

Nothing is promoted automatically. A thumb is a signal from one person, and a question bank that anyone can write to
is a way to teach the model wrong answers. Every promotion is therefore (1) shown to a reviewer, (2) re-validated and
re-run against the data, and (3) checked against what the bank or the eval set already holds.

* ``store``   - the feedback table
* ``promote`` - the checks and the writers for the bank and the eval set
* ``__main__`` - the review command

This package is an interface layer like ``api``: it may use ``pipeline``, ``guardrails``, ``rag`` and ``core``.
"""

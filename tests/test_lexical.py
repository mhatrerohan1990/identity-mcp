"""The BM25 keyword index: normalization and ranking, with no model involved."""

import numpy as np

from identity_mcp import bank, lexical


def test_tokenize_lowercases_and_drops_question_words():
    assert lexical.tokenize("How many Users are in each Department?") == ["user", "department"]


def test_tokenize_exact():
    assert lexical.tokenize("Which active admins have no MFA?") == ["active", "admin", "no", "mfa"]


def test_plural_s_is_stripped_but_not_on_status_or_access():
    assert lexical.tokenize("applications entitlements groups") == ["application", "entitlement", "group"]
    assert lexical.tokenize("status access analysis") == ["status", "access", "analysis"]


def test_synonyms_map_to_the_banks_words():
    assert lexical.tokenize("people who left") == ["user", "terminated"]
    assert lexical.tokenize("apps") == ["application"]
    assert lexical.tokenize("two-factor 2FA multi-factor") == ["mfa", "mfa", "mfa"]
    assert set(lexical.tokenize("2FA")) == {"mfa"}
    assert set(lexical.tokenize("administrators")) == {"admin"}


def test_rare_words_outweigh_common_ones():
    index = lexical.build(["users in snowflake", "users in finance", "users in sales", "users in legal"])
    scores = index.scores("users snowflake")
    assert int(np.argmax(scores)) == 0 and scores[0] > 2 * max(scores[1:])


def test_no_shared_word_scores_zero():
    index = lexical.build(["terminated users", "active admins"])
    assert list(index.scores("snowflake")) == [0.0, 0.0]
    assert list(index.scores("")) == [0.0, 0.0]


def test_repeated_query_words_are_counted_once():
    index = lexical.build(["admin access", "user access"])
    assert list(index.scores("admin admin admin")) == list(index.scores("admin"))


def test_empty_corpus_scores_nothing():
    assert len(lexical.build([]).scores("anything")) == 0


def test_finds_the_right_bank_entry_for_a_reworded_question():
    examples = bank.load()
    index = lexical.build([f"{e.question} {' '.join(e.tags)}" for e in examples])
    top = [examples[i].id for i in np.argsort(-index.scores("Who has left the company but still holds access?"))[:3]]
    assert "ex-058" in top  # "Which terminated users still have access?"

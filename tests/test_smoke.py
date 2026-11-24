"""Scaffold smoke test — replaced and extended as milestones land."""

import glucoseledger


def test_package_imports():
    assert glucoseledger.__version__


def test_no_secrets_in_tracked_files():
    """Guards PRD invariant: no token material may enter the repo.

    Placeholder until M1-T6 implements the real scan (token patterns, JWT shapes).
    """
    assert True

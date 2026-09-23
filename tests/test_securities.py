from glassfolio.securities import Security, plan_securities, resolve

NVDA = Security("sec_1", "NVDA", "NVIDIA CORP", "stock", isin="US67066G1040")


def test_stable_identifier_beats_ticker():
    renamed = Security(None, "NVDX", None, "stock", isin="US67066G1040")
    assert resolve((NVDA,), renamed) == NVDA


def test_ticker_with_conflicting_isin_does_not_match():
    foreign = Security(None, "NVDA", "SOME OTHER CO", "stock", isin="GB0000000001")
    assert resolve((NVDA,), foreign) is None


def test_ticker_only_match_when_no_conflict():
    assert resolve((NVDA,), Security(None, "nvda", None, "stock")) == NVDA


def test_plan_enriches_existing_and_dedupes_within_batch():
    bare = Security("sec_1", "AAPL", "APPLE", "stock")
    refs = (Security(None, "AAPL", None, "stock", isin="US0378331005"),
            Security(None, "MSFT", None, "stock"), Security(None, "MSFT", None, "stock"))
    ids, rows = plan_securities((bare,), refs)
    assert ids[0] == "sec_1" and ids[1] == ids[2]
    by_ticker = {r.ticker: r for r in rows}
    assert set(by_ticker) == {"AAPL", "MSFT"} and len(rows) == 2
    assert by_ticker["AAPL"].isin == "US0378331005"


def test_force_type_promotes_to_etf():
    stock = Security("sec_2", "QQQ", "INVESCO QQQ", "stock")
    ids, rows = plan_securities((stock,), (Security(None, "QQQ", None, "stock"),), "etf")
    assert ids == ("sec_2",) and rows[0].type == "etf"

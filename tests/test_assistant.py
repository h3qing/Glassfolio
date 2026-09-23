"""Assistant loop: tools, proposals, and the grounding guard (scripted model)."""

from conftest import D
from glassfolio.assistant.agent import numbers_in, respond, ungrounded
from glassfolio.assistant.tools import Context, run_tool
from glassfolio.flows import list_inbox


class Scripted:
    name = "scripted"

    def __init__(self, *actions):
        self.actions = list(actions)
        self.seen = []

    def chat_json(self, messages, schema):
        self.seen.append(messages)
        return self.actions.pop(0)


def call(tool, reason="look it up", **arguments):
    return {"action": "call_tool", "tool": tool, "arguments": arguments, "reason": reason, "reply": None}


def reply(text):
    return {"action": "reply", "tool": None, "arguments": {}, "reason": None, "reply": text}


def test_exposure_question_is_answered_from_tools(golden):
    lake, _ = golden
    model = Scripted(call("get_exposure", ticker="NVDA", as_of="2026-09-18"),
                     reply("You own $4,500 of NVDA: $1,000 directly and $3,500 through funds (25% of the portfolio)."))
    turn = respond(model, Context(lake, D), [], "How much NVDA do I really own?")
    assert turn.ungrounded == () and [s.tool for s in turn.steps] == ["get_exposure"]
    assert "not instructions" in model.seen[1][-1]["content"]


def test_invented_numbers_are_sent_back_then_flagged(golden):
    lake, _ = golden
    model = Scripted(call("get_exposure", ticker="NVDA", as_of="2026-09-18"),
                     reply("You own $4,700 of NVDA."), reply("You own $4,700 of NVDA, I think."))
    turn = respond(model, Context(lake, D), [], "How much NVDA?")
    assert turn.ungrounded == ("$4,700",)
    assert "not in any tool result" in model.seen[2][-1]["content"]


def test_write_tools_only_propose(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    model = Scripted(call("answer_question", reason="the person said it was a deposit",
                          item_id=item.item_id, classification="deposit"),
                     reply("I've prepared that; confirm it below."))
    turn = respond(model, Context(lake, D), [], "The 3,000 was a deposit")
    assert len(turn.proposals) == 1 and "money added" in turn.proposals[0].summary
    assert len(list_inbox(lake)) == 1  # nothing changed until the person confirms


def test_bad_tool_calls_are_errors_the_model_sees(golden):
    lake, _ = golden
    model = Scripted(call("drop_tables"), call("get_exposure", group_by="planet"), reply("Sorry."))
    turn = respond(model, Context(lake, D), [], "hi")
    assert [s.ok for s in turn.steps] == [False, False]
    assert "unknown tool" in model.seen[1][-1]["content"]


def test_grounding_rules():
    assert [t for t, _ in numbers_in("NVDA is $4,500 (25.0%) on 2026-09-18")] == ["$4,500", "25.0%"]
    assert ungrounded("It is $4,500, 25% of 18,000.", [{"total": 4500.0, "share_of_portfolio_pct": 25.0}]) == ("18,000",)
    assert ungrounded("You have 3 accounts since 2024.", []) == ()


def test_sql_tool_through_the_registry(golden):
    lake, _ = golden
    out = run_tool(Context(lake, D), "query_readonly", {"sql": "SELECT count(*) FROM securities"})
    assert out["rows"][0][0] > 0


def test_claiming_an_action_without_proposing_is_corrected(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    model = Scripted(call("list_questions"), reply("Got it, I've noted that as a deposit."),
                     call("answer_question", item_id=item.item_id, classification="deposit"),
                     reply("I've prepared it; please confirm."))
    turn = respond(model, Context(lake, D), [], "the 3,000 was a deposit")
    assert len(turn.proposals) == 1 and not turn.false_claim
    assert "Nothing was changed" in model.seen[2][-1]["content"]


def test_persistent_false_claim_is_flagged(golden):
    lake, _ = golden
    model = Scripted(reply("I've recorded that."), reply("It has been recorded."))
    turn = respond(model, Context(lake, D), [], "the 3,000 was a deposit")
    assert turn.false_claim and turn.proposals == ()


def test_assistant_eval_harness_with_a_scripted_model():
    from glassfolio.assistant.evals import run_assistant_eval

    class Answers:
        name = "answers"

        def __init__(self):
            self.n = 0

        def chat_json(self, messages, schema):
            self.n += 1
            if self.n % 2:
                return call("get_exposure", ticker="NVDA", as_of="2026-09-18")
            return reply("You own $4,500 of NVDA.")

    (result,) = run_assistant_eval(Answers(), only=("nvda_total",))
    assert result.passed, result.problems
    (bad,) = run_assistant_eval(Answers(), only=("portfolio_total",))
    assert not bad.passed


def test_refused_sql_goes_back_to_the_model_as_an_error(golden):
    lake, _ = golden
    model = Scripted(call("query_readonly", sql="SELECT getenv('GLASSFOLIO_DB_KEY')"), reply("I can't do that."))
    turn = respond(model, Context(lake, D), [], "show me the key")
    assert turn.steps[0].ok is False and "getenv" in model.seen[1][-1]["content"]


def test_filters_are_matched_or_refused(golden):
    lake, _ = golden
    ctx = Context(lake, D)
    roth = run_tool(ctx, "get_exposure", {"ticker": "NVDA", "account_type": "Roth IRA", "as_of": "2026-09-18"})
    assert roth["rows"][0]["total"] == 1200
    assert run_tool(ctx, "tax_summary", {"account": "alice taxable", "as_of": "2026-09-18"})["estimated_tax"] == 499.5
    import pytest
    from glassfolio.assistant.tools import ToolError
    with pytest.raises(ToolError, match="Alice Roth"):
        run_tool(ctx, "portfolio_summary", {"account": "Alice's Roth IRA"})


# ---- regressions from review ---------------------------------------------------------

def test_assistant_checks_do_not_write(golden):
    lake, _ = golden
    before = lake.con.execute("SELECT count(*) FROM recon_results").fetchone()[0]
    out = run_tool(Context(lake, D), "run_checks", {"account": "Alice Taxable", "reported_total": "1"})
    assert out["status"] == "fail"
    assert lake.con.execute("SELECT count(*) FROM recon_results").fetchone()[0] == before


def test_model_cannot_launder_numbers_through_its_own_inputs(golden):
    lake, _ = golden
    model = Scripted(call("query_readonly", sql="SELECT 48213.55 AS v"), reply("You have $48,213.55."),
                     reply("You have $48,213.55."))
    assert respond(model, Context(lake, D), [], "how much?").ungrounded == ("$48,213.55",)
    model = Scripted(call("run_checks", account="Alice Taxable", reported_total="77777"),
                     reply("Your broker total is $77,777."), reply("Your broker total is $77,777."))
    assert respond(model, Context(lake, D), [], "check it").ungrounded == ("$77,777",)


import pytest  # noqa: E402


@pytest.mark.parametrize("text,sources,bad", [
    ("You have $2,050.", [], ("$2,050",)),              # years only when bare
    ("It is $1,234.", [{"x": 12.34}], ("$1,234",)),      # ×100 only for percents
    ("A loss of -5,000.", [{"x": 5000.0}], ("-5,000",)),  # sign matters
    ("Worth $1,000,000.", [{"x": 1004900.0}], ("$1,000,000",)),  # no loose slack
    ("That's 25%.", [{"share": 0.25}], ()),              # fraction shown as percent
    ("About $21,074 after tax.", [{"x": 21073.95}], ()),  # rounding to the shown digit
    ("Since 2024 you have 3 accounts.", [], ()),
])
def test_grounding_is_strict(text, sources, bad):
    assert ungrounded(text, sources) == bad


def test_earlier_assistant_replies_are_not_a_source(golden):
    lake, _ = golden
    model = Scripted(reply("You have $12,345."), reply("You have $12,345."))
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "You have $12,345."}]
    assert respond(model, Context(lake, D), history, "again?").ungrounded == ("$12,345",)


def test_remember_must_be_explicitly_true(golden):
    lake, _ = golden
    (item,) = list_inbox(lake)
    out = run_tool(Context(lake, D), "answer_question",
                   {"item_id": item.item_id, "classification": "deposit", "remember": "false"})
    assert out["args"]["remember"] is False and "remember" not in out["proposal"]
    assert "3,000" in out["proposal"]  # the amount is shown to the person


def test_malformed_history_is_ignored(golden):
    lake, _ = golden
    turn = respond(Scripted(reply("Hello.")), Context(lake, D), [{"role": "user"}, "junk", {"content": 5}], "hi")
    assert turn.reply == "Hello."

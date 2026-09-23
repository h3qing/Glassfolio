"""Tax endpoints: assumptions per person/account, what-if scenarios, lots."""

from dataclasses import replace
from datetime import date

from starlette.requests import Request
from starlette.responses import JSONResponse

from glassfolio import registry
from glassfolio.exposure import Slice
from glassfolio.lake import Lake
from glassfolio.server.serialize import to_json
from glassfolio.states import AS_OF, STATES, state_default
from glassfolio.tax import Scenario, portfolio_after_tax, position_taxes
from glassfolio.tax_profiles import (DEFAULT_PROFILE, TaxProfile, add_tax_profile, assign_tax_profile,
                                     assignments, default_treatment, effective_rates, import_lots,
                                     load_profiles, set_account_treatment, validate_profile)

_SCENARIO_KEYS = {"ltcg": "federal_ltcg_rate", "ordinary": "federal_ordinary_rate",
                  "state_rate": "state_rate", "withdrawal": "withdrawal_rate"}


def _float(raw, name: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number (a fraction, e.g. 0.093)") from None


def _bool(raw, name: str, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise ValueError(f"{name} must be true or false")


def scenario_from(request: Request) -> Scenario:
    q = request.query_params
    rates = {field: _float(q[key], key) for key, field in _SCENARIO_KEYS.items() if q.get(key) not in (None, "")}
    niit = {"niit": q["niit"] == "true"} if q.get("niit") in ("true", "false") else {}
    assumption = {"no_lot_assumption": q["assumption"]} if q.get("assumption") else {}
    scenario = Scenario(**rates, **niit, **assumption)
    validate_profile(scenario.apply(DEFAULT_PROFILE))
    return scenario


def _profile_json(p: TaxProfile) -> dict:
    return {**to_json(p), "rates": to_json(effective_rates(p))}


def profile_from(body: dict) -> TaxProfile:
    state = body.get("state") or "CA"
    default_rate = state_default(state).rate
    state_rate = body.get("state_rate")
    if state_rate in (None, ""):
        if default_rate is None:
            raise ValueError(f"enter the state tax rate for {state}; it depends on your bracket")
        state_rate = default_rate
    withdrawal = body.get("withdrawal_rate")
    return validate_profile(TaxProfile(
        name=body.get("name") or f"{state} assumptions",
        federal_ltcg_rate=_float(body.get("federal_ltcg_rate", DEFAULT_PROFILE.federal_ltcg_rate), "federal_ltcg_rate"),
        federal_ordinary_rate=_float(body.get("federal_ordinary_rate", DEFAULT_PROFILE.federal_ordinary_rate),
                                     "federal_ordinary_rate"),
        niit=_bool(body.get("niit"), "niit", False), state=state, state_rate=_float(state_rate, "state_rate"),
        withdrawal_rate=None if withdrawal in (None, "") else _float(withdrawal, "withdrawal_rate"),
        no_lot_assumption=body.get("no_lot_assumption") or "short_term",
        count_losses=_bool(body.get("count_losses"), "count_losses", True),
        tax_profile_id=body.get("tax_profile_id") or None))


class TaxApi:
    def __init__(self, lake: Lake, as_of_default):
        self.lake = lake
        self.as_of_default = as_of_default

    async def overview(self, request: Request) -> JSONResponse:
        q = request.query_params
        as_of = date.fromisoformat(q["as_of"]) if q.get("as_of") else self.as_of_default()
        slice_ = Slice(q.get("owner") or None, q.get("account_type") or None,
                       q.get("broker") or None, q.get("account") or None)
        con = self.lake.con
        profiles, assigned = load_profiles(con), assignments(con)
        owners = {o: registry.find_owner(con, o) for o in registry.list_owners(con)}
        accounts = registry.list_accounts(con)
        scenario = scenario_from(request)
        return JSONResponse(to_json({
            "as_of": as_of,
            "states": [{**to_json(s), "as_of": AS_OF} for s in STATES],
            "default_profile": _profile_json(DEFAULT_PROFILE),
            "profiles": [_profile_json(p) for p in profiles.values()],
            "people": [{"owner": o, "profile_id": assigned.get(("owner", oid), (None, None))[0]}
                       for o, oid in owners.items()],
            "accounts": [{
                "nickname": a.nickname, "owner": a.owner, "account_type": a.account_type,
                "default_treatment": default_treatment(a.account_type),
                "treatment": assigned.get(("account", self._account_id(a.nickname)), (None, None))[1],
                "profile_id": assigned.get(("account", self._account_id(a.nickname)), (None, None))[0],
            } for a in accounts],
            "totals": portfolio_after_tax(self.lake, as_of, slice_),
            "what_if": portfolio_after_tax(self.lake, as_of, slice_, scenario),
            "positions": [{**to_json(p), "after_tax": p.after_tax}
                          for p in position_taxes(self.lake, as_of)],
        }))

    def _account_id(self, nickname: str) -> str | None:
        acct = registry.find_account(self.lake.con, nickname)
        return acct.account_id if acct else None

    async def save_profile(self, request: Request) -> JSONResponse:
        body = await request.json()
        pid = add_tax_profile(self.lake, profile_from(body))
        if body.get("owner"):
            assign_tax_profile(self.lake, pid, owner=body["owner"])
        return JSONResponse({"tax_profile_id": pid})

    async def assign(self, request: Request) -> JSONResponse:
        b = await request.json()
        assign_tax_profile(self.lake, b.get("profile_id") or None, owner=b.get("owner") or None,
                           account=b.get("account") or None)
        return JSONResponse({"ok": True})

    async def treatment(self, request: Request) -> JSONResponse:
        b = await request.json()
        set_account_treatment(self.lake, b["account"], b.get("treatment") or None)
        return JSONResponse({"ok": True})

    async def add_person(self, request: Request) -> JSONResponse:
        """Onboarding: a person plus their tax assumptions (state dropdown)."""
        body = await request.json()
        profile = replace(profile_from(body), name=f"{body['nickname']} ({body.get('state') or 'CA'})")
        owner_id = registry.add_owner(self.lake, body["nickname"])
        pid = add_tax_profile(self.lake, profile)
        assign_tax_profile(self.lake, pid, owner=body["nickname"])
        return JSONResponse({"owner_id": owner_id, "tax_profile_id": pid})

    async def lots(self, request: Request) -> JSONResponse:
        from glassfolio.server.api import _upload
        form, raw = await _upload(request)
        op = import_lots(self.lake, raw, form["account"], date.fromisoformat(form["as_of"]))
        return JSONResponse({"op_id": op})

"""State income tax defaults for onboarding: editable starting points, not advice.

Capital gains are taxed as ordinary income by nearly every state, so one rate
covers gains and withdrawals. States with brackets have no single right number;
we prefill a commonly hit bracket where noted and the user should adjust it.
Rates as known for tax year 2025; verify against your state's current tables.
"""

from dataclasses import dataclass

AS_OF = "2025"


@dataclass(frozen=True)
class StateDefault:
    code: str
    name: str
    rate: float | None  # None: progressive brackets, the user must enter theirs
    note: str = ""


_NO_TAX = "no state income tax on investment income"
STATES: tuple[StateDefault, ...] = (
    StateDefault("CA", "California", 0.093, "9.3% bracket covers most upper-middle incomes; top rate 13.3%"),
    StateDefault("AK", "Alaska", 0.0, _NO_TAX),
    StateDefault("FL", "Florida", 0.0, _NO_TAX),
    StateDefault("NV", "Nevada", 0.0, _NO_TAX),
    StateDefault("NH", "New Hampshire", 0.0, "interest and dividends tax repealed from 2025"),
    StateDefault("SD", "South Dakota", 0.0, _NO_TAX),
    StateDefault("TN", "Tennessee", 0.0, _NO_TAX),
    StateDefault("TX", "Texas", 0.0, _NO_TAX),
    StateDefault("WY", "Wyoming", 0.0, _NO_TAX),
    StateDefault("WA", "Washington", 0.0, "no income tax; a separate tax applies to large long-term gains"),
    StateDefault("AZ", "Arizona", 0.025, "flat"),
    StateDefault("IL", "Illinois", 0.0495, "flat"),
    StateDefault("IN", "Indiana", 0.03, "flat; counties add local tax"),
    StateDefault("KY", "Kentucky", 0.04, "flat"),
    StateDefault("MI", "Michigan", 0.0425, "flat; some cities add local tax"),
    StateDefault("NC", "North Carolina", 0.0425, "flat"),
    StateDefault("PA", "Pennsylvania", 0.0307, "flat; many localities add tax"),
    StateDefault("MA", "Massachusetts", 0.05, "5% on long-term gains; short-term gains are taxed at 8.5%"),
    StateDefault("NY", "New York", None, "progressive; NYC residents add city tax"),
    StateDefault("NJ", "New Jersey", None, "progressive"),
    StateDefault("OR", "Oregon", None, "progressive"),
    StateDefault("MN", "Minnesota", None, "progressive"),
    StateDefault("HI", "Hawaii", None, "progressive; long-term gains capped at 7.25%"),
    StateDefault("OTHER", "Other state", None, "enter your state's rate for your bracket"),
)


def state_default(code: str) -> StateDefault:
    return next((s for s in STATES if s.code == code), STATES[-1])

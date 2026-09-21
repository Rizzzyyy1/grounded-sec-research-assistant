"""Canonical metric taxonomy.

Companies pick different us-gaap tags for the same idea (Apple has reported revenue as
``SalesRevenueNet`` and later as ``RevenueFromContractWithCustomerExcludingAssessedTax``). Each
:class:`MetricSpec` lists the tags that can carry a metric **in priority order**; resolution is
per period, so a company that switched tags mid-history is still covered end to end.

Sign convention: values are exactly as reported. Cash *outflows* tagged ``PaymentsTo...`` are
positive numbers (e.g. capex), so analytics subtracts them explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Statement = Literal["income", "balance", "cashflow"]
PeriodType = Literal["duration", "instant"]

FINANCIALS = "Financials"


@dataclass(frozen=True)
class MetricSpec:
    name: str
    statement: Statement
    period_type: PeriodType
    unit: str
    tags: tuple[str, ...]
    label: str
    #: Flow metrics in USD can be summed across quarters, so Q4 = FY - 9M YTD is valid.
    #: Per-share, ratio and balance-sheet values are not additive.
    additive: bool = False
    #: Sectors where the metric does not exist (a bank has no COGS or gross profit).
    na_sectors: frozenset[str] = frozenset()
    #: Present only for some companies (e.g. redeemable NCI); its absence is never a gap.
    optional: bool = False


_BANK_NA = frozenset({FINANCIALS})


def _flow(
    name: str, label: str, statement: Statement, *tags: str, na: frozenset[str] = frozenset()
) -> MetricSpec:
    return MetricSpec(name, statement, "duration", "USD", tags, label, additive=True, na_sectors=na)


def _stock(
    name: str,
    label: str,
    *tags: str,
    na: frozenset[str] = frozenset(),
    optional: bool = False,
) -> MetricSpec:
    return MetricSpec(
        name, "balance", "instant", "USD", tags, label, na_sectors=na, optional=optional
    )


METRICS: tuple[MetricSpec, ...] = (
    # ---- income statement
    _flow(
        "revenue",
        "Revenue",
        "income",
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ),
    _flow(
        "cost_of_revenue",
        "Cost of revenue",
        "income",
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        na=_BANK_NA,
    ),
    _flow("gross_profit", "Gross profit", "income", "GrossProfit", na=_BANK_NA),
    _flow("operating_income", "Operating income", "income", "OperatingIncomeLoss", na=_BANK_NA),
    _flow(
        "pretax_income",
        "Income before taxes",
        "income",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    _flow("income_tax_expense", "Income tax expense", "income", "IncomeTaxExpenseBenefit"),
    _flow("net_income", "Net income", "income", "NetIncomeLoss", "ProfitLoss"),
    MetricSpec(
        "eps_diluted",
        "income",
        "duration",
        "USD/shares",
        ("EarningsPerShareDiluted",),
        "Diluted EPS",
    ),
    _flow(
        "interest_expense",
        "Interest expense",
        "income",
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestExpenseDebt",
    ),
    _flow(
        "research_and_development",
        "R&D expense",
        "income",
        "ResearchAndDevelopmentExpense",
        na=_BANK_NA,
    ),
    # ---- balance sheet
    _stock("total_assets", "Total assets", "Assets"),
    _stock("current_assets", "Current assets", "AssetsCurrent", na=_BANK_NA),
    _stock("total_liabilities", "Total liabilities", "Liabilities"),
    _stock("current_liabilities", "Current liabilities", "LiabilitiesCurrent", na=_BANK_NA),
    # Parent-company equity: the right denominator for ROE (the numerator is income to the parent).
    _stock(
        "shareholders_equity",
        "Shareholders' equity (parent)",
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    # Equity including non-controlling interests: what makes Assets = Liabilities + Equity hold.
    _stock(
        "total_equity",
        "Total equity (incl. NCI)",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
    ),
    # Redeemable non-controlling interests sit between liabilities and equity ("mezzanine").
    _stock(
        "mezzanine_equity",
        "Redeemable non-controlling interests",
        "RedeemableNoncontrollingInterestEquityCarryingAmount",
        "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
        optional=True,
    ),
    _stock(
        "cash_and_equivalents",
        "Cash and equivalents",
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    _stock(
        "long_term_debt",
        "Long-term debt",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ),
    _stock("inventory", "Inventory", "InventoryNet", na=_BANK_NA),
    _stock(
        "accounts_receivable",
        "Accounts receivable",
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
        na=_BANK_NA,
    ),
    _stock(
        "accounts_payable",
        "Accounts payable",
        "AccountsPayableCurrent",
        "AccountsPayableTradeCurrent",
        na=_BANK_NA,
    ),
    # ---- cash flow
    _flow(
        "operating_cash_flow",
        "Operating cash flow",
        "cashflow",
        "NetCashProvidedByUsedInOperatingActivities",
    ),
    _flow(
        "capex",
        "Capital expenditures",
        "cashflow",
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        na=_BANK_NA,  # a bank's cash-flow statement has no capex line
    ),
    _flow(
        "depreciation_amortization",
        "Depreciation & amortization",
        "cashflow",
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ),
    _flow(
        "dividends_paid",
        "Dividends paid",
        "cashflow",
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfOrdinaryDividends",
    ),
    _flow(
        "share_repurchases", "Share repurchases", "cashflow", "PaymentsForRepurchaseOfCommonStock"
    ),
)

CANONICAL_METRICS: dict[str, MetricSpec] = {m.name: m for m in METRICS}

#: Metrics we construct ourselves when the filer did not report them (always flagged derived).
DERIVED_METRICS = ("gross_profit", "total_liabilities")

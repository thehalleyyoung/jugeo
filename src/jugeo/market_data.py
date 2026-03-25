"""
market_data.py — Unified data ingestion and normalization layer for cech_model_risk.

Handles CME Group futures/options settlement prices (rates, equity, commodity),
Bloomberg BVAL evaluated pricing (cross-asset, overlapping maturities), and
QuantLib example curves and volatility surfaces. Produces standardized
MarketSnapshot objects with consistent tenor/strike grids suitable for sheaf
section construction.
"""

from __future__ import annotations

import datetime
import enum
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy import interpolate, optimize

from .sheaf_types import (
    Matrix,
    OpenCover,
    SectionData,
    SheafSection,
    StalkFiber,
    Vector,
)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class AssetClass(enum.Enum):
    """Broad asset class taxonomy used across all data sources."""

    RATES = "rates"
    EQUITY = "equity"
    COMMODITY = "commodity"
    FX = "fx"
    CREDIT = "credit"
    VOLATILITY = "volatility"


class DayCountConvention(enum.Enum):
    """Standard day-count conventions for tenor normalisation."""

    ACT_360 = "ACT/360"
    ACT_365 = "ACT/365"
    THIRTY_360 = "30/360"
    ACT_ACT = "ACT/ACT"


# ---------------------------------------------------------------------------
# Core data records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SettlementRecord:
    """A single CME futures or options settlement record.

    Attributes
    ----------
    ticker : str
        Exchange-native product code (e.g. ``"ZN H5"``).
    asset_class : AssetClass
        Broad classification of the underlying instrument.
    settlement_date : datetime.date
        The official settlement date for this record.
    maturity_date : datetime.date
        Expiration / delivery date of the contract.
    settlement_price : float
        Final settlement price as published by CME.
    strike : float | None
        Strike price; ``None`` for outright futures records.
    open_interest : int
        Open interest in contracts at settlement.
    volume : int
        Daily trade volume in contracts.
    metadata : dict[str, Any]
        Arbitrary extra fields (e.g. delivery point, option type).
    """

    ticker: str
    asset_class: AssetClass
    settlement_date: datetime.date
    maturity_date: datetime.date
    settlement_price: float
    strike: float | None = None
    open_interest: int = 0
    volume: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def time_to_maturity(self) -> float:
        """Fractional years to maturity from settlement date (ACT/365)."""
        delta = (self.maturity_date - self.settlement_date).days
        return max(delta / 365.25, 0.0)


@dataclass(frozen=True)
class BVALRecord:
    """A single Bloomberg BVAL evaluated-price record.

    Attributes
    ----------
    bbg_id : str
        Bloomberg unique identifier (FIGI or ``<TICKER> <YELLOW_KEY>``).
    asset_class : AssetClass
        Broad classification of the instrument.
    valuation_date : datetime.date
        BVAL evaluation date.
    maturity_date : datetime.date
        Instrument maturity or expiry.
    mid_price : float
        BVAL mid evaluated price.
    bid_price : float
        BVAL bid evaluated price.
    ask_price : float
        BVAL ask evaluated price.
    bval_score : float
        BVAL quality score (0–10; higher is more liquid/trusted).
    yield_value : float | None
        Yield-to-maturity in decimal (e.g. 0.045 for 4.5 %).
    spread_value : float | None
        OAS or Z-spread in basis points.
    metadata : dict[str, Any]
        Arbitrary extra fields (currency, rating, etc.).
    """

    bbg_id: str
    asset_class: AssetClass
    valuation_date: datetime.date
    maturity_date: datetime.date
    mid_price: float
    bid_price: float
    ask_price: float
    bval_score: float = 5.0
    yield_value: float | None = None
    spread_value: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def bid_ask_spread(self) -> float:
        """Bid-ask spread as a fraction of mid price."""
        if self.mid_price == 0.0:
            return float("nan")
        return (self.ask_price - self.bid_price) / abs(self.mid_price)


@dataclass
class VolSurfaceRecord:
    """A discretised volatility surface (implied vols on a strike/tenor grid).

    Attributes
    ----------
    source : str
        Data source identifier (e.g. ``"CME"``, ``"BVAL"``).
    underlying : str
        Underlying instrument ticker.
    asset_class : AssetClass
        Asset class of the underlying.
    valuation_date : datetime.date
        Date for which surface is valid.
    tenors : Vector
        Sorted array of tenors in fractional years.
    strikes : Vector
        Sorted array of strikes (absolute, not moneyness).
    implied_vols : Matrix
        ``(len(tenors), len(strikes))`` matrix of annualised implied volatilities.
    metadata : dict[str, Any]
        Arbitrary extra data.
    """

    source: str
    underlying: str
    asset_class: AssetClass
    valuation_date: datetime.date
    tenors: Vector
    strikes: Vector
    implied_vols: Matrix
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tenors = np.asarray(self.tenors, dtype=np.float64)
        self.strikes = np.asarray(self.strikes, dtype=np.float64)
        self.implied_vols = np.asarray(self.implied_vols, dtype=np.float64)
        expected_shape = (len(self.tenors), len(self.strikes))
        if self.implied_vols.shape != expected_shape:
            raise ValueError(
                f"implied_vols shape {self.implied_vols.shape} "
                f"!= expected {expected_shape}"
            )

    def interpolate_vol(self, tenor: float, strike: float) -> float:
        """Bilinear interpolation of implied vol at arbitrary (tenor, strike)."""
        rbs = interpolate.RectBivariateSpline(
            self.tenors, self.strikes, self.implied_vols, kx=1, ky=1
        )
        return float(rbs(tenor, strike))


# ---------------------------------------------------------------------------
# Grids
# ---------------------------------------------------------------------------

@dataclass
class TenorGrid:
    """Normalised tenor grid in fractional years.

    Attributes
    ----------
    tenors : Vector
        Sorted array of tenors in fractional years.
    labels : list[str]
        Human-readable labels (e.g. ``["1M","3M","6M","1Y"]``).
    day_count : DayCountConvention
        Convention used to convert calendar dates to tenors.
    """

    tenors: Vector
    labels: list[str] = field(default_factory=list)
    day_count: DayCountConvention = DayCountConvention.ACT_365

    def __post_init__(self) -> None:
        self.tenors = np.sort(np.asarray(self.tenors, dtype=np.float64))
        if not self.labels:
            self.labels = [f"{t:.4f}Y" for t in self.tenors]

    @classmethod
    def standard_rates(cls) -> "TenorGrid":
        """Return the standard CME/BVAL rates tenor grid (1M–30Y)."""
        tenors_years = np.array(
            [1/12, 2/12, 3/12, 6/12, 1, 2, 3, 5, 7, 10, 15, 20, 30],
            dtype=np.float64,
        )
        labels = ["1M","2M","3M","6M","1Y","2Y","3Y","5Y","7Y","10Y","15Y","20Y","30Y"]
        return cls(tenors=tenors_years, labels=labels)


@dataclass
class StrikeGrid:
    """Normalised strike grid for options surfaces.

    Attributes
    ----------
    strikes : Vector
        Absolute strike prices.
    moneyness : Vector
        Log-moneyness ``ln(K/F)`` relative to a forward price ``F``.
    forward : float
        Forward price used to compute moneyness.
    """

    strikes: Vector
    forward: float
    moneyness: Vector = field(init=False)

    def __post_init__(self) -> None:
        self.strikes = np.sort(np.asarray(self.strikes, dtype=np.float64))
        if self.forward <= 0:
            raise ValueError("forward must be positive")
        self.moneyness = np.log(self.strikes / self.forward)

    @classmethod
    def from_moneyness(cls, forward: float, log_moneyness: npt.ArrayLike) -> "StrikeGrid":
        """Construct grid from log-moneyness values and a forward price."""
        m = np.sort(np.asarray(log_moneyness, dtype=np.float64))
        strikes = forward * np.exp(m)
        obj = cls.__new__(cls)
        object.__setattr__(obj, "strikes", strikes) if False else None
        obj.forward = forward
        obj.strikes = strikes
        obj.moneyness = m
        return obj


@dataclass
class OverlapRegion:
    """A region where two or more market data sources share coverage.

    Used to identify where Čech intersections occur and restriction maps
    can be defined.

    Attributes
    ----------
    source_ids : tuple[str, ...]
        Identifiers of the overlapping data sources.
    asset_class : AssetClass
        Asset class of the overlap.
    tenor_range : tuple[float, float]
        (min_tenor, max_tenor) in fractional years for the overlap.
    common_dates : list[datetime.date]
        Settlement/valuation dates present in all sources.
    discrepancy : float
        Mean absolute price discrepancy across sources in the overlap.
    """

    source_ids: tuple[str, ...]
    asset_class: AssetClass
    tenor_range: tuple[float, float]
    common_dates: list[datetime.date] = field(default_factory=list)
    discrepancy: float = 0.0

    def to_open_cover_set(self) -> dict[str, Any]:
        """Serialise as a metadata dict for use in an OpenCover."""
        return {
            "sources": list(self.source_ids),
            "asset_class": self.asset_class.value,
            "tenor_min": self.tenor_range[0],
            "tenor_max": self.tenor_range[1],
            "n_dates": len(self.common_dates),
            "discrepancy": self.discrepancy,
        }


# ---------------------------------------------------------------------------
# Market Snapshot
# ---------------------------------------------------------------------------

@dataclass
class MarketSnapshot:
    """A normalised, cross-source market data snapshot for a single date.

    Aggregates settlement records, BVAL records, and QuantLib curve/surface
    data into a unified structure ready for sheaf section construction.

    Attributes
    ----------
    snapshot_date : datetime.date
        The common valuation date of all data in this snapshot.
    tenor_grid : TenorGrid
        Unified tenor grid covering all sources.
    settlements : list[SettlementRecord]
        CME settlement records included in this snapshot.
    bval_records : list[BVALRecord]
        Bloomberg BVAL records included in this snapshot.
    vol_surfaces : dict[str, VolSurfaceRecord]
        Volatility surfaces keyed by underlying ticker.
    discount_curves : dict[str, pd.Series]
        Discount factors keyed by curve name (index = tenor in years).
    overlap_regions : list[OverlapRegion]
        Detected regions of cross-source overlap.
    metadata : dict[str, Any]
        Snapshot-level metadata.
    """

    snapshot_date: datetime.date
    tenor_grid: TenorGrid
    settlements: list[SettlementRecord] = field(default_factory=list)
    bval_records: list[BVALRecord] = field(default_factory=list)
    vol_surfaces: dict[str, VolSurfaceRecord] = field(default_factory=dict)
    discount_curves: dict[str, pd.Series] = field(default_factory=dict)
    overlap_regions: list[OverlapRegion] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def settlements_df(self) -> pd.DataFrame:
        """Return settlements as a DataFrame sorted by maturity."""
        if not self.settlements:
            return pd.DataFrame()
        rows = [
            {
                "ticker": r.ticker,
                "asset_class": r.asset_class.value,
                "settlement_date": r.settlement_date,
                "maturity_date": r.maturity_date,
                "ttm": r.time_to_maturity,
                "price": r.settlement_price,
                "strike": r.strike,
                "oi": r.open_interest,
                "volume": r.volume,
            }
            for r in self.settlements
        ]
        return pd.DataFrame(rows).sort_values("ttm").reset_index(drop=True)

    def bval_df(self) -> pd.DataFrame:
        """Return BVAL records as a DataFrame sorted by maturity."""
        if not self.bval_records:
            return pd.DataFrame()
        rows = [
            {
                "bbg_id": r.bbg_id,
                "asset_class": r.asset_class.value,
                "valuation_date": r.valuation_date,
                "maturity_date": r.maturity_date,
                "ttm": (r.maturity_date - r.valuation_date).days / 365.25,
                "mid": r.mid_price,
                "bid": r.bid_price,
                "ask": r.ask_price,
                "bval_score": r.bval_score,
                "yield": r.yield_value,
                "spread_bp": r.spread_value,
            }
            for r in self.bval_records
        ]
        return pd.DataFrame(rows).sort_values("ttm").reset_index(drop=True)

    def to_stalk_fiber(self, open_set_id: int, source: str = "combined") -> StalkFiber:
        """Flatten snapshot price grid to a StalkFiber for sheaf construction.

        Parameters
        ----------
        open_set_id : int
            Index of the open set in the ambient cover.
        source : str
            Which source to include (``"settlements"``, ``"bval"``, or ``"combined"``).
        """
        parts: list[Vector] = []
        if source in ("settlements", "combined"):
            df = self.settlements_df()
            if not df.empty:
                prices = np.interp(
                    self.tenor_grid.tenors,
                    df["ttm"].to_numpy(),
                    df["price"].to_numpy(),
                )
                parts.append(prices)
        if source in ("bval", "combined"):
            df = self.bval_df()
            if not df.empty:
                mids = np.interp(
                    self.tenor_grid.tenors,
                    df["ttm"].to_numpy(),
                    df["mid"].to_numpy(),
                )
                parts.append(mids)
        data: Vector = np.concatenate(parts) if parts else np.zeros(len(self.tenor_grid.tenors))
        return StalkFiber(
            open_set_id=open_set_id,
            data=data,
            dimension=len(data),
            metadata={"snapshot_date": self.snapshot_date.isoformat(), "source": source},
        )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

class CMESettlementLoader:
    """Loader for CME Group futures and options settlement prices.

    Supports rates (Eurodollar, SOFR, Treasury), equity (E-mini S&P, Nasdaq),
    and commodity (crude, gold, corn) product codes.

    Parameters
    ----------
    asset_classes : list[AssetClass] | None
        Filter to specific asset classes; ``None`` loads all.
    min_bval_score : float
        Minimum BVAL score threshold (not used by CME; kept for API symmetry).
    """

    # Mapping from CME product group prefix to AssetClass
    _PREFIX_MAP: dict[str, AssetClass] = {
        "ZN": AssetClass.RATES,
        "ZB": AssetClass.RATES,
        "ZT": AssetClass.RATES,
        "ZF": AssetClass.RATES,
        "SR3": AssetClass.RATES,
        "GE": AssetClass.RATES,
        "ES": AssetClass.EQUITY,
        "NQ": AssetClass.EQUITY,
        "RTY": AssetClass.EQUITY,
        "CL": AssetClass.COMMODITY,
        "GC": AssetClass.COMMODITY,
        "ZC": AssetClass.COMMODITY,
        "ZW": AssetClass.COMMODITY,
    }

    def __init__(
        self,
        asset_classes: list[AssetClass] | None = None,
        min_open_interest: int = 0,
    ) -> None:
        self.asset_classes = set(asset_classes) if asset_classes else set(AssetClass)
        self.min_open_interest = min_open_interest

    def _infer_asset_class(self, ticker: str) -> AssetClass:
        """Infer asset class from ticker prefix."""
        for prefix, ac in self._PREFIX_MAP.items():
            if ticker.upper().startswith(prefix):
                return ac
        return AssetClass.RATES  # default

    def load_from_dataframe(self, df: pd.DataFrame) -> list[SettlementRecord]:
        """Parse a CME-formatted DataFrame into SettlementRecord objects.

        Expected columns: ``ticker``, ``settlement_date``, ``maturity_date``,
        ``settlement_price``, ``strike`` (optional), ``open_interest``, ``volume``.

        Parameters
        ----------
        df : pd.DataFrame
            Raw CME settlement data.

        Returns
        -------
        list[SettlementRecord]
            Filtered and validated settlement records.
        """
        records: list[SettlementRecord] = []
        required = {"ticker", "settlement_date", "maturity_date", "settlement_price"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")

        for _, row in df.iterrows():
            ac = self._infer_asset_class(str(row["ticker"]))
            if ac not in self.asset_classes:
                continue
            oi = int(row.get("open_interest", 0))
            if oi < self.min_open_interest:
                continue
            records.append(
                SettlementRecord(
                    ticker=str(row["ticker"]),
                    asset_class=ac,
                    settlement_date=pd.Timestamp(row["settlement_date"]).date(),
                    maturity_date=pd.Timestamp(row["maturity_date"]).date(),
                    settlement_price=float(row["settlement_price"]),
                    strike=float(row["strike"]) if "strike" in row and pd.notna(row["strike"]) else None,
                    open_interest=oi,
                    volume=int(row.get("volume", 0)),
                )
            )
        return records

    def generate_synthetic(
        self,
        asset_class: AssetClass = AssetClass.RATES,
        base_date: datetime.date | None = None,
        n_tenors: int = 13,
        seed: int = 42,
    ) -> list[SettlementRecord]:
        """Generate synthetic CME settlement records for testing.

        Uses a Nelson-Siegel-Svensson-inspired rate curve plus log-normal noise.

        Parameters
        ----------
        asset_class : AssetClass
            Asset class for the synthetic records.
        base_date : datetime.date | None
            Settlement date; defaults to today.
        n_tenors : int
            Number of tenor points.
        seed : int
            NumPy random seed.

        Returns
        -------
        list[SettlementRecord]
            Synthetic settlement records.
        """
        rng = np.random.default_rng(seed)
        base = base_date or datetime.date.today()
        grid = TenorGrid.standard_rates()
        tenors = grid.tenors[:n_tenors]

        # Nelson-Siegel rate curve
        beta0, beta1, beta2, tau = 0.04, -0.02, 0.01, 2.0
        x = tenors / tau
        ns_rates = (
            beta0
            + beta1 * (1 - np.exp(-x)) / x
            + beta2 * ((1 - np.exp(-x)) / x - np.exp(-x))
        )
        prices = 100 * np.exp(-ns_rates * tenors) + rng.normal(0, 0.05, len(tenors))

        prefix = {AssetClass.RATES: "ZN", AssetClass.EQUITY: "ES", AssetClass.COMMODITY: "GC"}.get(
            asset_class, "XX"
        )
        records = []
        for i, (t, p) in enumerate(zip(tenors, prices)):
            days = int(t * 365.25)
            mat = base + datetime.timedelta(days=days)
            records.append(
                SettlementRecord(
                    ticker=f"{prefix} {i+1:02d}",
                    asset_class=asset_class,
                    settlement_date=base,
                    maturity_date=mat,
                    settlement_price=float(p),
                    open_interest=int(rng.integers(1000, 50000)),
                    volume=int(rng.integers(500, 20000)),
                )
            )
        return records


class BloombergBVALLoader:
    """Loader for Bloomberg BVAL evaluated pricing data.

    Handles cross-asset evaluated prices with overlapping maturities across
    rates, credit, equity, and commodity instruments.

    Parameters
    ----------
    min_bval_score : float
        Minimum BVAL quality score for inclusion (0–10).
    asset_classes : list[AssetClass] | None
        Asset classes to include; ``None`` loads all.
    """

    def __init__(
        self,
        min_bval_score: float = 3.0,
        asset_classes: list[AssetClass] | None = None,
    ) -> None:
        self.min_bval_score = min_bval_score
        self.asset_classes = set(asset_classes) if asset_classes else set(AssetClass)

    def load_from_dataframe(self, df: pd.DataFrame) -> list[BVALRecord]:
        """Parse a BVAL-formatted DataFrame into BVALRecord objects.

        Expected columns: ``bbg_id``, ``asset_class``, ``valuation_date``,
        ``maturity_date``, ``mid_price``, ``bid_price``, ``ask_price``,
        ``bval_score``, ``yield`` (optional), ``spread_bp`` (optional).

        Parameters
        ----------
        df : pd.DataFrame
            Raw Bloomberg BVAL data.

        Returns
        -------
        list[BVALRecord]
            Filtered and validated BVAL records.
        """
        required = {"bbg_id", "valuation_date", "maturity_date", "mid_price", "bid_price", "ask_price"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")

        records: list[BVALRecord] = []
        for _, row in df.iterrows():
            score = float(row.get("bval_score", 5.0))
            if score < self.min_bval_score:
                continue
            ac_raw = str(row.get("asset_class", "rates")).lower()
            try:
                ac = AssetClass(ac_raw)
            except ValueError:
                ac = AssetClass.RATES
            if ac not in self.asset_classes:
                continue
            records.append(
                BVALRecord(
                    bbg_id=str(row["bbg_id"]),
                    asset_class=ac,
                    valuation_date=pd.Timestamp(row["valuation_date"]).date(),
                    maturity_date=pd.Timestamp(row["maturity_date"]).date(),
                    mid_price=float(row["mid_price"]),
                    bid_price=float(row["bid_price"]),
                    ask_price=float(row["ask_price"]),
                    bval_score=score,
                    yield_value=float(row["yield"]) if "yield" in row and pd.notna(row.get("yield")) else None,
                    spread_value=float(row["spread_bp"]) if "spread_bp" in row and pd.notna(row.get("spread_bp")) else None,
                )
            )
        return records

    def generate_synthetic(
        self,
        asset_class: AssetClass = AssetClass.RATES,
        base_date: datetime.date | None = None,
        n_records: int = 15,
        seed: int = 7,
    ) -> list[BVALRecord]:
        """Generate synthetic BVAL records for testing.

        Parameters
        ----------
        asset_class : AssetClass
            Asset class for the synthetic records.
        base_date : datetime.date | None
            Valuation date; defaults to today.
        n_records : int
            Number of records to generate.
        seed : int
            NumPy random seed.

        Returns
        -------
        list[BVALRecord]
            Synthetic BVAL records.
        """
        rng = np.random.default_rng(seed)
        base = base_date or datetime.date.today()
        tenors = np.linspace(0.25, 30, n_records)
        yields = 0.035 + 0.015 * (1 - np.exp(-tenors / 5)) + rng.normal(0, 0.002, n_records)
        prices = 100 * np.exp(-yields * tenors)

        records = []
        for i, (t, p, y) in enumerate(zip(tenors, prices, yields)):
            spread = float(rng.uniform(50, 300))
            score = float(rng.uniform(self.min_bval_score, 10.0))
            mat = base + datetime.timedelta(days=int(t * 365.25))
            mid = float(p)
            half_spread = abs(mid) * 0.001
            records.append(
                BVALRecord(
                    bbg_id=f"BVAL_{asset_class.value.upper()}_{i+1:03d}",
                    asset_class=asset_class,
                    valuation_date=base,
                    maturity_date=mat,
                    mid_price=mid,
                    bid_price=mid - half_spread,
                    ask_price=mid + half_spread,
                    bval_score=score,
                    yield_value=float(y),
                    spread_value=spread,
                )
            )
        return records


class QuantLibCurveLoader:
    """Loader for QuantLib example discount curves and volatility surfaces.

    Bootstraps piecewise-flat or cubic-spline interpolated curves from
    instrument quotes and returns ``pd.Series`` discount factor objects
    keyed by tenor.

    Parameters
    ----------
    interpolation : str
        ``"linear"`` (log-linear on discount factors) or ``"cubic"`` (cubic spline on zero rates).
    """

    def __init__(self, interpolation: str = "linear") -> None:
        if interpolation not in ("linear", "cubic"):
            raise ValueError("interpolation must be 'linear' or 'cubic'")
        self.interpolation = interpolation

    def bootstrap_curve(
        self,
        tenors: npt.ArrayLike,
        par_rates: npt.ArrayLike,
        day_count: DayCountConvention = DayCountConvention.ACT_365,
    ) -> pd.Series:
        """Bootstrap a discount curve from par rates via iterative stripping.

        Parameters
        ----------
        tenors : array-like
            Tenors in fractional years (must be sorted ascending).
        par_rates : array-like
            Par rates (semi-annual, decimal) for each tenor.
        day_count : DayCountConvention
            Day count convention used for accrual.

        Returns
        -------
        pd.Series
            Discount factors indexed by tenor (fractional years).
        """
        t = np.asarray(tenors, dtype=np.float64)
        r = np.asarray(par_rates, dtype=np.float64)
        if t.shape != r.shape:
            raise ValueError("tenors and par_rates must have the same length")

        disc = np.ones(len(t))
        for i, (ti, ri) in enumerate(zip(t, r)):
            # Simple bootstrap: D(T) = 1 / (1 + r * T) for short end
            coupon_pv = sum(
                disc[j] * ri * (t[j] - (t[j - 1] if j > 0 else 0.0))
                for j in range(i)
            )
            disc[i] = (1 - coupon_pv) / (1 + ri * (ti - (t[i - 1] if i > 0 else 0.0)))

        if self.interpolation == "cubic":
            cs = interpolate.CubicSpline(t, np.log(disc))
            dense = np.linspace(t[0], t[-1], max(len(t) * 10, 100))
            disc_dense = np.exp(cs(dense))
            return pd.Series(disc_dense, index=dense, name="discount_factor")

        return pd.Series(disc, index=t, name="discount_factor")

    def build_vol_surface(
        self,
        tenors: npt.ArrayLike,
        strikes: npt.ArrayLike,
        base_vol: float = 0.20,
        skew: float = -0.05,
        seed: int = 99,
    ) -> VolSurfaceRecord:
        """Construct a synthetic QuantLib-style vol surface with smile.

        Uses a simplified SABR-inspired parametrisation: ATM vol decays
        with tenor and the smile is quadratic in log-moneyness.

        Parameters
        ----------
        tenors : array-like
            Tenors in fractional years.
        strikes : array-like
            Absolute strike prices.
        base_vol : float
            ATM volatility at the short end.
        skew : float
            Linear skew coefficient (negative = put skew).
        seed : int
            NumPy random seed for noise.

        Returns
        -------
        VolSurfaceRecord
            Populated volatility surface.
        """
        rng = np.random.default_rng(seed)
        t = np.sort(np.asarray(tenors, dtype=np.float64))
        k = np.sort(np.asarray(strikes, dtype=np.float64))
        forward = np.median(k)

        atm_vols = base_vol * np.exp(-0.05 * t)  # vol term structure decay
        log_m = np.log(k / forward)

        vols = np.zeros((len(t), len(k)))
        for i, (ti, atm) in enumerate(zip(t, atm_vols)):
            convexity = 0.10 / np.sqrt(ti + 0.01)
            smile = atm + skew * log_m + convexity * log_m ** 2
            vols[i] = np.clip(smile + rng.normal(0, 0.002, len(k)), 0.001, 2.0)

        return VolSurfaceRecord(
            source="QuantLib",
            underlying="SYNTHETIC",
            asset_class=AssetClass.EQUITY,
            valuation_date=datetime.date.today(),
            tenors=t,
            strikes=k,
            implied_vols=vols,
        )


class CurveBootstrapper:
    """Wraps QuantLibCurveLoader with cross-asset bootstrapping utilities.

    Bootstraps discount curves for each asset class and stores them in
    a unified ``dict[str, pd.Series]`` for inclusion in a MarketSnapshot.

    Parameters
    ----------
    loader : QuantLibCurveLoader
        Underlying loader instance.
    """

    def __init__(self, loader: QuantLibCurveLoader | None = None) -> None:
        self.loader = loader or QuantLibCurveLoader()

    def bootstrap_all(
        self,
        settlements: list[SettlementRecord],
        bval_records: list[BVALRecord],
        tenor_grid: TenorGrid,
    ) -> dict[str, pd.Series]:
        """Bootstrap discount curves from combined settlement and BVAL data.

        Parameters
        ----------
        settlements : list[SettlementRecord]
            CME settlement records (used for short-end anchoring).
        bval_records : list[BVALRecord]
            BVAL records (used for long-end completion).
        tenor_grid : TenorGrid
            Target tenor grid for the output curves.

        Returns
        -------
        dict[str, pd.Series]
            Discount curves keyed by ``"<asset_class>_<source>"`` strings.
        """
        curves: dict[str, pd.Series] = {}

        for ac in AssetClass:
            cme_recs = [r for r in settlements if r.asset_class == ac]
            bval_recs = [r for r in bval_records if r.asset_class == ac]

            if not cme_recs and not bval_recs:
                continue

            tenors: list[float] = []
            rates: list[float] = []
            for r in sorted(cme_recs, key=lambda x: x.time_to_maturity):
                if r.time_to_maturity > 0:
                    tenors.append(r.time_to_maturity)
                    rates.append(max((-np.log(r.settlement_price / 100)) / r.time_to_maturity, 1e-6))

            for r in sorted(bval_recs, key=lambda x: (x.maturity_date - x.valuation_date).days):
                t = (r.maturity_date - r.valuation_date).days / 365.25
                if t > 0 and r.yield_value is not None:
                    tenors.append(t)
                    rates.append(r.yield_value)

            if len(tenors) >= 2:
                t_arr = np.array(tenors)
                r_arr = np.array(rates)
                order = np.argsort(t_arr)
                try:
                    curve = self.loader.bootstrap_curve(t_arr[order], r_arr[order])
                    curves[ac.value] = curve
                except Exception as exc:
                    warnings.warn(f"Bootstrapping failed for {ac.value}: {exc}")

        return curves


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def normalize_day_count(
    start: datetime.date,
    end: datetime.date,
    convention: DayCountConvention = DayCountConvention.ACT_365,
) -> float:
    """Convert a date range to a fractional-year tenor.

    Parameters
    ----------
    start : datetime.date
        Start date.
    end : datetime.date
        End date.
    convention : DayCountConvention
        Day-count convention to apply.

    Returns
    -------
    float
        Tenor in fractional years (non-negative).
    """
    delta_days = max((end - start).days, 0)
    if convention == DayCountConvention.ACT_360:
        return delta_days / 360.0
    if convention == DayCountConvention.ACT_365:
        return delta_days / 365.0
    if convention == DayCountConvention.ACT_ACT:
        return delta_days / 365.25
    if convention == DayCountConvention.THIRTY_360:
        y1, m1, d1 = start.year, start.month, min(start.day, 30)
        y2, m2, d2 = end.year, end.month, min(end.day, 30)
        days_30_360 = 360 * (y2 - y1) + 30 * (m2 - m1) + (d2 - d1)
        return days_30_360 / 360.0
    return delta_days / 365.0


def interpolate_missing_tenors(
    tenors: npt.ArrayLike,
    values: npt.ArrayLike,
    target_tenors: npt.ArrayLike,
    method: str = "linear",
) -> Vector:
    """Interpolate (and extrapolate flat) values onto a target tenor grid.

    Parameters
    ----------
    tenors : array-like
        Known tenor points in fractional years.
    values : array-like
        Observed values at ``tenors``.
    target_tenors : array-like
        Desired output tenor grid.
    method : str
        Interpolation method: ``"linear"``, ``"cubic"``, or ``"monotone"``.

    Returns
    -------
    Vector
        Interpolated values at each target tenor.
    """
    t = np.asarray(tenors, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    tgt = np.asarray(target_tenors, dtype=np.float64)

    valid = np.isfinite(v)
    t, v = t[valid], v[valid]
    if len(t) == 0:
        return np.full(len(tgt), np.nan)
    if len(t) == 1:
        return np.full(len(tgt), v[0])

    order = np.argsort(t)
    t, v = t[order], v[order]

    if method == "cubic":
        f = interpolate.CubicSpline(t, v, extrapolate=True)
    elif method == "monotone":
        f = interpolate.PchipInterpolator(t, v, extrapolate=True)
    else:
        f = interpolate.interp1d(t, v, kind="linear", bounds_error=False, fill_value=(v[0], v[-1]))

    return np.asarray(f(tgt), dtype=np.float64)


def align_settlement_dates(
    settlements: list[SettlementRecord],
    bval_records: list[BVALRecord],
) -> tuple[list[SettlementRecord], list[BVALRecord]]:
    """Align settlement and BVAL records to their common valuation dates.

    Drops records whose date is not present in both source sets.

    Parameters
    ----------
    settlements : list[SettlementRecord]
    bval_records : list[BVALRecord]

    Returns
    -------
    tuple[list[SettlementRecord], list[BVALRecord]]
        Records restricted to the intersection of dates.
    """
    cme_dates = {r.settlement_date for r in settlements}
    bval_dates = {r.valuation_date for r in bval_records}
    common = cme_dates & bval_dates
    if not common:
        warnings.warn("No common dates between CME and BVAL records; returning all records.")
        return settlements, bval_records
    aligned_s = [r for r in settlements if r.settlement_date in common]
    aligned_b = [r for r in bval_records if r.valuation_date in common]
    return aligned_s, aligned_b


def detect_overlapping_maturities(
    settlements: list[SettlementRecord],
    bval_records: list[BVALRecord],
    tenor_tol: float = 0.083,
) -> list[OverlapRegion]:
    """Detect maturity overlaps between CME and BVAL data sources.

    Two instruments overlap if their time-to-maturity values are within
    ``tenor_tol`` fractional years (default ≈ 1 month).

    Parameters
    ----------
    settlements : list[SettlementRecord]
    bval_records : list[BVALRecord]
    tenor_tol : float
        Tenor proximity threshold (fractional years).

    Returns
    -------
    list[OverlapRegion]
        Regions of overlap grouped by asset class.
    """
    regions: list[OverlapRegion] = []

    for ac in AssetClass:
        cme = [(r.time_to_maturity, r.settlement_price) for r in settlements if r.asset_class == ac]
        bval = [
            ((r.maturity_date - r.valuation_date).days / 365.25, r.mid_price)
            for r in bval_records if r.asset_class == ac
        ]
        if not cme or not bval:
            continue

        cme_t = np.array([x[0] for x in cme])
        bval_t = np.array([x[0] for x in bval])

        overlap_tenors: list[tuple[float, float, float]] = []
        for ct, cp in cme:
            dists = np.abs(bval_t - ct)
            close_idx = np.where(dists <= tenor_tol)[0]
            for idx in close_idx:
                bt, bp = bval[idx]
                overlap_tenors.append((ct, bt, abs(cp - bp)))

        if overlap_tenors:
            all_tenors = [min(a, b) for a, b, _ in overlap_tenors]
            discrepancies = [d for _, _, d in overlap_tenors]
            regions.append(
                OverlapRegion(
                    source_ids=("CME", "BVAL"),
                    asset_class=ac,
                    tenor_range=(min(all_tenors), max(all_tenors)),
                    discrepancy=float(np.mean(discrepancies)),
                )
            )

    return regions


def build_cross_asset_grid(
    settlements: list[SettlementRecord],
    bval_records: list[BVALRecord],
    asset_classes: list[AssetClass] | None = None,
) -> TenorGrid:
    """Build a unified cross-asset tenor grid from all available instruments.

    Merges and de-duplicates tenor points from CME and BVAL data, then
    adds standard pillar points for completeness.

    Parameters
    ----------
    settlements : list[SettlementRecord]
    bval_records : list[BVALRecord]
    asset_classes : list[AssetClass] | None
        Asset classes to include; ``None`` includes all.

    Returns
    -------
    TenorGrid
        Unified, sorted tenor grid.
    """
    acs = set(asset_classes) if asset_classes else set(AssetClass)

    raw_tenors: list[float] = []
    for r in settlements:
        if r.asset_class in acs and r.time_to_maturity > 0:
            raw_tenors.append(r.time_to_maturity)
    for r in bval_records:
        if r.asset_class in acs:
            t = (r.maturity_date - r.valuation_date).days / 365.25
            if t > 0:
                raw_tenors.append(t)

    # Standard pillar points
    standard = [1/12, 2/12, 3/12, 6/12, 1, 2, 3, 5, 7, 10, 15, 20, 30]
    all_tenors = np.unique(np.concatenate([np.array(raw_tenors), np.array(standard)]))

    # Collapse near-duplicates within 5 days (5/365.25)
    deduped: list[float] = [all_tenors[0]]
    for t in all_tenors[1:]:
        if t - deduped[-1] > 5 / 365.25:
            deduped.append(t)

    tenors = np.array(deduped, dtype=np.float64)
    return TenorGrid(tenors=tenors)


def compute_implied_forwards(
    discount_curve: pd.Series,
    tenors: npt.ArrayLike | None = None,
) -> pd.Series:
    """Compute discrete implied forward rates from a discount curve.

    Parameters
    ----------
    discount_curve : pd.Series
        Discount factors indexed by tenor in fractional years.
    tenors : array-like | None
        Target tenors; defaults to the index of ``discount_curve``.

    Returns
    -------
    pd.Series
        Annualised implied forward rates indexed by forward-start tenor.
    """
    t = np.asarray(discount_curve.index, dtype=np.float64)
    d = np.asarray(discount_curve.values, dtype=np.float64)

    if tenors is not None:
        tgt = np.asarray(tenors, dtype=np.float64)
        d = np.interp(tgt, t, d)
        t = tgt

    fwd_rates = np.empty(len(t) - 1)
    for i in range(len(t) - 1):
        dt = t[i + 1] - t[i]
        if dt > 0 and d[i] > 0 and d[i + 1] > 0:
            fwd_rates[i] = (d[i] / d[i + 1] - 1) / dt
        else:
            fwd_rates[i] = float("nan")

    return pd.Series(fwd_rates, index=t[:-1], name="implied_forward_rate")


def merge_market_sources(
    settlements: list[SettlementRecord],
    bval_records: list[BVALRecord],
    tenor_grid: TenorGrid,
    weight_cme: float = 0.5,
    weight_bval: float = 0.5,
) -> pd.DataFrame:
    """Blend CME settlement prices and BVAL mid prices onto a unified grid.

    Where both sources have coverage, prices are blended by source weights.
    Where only one source covers a tenor, that source is used directly.

    Parameters
    ----------
    settlements : list[SettlementRecord]
    bval_records : list[BVALRecord]
    tenor_grid : TenorGrid
        Output tenor grid.
    weight_cme : float
        Blending weight for CME prices (must sum to 1 with weight_bval).
    weight_bval : float
        Blending weight for BVAL prices.

    Returns
    -------
    pd.DataFrame
        Columns: ``tenor``, ``cme_price``, ``bval_price``, ``blended_price``,
        ``source_coverage``.
    """
    if abs(weight_cme + weight_bval - 1.0) > 1e-9:
        raise ValueError("weight_cme + weight_bval must equal 1.0")

    tgt = tenor_grid.tenors

    # --- CME ---
    cme_t = np.array([r.time_to_maturity for r in settlements if r.time_to_maturity > 0])
    cme_p = np.array([r.settlement_price for r in settlements if r.time_to_maturity > 0])
    cme_interp = (
        interpolate_missing_tenors(cme_t, cme_p, tgt)
        if len(cme_t) >= 2
        else np.full(len(tgt), np.nan)
    )

    # --- BVAL ---
    bval_t = np.array(
        [(r.maturity_date - r.valuation_date).days / 365.25 for r in bval_records]
    )
    bval_p = np.array([r.mid_price for r in bval_records])
    bval_interp = (
        interpolate_missing_tenors(bval_t, bval_p, tgt)
        if len(bval_t) >= 2
        else np.full(len(tgt), np.nan)
    )

    # --- Blend ---
    cme_ok = np.isfinite(cme_interp)
    bval_ok = np.isfinite(bval_interp)
    blended = np.full(len(tgt), np.nan)
    coverage = np.full(len(tgt), "none", dtype=object)

    both = cme_ok & bval_ok
    blended[both] = weight_cme * cme_interp[both] + weight_bval * bval_interp[both]
    coverage[both] = "both"

    cme_only = cme_ok & ~bval_ok
    blended[cme_only] = cme_interp[cme_only]
    coverage[cme_only] = "cme"

    bval_only = ~cme_ok & bval_ok
    blended[bval_only] = bval_interp[bval_only]
    coverage[bval_only] = "bval"

    return pd.DataFrame(
        {
            "tenor": tgt,
            "cme_price": cme_interp,
            "bval_price": bval_interp,
            "blended_price": blended,
            "source_coverage": coverage,
        }
    )


# ---------------------------------------------------------------------------
# Top-level loader functions
# ---------------------------------------------------------------------------

def load_cme_settlements(
    df: pd.DataFrame | None = None,
    asset_classes: list[AssetClass] | None = None,
    base_date: datetime.date | None = None,
    synthetic: bool = False,
    seed: int = 42,
) -> list[SettlementRecord]:
    """Load CME Group settlement records from a DataFrame or generate synthetic data.

    Parameters
    ----------
    df : pd.DataFrame | None
        Raw CME data; if ``None`` and ``synthetic=True``, generates example data.
    asset_classes : list[AssetClass] | None
        Asset classes to include.
    base_date : datetime.date | None
        Settlement date for synthetic data.
    synthetic : bool
        If ``True`` and ``df`` is ``None``, generate synthetic records.
    seed : int
        Random seed for synthetic generation.

    Returns
    -------
    list[SettlementRecord]
        Loaded or synthetic settlement records.
    """
    loader = CMESettlementLoader(asset_classes=asset_classes)
    if df is not None:
        return loader.load_from_dataframe(df)
    if synthetic:
        records: list[SettlementRecord] = []
        for ac in (asset_classes or list(AssetClass)):
            records.extend(loader.generate_synthetic(asset_class=ac, base_date=base_date, seed=seed))
        return records
    raise ValueError("Either df or synthetic=True must be provided.")


def load_bval_prices(
    df: pd.DataFrame | None = None,
    asset_classes: list[AssetClass] | None = None,
    min_bval_score: float = 3.0,
    base_date: datetime.date | None = None,
    synthetic: bool = False,
    seed: int = 7,
) -> list[BVALRecord]:
    """Load Bloomberg BVAL evaluated prices from a DataFrame or generate synthetic data.

    Parameters
    ----------
    df : pd.DataFrame | None
        Raw BVAL data; if ``None`` and ``synthetic=True``, generates example data.
    asset_classes : list[AssetClass] | None
        Asset classes to include.
    min_bval_score : float
        Minimum BVAL quality score threshold.
    base_date : datetime.date | None
        Valuation date for synthetic data.
    synthetic : bool
        If ``True`` and ``df`` is ``None``, generate synthetic records.
    seed : int
        Random seed for synthetic generation.

    Returns
    -------
    list[BVALRecord]
        Loaded or synthetic BVAL records.
    """
    loader = BloombergBVALLoader(min_bval_score=min_bval_score, asset_classes=asset_classes)
    if df is not None:
        return loader.load_from_dataframe(df)
    if synthetic:
        records: list[BVALRecord] = []
        for ac in (asset_classes or list(AssetClass)):
            records.extend(loader.generate_synthetic(asset_class=ac, base_date=base_date, seed=seed))
        return records
    raise ValueError("Either df or synthetic=True must be provided.")


def load_quantlib_curves(
    settlements: list[SettlementRecord],
    bval_records: list[BVALRecord],
    tenor_grid: TenorGrid | None = None,
    interpolation: str = "linear",
) -> dict[str, pd.Series]:
    """Bootstrap QuantLib-compatible discount curves from market data.

    Parameters
    ----------
    settlements : list[SettlementRecord]
        CME settlement records for short-end anchoring.
    bval_records : list[BVALRecord]
        BVAL records for long-end completion.
    tenor_grid : TenorGrid | None
        Output tenor grid; defaults to ``TenorGrid.standard_rates()``.
    interpolation : str
        Interpolation method for the QuantLibCurveLoader.

    Returns
    -------
    dict[str, pd.Series]
        Bootstrapped discount curves keyed by asset class name.
    """
    grid = tenor_grid or TenorGrid.standard_rates()
    ql_loader = QuantLibCurveLoader(interpolation=interpolation)
    bootstrapper = CurveBootstrapper(loader=ql_loader)
    return bootstrapper.bootstrap_all(settlements, bval_records, grid)


def build_market_snapshot(
    settlements: list[SettlementRecord],
    bval_records: list[BVALRecord],
    snapshot_date: datetime.date | None = None,
    tenor_grid: TenorGrid | None = None,
    include_vol_surfaces: bool = False,
    include_curves: bool = True,
) -> MarketSnapshot:
    """Assemble a complete MarketSnapshot from CME and BVAL data.

    Parameters
    ----------
    settlements : list[SettlementRecord]
    bval_records : list[BVALRecord]
    snapshot_date : datetime.date | None
        Common valuation date; inferred from data if ``None``.
    tenor_grid : TenorGrid | None
        Output tenor grid; built from data if ``None``.
    include_vol_surfaces : bool
        If ``True``, build synthetic vol surfaces for equity instruments.
    include_curves : bool
        If ``True``, bootstrap discount curves.

    Returns
    -------
    MarketSnapshot
        Fully assembled market snapshot.
    """
    date = snapshot_date or (
        settlements[0].settlement_date if settlements
        else bval_records[0].valuation_date if bval_records
        else datetime.date.today()
    )
    grid = tenor_grid or build_cross_asset_grid(settlements, bval_records)
    overlaps = detect_overlapping_maturities(settlements, bval_records)
    curves: dict[str, pd.Series] = {}
    if include_curves:
        curves = load_quantlib_curves(settlements, bval_records, grid)

    vol_surfaces: dict[str, VolSurfaceRecord] = {}
    if include_vol_surfaces:
        eq_recs = [r for r in settlements if r.asset_class == AssetClass.EQUITY]
        if eq_recs:
            prices = np.array([r.settlement_price for r in eq_recs])
            fwd = float(np.median(prices))
            strikes = np.linspace(fwd * 0.7, fwd * 1.3, 15)
            tenors = np.linspace(0.25, 2.0, 8)
            ql_loader = QuantLibCurveLoader()
            vs = ql_loader.build_vol_surface(tenors, strikes)
            vol_surfaces["EQUITY_SYNTHETIC"] = vs

    return MarketSnapshot(
        snapshot_date=date,
        tenor_grid=grid,
        settlements=settlements,
        bval_records=bval_records,
        vol_surfaces=vol_surfaces,
        discount_curves=curves,
        overlap_regions=overlaps,
        metadata={"n_cme": len(settlements), "n_bval": len(bval_records)},
    )

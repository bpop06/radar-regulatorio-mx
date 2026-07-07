from app.sources.anam import AnamCollector
from app.sources.diputados import DiputadosCollector
from app.sources.dof import DofCollector
from app.sources.gobmx import GobMxCollector
from app.sources.icsid import IcsidCollector
from app.sources.impi import ImpiCollector
from app.sources.international import (
    CijCollector,
    CpiCollector,
    OmcCollector,
    OnuNoticiasCollector,
    TradeGovCollector,
    UstrCollector,
)
from app.sources.platiica import PlatiicaCollector
from app.sources.senado import SenadoCollector
from app.sources.snice import SniceCollector
from app.sources.tfja import TfjaCollector
from app.sources.tmec import TmecCollector
from app.sources.worldbank import WorldBankCollector

__all__ = [
    "AnamCollector",
    "OmcCollector",
    "TfjaCollector",
    "TmecCollector",
    "CijCollector",
    "CpiCollector",
    "DiputadosCollector",
    "DofCollector",
    "GobMxCollector",
    "IcsidCollector",
    "ImpiCollector",
    "OnuNoticiasCollector",
    "PlatiicaCollector",
    "SenadoCollector",
    "SniceCollector",
    "TradeGovCollector",
    "UstrCollector",
    "WorldBankCollector",
]

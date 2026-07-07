from app.sources.diputados import DiputadosCollector
from app.sources.dof import DofCollector
from app.sources.gobmx import GobMxCollector
from app.sources.icsid import IcsidCollector
from app.sources.impi import ImpiCollector
from app.sources.international import (
    CijCollector,
    CpiCollector,
    OnuNoticiasCollector,
    TradeGovCollector,
    UstrCollector,
)
from app.sources.platiica import PlatiicaCollector
from app.sources.senado import SenadoCollector
from app.sources.snice import SniceCollector
from app.sources.worldbank import WorldBankCollector

__all__ = [
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

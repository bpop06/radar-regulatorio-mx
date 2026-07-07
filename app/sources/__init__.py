from app.sources.diputados import DiputadosCollector
from app.sources.dof import DofCollector
from app.sources.gobmx import GobMxCollector
from app.sources.impi import ImpiCollector
from app.sources.international import (
    OnuNoticiasCollector,
    TradeGovCollector,
    UstrCollector,
)
from app.sources.platiica import PlatiicaCollector
from app.sources.senado import SenadoCollector
from app.sources.snice import SniceCollector

__all__ = [
    "DiputadosCollector",
    "DofCollector",
    "GobMxCollector",
    "ImpiCollector",
    "OnuNoticiasCollector",
    "PlatiicaCollector",
    "SenadoCollector",
    "SniceCollector",
    "TradeGovCollector",
    "UstrCollector",
]

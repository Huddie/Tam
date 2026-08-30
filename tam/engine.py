"""tam.Engine -- which DataFrame library a query result comes back as, for
every `engine=` parameter across `tam.Symbol`/`tam.query()`. A plain string
("pandas"/"polars") still works everywhere `engine=` is accepted (this is a
str-Enum specifically so `engine == "polars"` and passing a bare string
both keep working unchanged) -- `Engine.PANDAS`/`Engine.POLARS` just gives
autocomplete/typo-safety instead of having to remember or guess the two
valid spellings.

    from tam import Symbol, Engine

    Symbol("AAPL").splits(engine=Engine.POLARS)   # same as engine="polars"
"""

from __future__ import annotations

from enum import Enum


class Engine(str, Enum):
    PANDAS = "pandas"
    POLARS = "polars"

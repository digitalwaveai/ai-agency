from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Lead:
    name: str
    source_url: str
    website: str = ""
    phone: str = ""
    address: str = ""
    snippet: str = ""
    query: str = ""
    score: int = 0
    id: int | None = None
    status: str = "new"

    @property
    def contact(self) -> str:
        return self.phone or self.website or "публичный контакт не найден"

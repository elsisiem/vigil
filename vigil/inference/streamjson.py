"""Incremental parser that emits array items while a JSON document is still streaming.

Given a document shaped like {"components": [ {...}, {...} ]}, each element object is yielded
the moment its closing brace arrives, which is what lets a component render while the model is
still producing the next one.
"""

from __future__ import annotations

import json
from typing import Any


class ComponentStreamParser:
    def __init__(self, item_depth: int = 3):
        self.buf: list[str] = []
        self.depth = 0
        self.in_str = False
        self.esc = False
        self.item_start: int | None = None
        self.item_depth = item_depth
        self.pos = 0
        self.items: list[dict[str, Any]] = []

    def feed(self, chunk: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ch in chunk:
            self.buf.append(ch)
            i = self.pos
            self.pos += 1
            if self.in_str:
                if self.esc:
                    self.esc = False
                elif ch == "\\":
                    self.esc = True
                elif ch == '"':
                    self.in_str = False
                continue
            if ch == '"':
                self.in_str = True
            elif ch in "{[":
                self.depth += 1
                if self.depth == self.item_depth and ch == "{" and self.item_start is None:
                    self.item_start = i
            elif ch in "}]":
                if self.depth == self.item_depth and ch == "}" and self.item_start is not None:
                    text = "".join(self.buf[self.item_start : i + 1])
                    self.item_start = None
                    try:
                        obj = json.loads(text)
                        self.items.append(obj)
                        out.append(obj)
                    except json.JSONDecodeError:
                        pass
                self.depth -= 1
        return out

    def text(self) -> str:
        return "".join(self.buf)

    def final(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.text())
        except json.JSONDecodeError:
            return None

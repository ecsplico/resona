"""Composable postprocessing pipeline — chain of str → str transformations."""

from __future__ import annotations

from typing import Callable

PostprocessStep = Callable[[str], str]


class PostprocessPipeline:
    """Ordered chain of text postprocessing steps."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, PostprocessStep]] = []

    def add(self, name: str, step: PostprocessStep) -> PostprocessPipeline:
        """Append a named step. Returns self for fluent chaining."""
        self._steps.append((name, step))
        return self

    def run(self, text: str) -> str:
        """Run all steps in order, returning the final text."""
        for _name, step in self._steps:
            text = step(text)
        return text

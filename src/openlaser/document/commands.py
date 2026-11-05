"""Patrón Command para Deshacer/Rehacer, independiente de la interfaz."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable


class Command(ABC):
    text: str = ""

    @abstractmethod
    def do(self) -> None: ...

    @abstractmethod
    def undo(self) -> None: ...

    def merge_with(self, other: "Command") -> bool:
        """Permite fusionar ediciones consecutivas del mismo campo."""
        return False


class SetAttribute(Command):
    def __init__(self, target: Any, attr: str, value: Any, text: str = "") -> None:
        self.target, self.attr, self.value = target, attr, value
        self.old = getattr(target, attr)
        self.text = text or attr

    def do(self) -> None:
        setattr(self.target, self.attr, self.value)

    def undo(self) -> None:
        setattr(self.target, self.attr, self.old)

    def merge_with(self, other: Command) -> bool:
        if isinstance(other, SetAttribute) and other.target is self.target and other.attr == self.attr:
            self.value = other.value
            return True
        return False


class UndoStack:
    def __init__(self, limit: int = 0) -> None:
        """``limit`` = 0 significa ilimitado."""
        self.limit = limit
        self._done: list[Command] = []
        self._undone: list[Command] = []
        self.listeners: list[Callable[[], None]] = []

    def push(self, cmd: Command, merge: bool = False) -> None:
        cmd.do()
        if merge and self._done and self._done[-1].merge_with(cmd):
            pass
        else:
            self._done.append(cmd)
            if self.limit and len(self._done) > self.limit:
                self._done.pop(0)
        self._undone.clear()
        self._notify()

    def can_undo(self) -> bool:
        return bool(self._done)

    def can_redo(self) -> bool:
        return bool(self._undone)

    def undo(self) -> None:
        if self._done:
            cmd = self._done.pop()
            cmd.undo()
            self._undone.append(cmd)
            self._notify()

    def redo(self) -> None:
        if self._undone:
            cmd = self._undone.pop()
            cmd.do()
            self._done.append(cmd)
            self._notify()

    def clear(self) -> None:
        self._done.clear()
        self._undone.clear()
        self._notify()

    # --- historial ------------------------------------------------------------
    def history(self) -> tuple[list[str], int]:
        """Textos de todos los pasos (hechos y deshechos) y cuántos están aplicados."""
        done = [getattr(c, "text", "") or type(c).__name__ for c in self._done]
        undone = [getattr(c, "text", "") or type(c).__name__ for c in reversed(self._undone)]
        return done + undone, len(done)

    def go_to(self, applied: int) -> None:
        """Deshace o rehace hasta dejar ``applied`` pasos aplicados (una sola notificación)."""
        listeners, self.listeners = self.listeners, []
        try:
            while len(self._done) > applied and self._done:
                self.undo()
            while len(self._done) < applied and self._undone:
                self.redo()
        finally:
            self.listeners = listeners
        self._notify()

    def _notify(self) -> None:
        for fn in list(self.listeners):
            fn()


class DocumentEdit(Command):
    """Cambio estructural del documento guardado como dos instantáneas baratas.

    Las formas son inmutables, así que basta con guardar las listas de
    referencias antes y después. Las capas se copian superficialmente porque
    sus atributos sí son mutables.
    """

    def __init__(self, doc: object, before: tuple[list, list], after: tuple[list, list], text: str) -> None:
        self.doc = doc
        self.before = before
        self.after = after
        self.text = text

    @staticmethod
    def snapshot(doc: object) -> tuple[list, list]:
        import copy
        return (list(doc.shapes), [copy.copy(ly) for ly in doc.layers])  # type: ignore[attr-defined]

    def _apply(self, state: tuple[list, list]) -> None:
        import copy
        self.doc.shapes = list(state[0])  # type: ignore[attr-defined]
        # Conserva la identidad de los objetos Layer existentes (otros comandos y la
        # interfaz guardan referencias a ellos): sólo se restauran sus atributos.
        existing = {ly.id: ly for ly in self.doc.layers}  # type: ignore[attr-defined]
        layers = []
        for saved in state[1]:
            target = existing.get(saved.id)
            if target is None:
                target = copy.copy(saved)
            else:
                target.__dict__.update(copy.copy(saved).__dict__)
            layers.append(target)
        self.doc.layers = layers  # type: ignore[attr-defined]

    def do(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)


class edit_document:  # noqa: N801 - se usa como gestor de contexto: ``with edit_document(...)``
    """Agrupa cualquier modificación de ``doc.shapes``/``doc.layers`` en un paso deshacible.

    ::

        with edit_document(doc, undo, "Mover"):
            doc.shapes[i] = doc.shapes[i].transformed(m)
    """

    def __init__(self, doc: object, undo: UndoStack, text: str) -> None:
        self.doc, self.undo, self.text = doc, undo, text

    def __enter__(self) -> object:
        self.before = DocumentEdit.snapshot(self.doc)
        return self.doc

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        if exc_type is not None:
            DocumentEdit(self.doc, self.before, self.before, self.text).undo()
            return False
        after = DocumentEdit.snapshot(self.doc)
        same_shapes = len(after[0]) == len(self.before[0]) and all(
            a is b for a, b in zip(after[0], self.before[0]))
        same_layers = len(after[1]) == len(self.before[1]) and all(
            a.__dict__ == b.__dict__ for a, b in zip(after[1], self.before[1]))
        if same_shapes and same_layers:
            return False
        self.undo.push(DocumentEdit(self.doc, self.before, after, self.text))
        return False

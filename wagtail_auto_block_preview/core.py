from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from django.db import connection, transaction
from django.db.models.signals import (
    m2m_changed,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)
from django.dispatch import Signal

from wagtail.blocks import Block

_MODEL_LIFECYCLE_SIGNALS = (pre_save, post_save, pre_delete, post_delete, m2m_changed)


class ValueFaker:
    """A plain preview value, no database write — text, numbers, dates, ..."""

    def __init__(self, fn: Callable[[Block], Any]) -> None:
        self.fn = fn

    def __call__(self, block: Block) -> Any:
        return self.fn(block)


class FabricatedFaker:
    """
    A real, persisted object for the duration of a render, then rolled
    back — for chooser/relational fields where a plain stand-in won't
    survive a real `.get_absolute_url()`/related-manager call. Always
    invoked inside render_in_sandbox(); never call fn() directly.
    """

    def __init__(self, fn: Callable[[Block], Any]) -> None:
        self.fn = fn

    def __call__(self, block: Block) -> Any:
        return self.fn(block)


class FakerRegistry:
    """
    {block_class: faker} lookup, resolved by MRO — a faker registered for a
    base class (e.g. URLBlock) automatically applies to subclasses that
    don't register their own.
    """

    def __init__(self) -> None:
        # Keyed on `type`, not `type[Block]` — lookup() walks a block
        # class's full __mro__, which always ends in `object`.
        self._fakers: dict[type, ValueFaker | FabricatedFaker] = {}
        self._loaded = False

    def register(self, block_class: type[Block], faker: ValueFaker | FabricatedFaker) -> None:
        self._fakers[block_class] = faker

    def lookup(self, block_class: type[Block]) -> ValueFaker | FabricatedFaker | None:
        self._ensure_loaded()
        for klass in block_class.__mro__:
            faker = self._fakers.get(klass)
            if faker is not None:
                return faker
        return None

    def reset(self) -> None:
        self._fakers.clear()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        from wagtail import hooks

        for fn in hooks.get_hooks("register_block_fakers"):
            for block_class, faker in fn():
                self._fakers.setdefault(block_class, faker)


registry = FakerRegistry()


class SignalRegistry:
    """
    Which Django signals render_in_sandbox() mutes during fabrication, on
    top of the fixed model-lifecycle baseline (pre/post_save,
    pre/post_delete, m2m_changed) — extend via the register_muted_signals
    hook when a project's own receiver lives on a signal outside that set
    (e.g. a custom signal a FabricatedFaker's model triggers) and would
    otherwise fire during fabrication.

        @hooks.register("register_muted_signals")
        def mute_extra_signals():
            return [my_app.signals.stock_changed]
    """

    def __init__(self, defaults: tuple[Signal, ...]) -> None:
        self._defaults = defaults
        self._extra: list[Signal] = []
        self._loaded = False

    def all(self) -> tuple[Signal, ...]:
        self._ensure_loaded()
        return self._defaults + tuple(self._extra)

    def reset(self) -> None:
        self._extra = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        from wagtail import hooks

        for fn in hooks.get_hooks("register_muted_signals"):
            self._extra.extend(fn())

    @contextmanager
    def mute(self):
        # Detach every receiver so a row fabricated for a preview never
        # triggers real side effects (search indexing, emails, webhooks, ...).
        saved = []
        for signal in self.all():
            saved.append((signal, signal.receivers))
            signal.receivers = []
            signal.sender_receivers_cache.clear()
        try:
            yield
        finally:
            for signal, receivers in saved:
                signal.receivers = receivers
                signal.sender_receivers_cache.clear()


muted_signals = SignalRegistry(_MODEL_LIFECYCLE_SIGNALS)


def render_in_sandbox(fn: Callable[[], Any]) -> Any:
    """
    Run fn() inside a savepoint that is always rolled back afterwards, so a
    FabricatedFaker can create real, persisted, relational objects without
    leaving anything behind.

    fn() must do both the fabrication *and* whatever consumes the
    fabricated object (e.g. rendering a template with it) — splitting
    "fabricate" and "render" across this boundary means the render could
    run after the rollback, against a row that no longer exists.
    """
    if not connection.features.uses_savepoints:
        raise RuntimeError(
            "wagtail_auto_block_preview requires a database backend that "
            "supports savepoints (this connection does not) — refusing to "
            "fabricate, since writes made during fabrication could not be "
            "guaranteed to roll back."
        )
    with transaction.atomic(), muted_signals.mute():
        sid = transaction.savepoint()
        try:
            return fn()
        finally:
            transaction.savepoint_rollback(sid)


def fabricated(fn: Callable[[], Any]) -> Callable[[], Any]:
    """
    Wrap a zero-arg callable for a block's own `preview_value=`/`default=`
    kwarg (Wagtail's `_evaluate_callable` already supports a plain zero-arg
    callable there), getting the same sandboxing a registered
    FabricatedFaker gets. For a one-off fabrication needed in exactly one
    place; register a FabricatedFaker via the `register_block_fakers` hook
    instead when the same fabrication should apply everywhere a block type
    is used.

        highlighted_product = ProductChooserBlock(
            preview_value=fabricated(lambda: ProductFactory()),
        )
    """

    def _evaluate() -> Any:
        return render_in_sandbox(fn)

    return _evaluate

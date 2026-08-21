import logging
from typing import TYPE_CHECKING, Any

from wagtail.blocks import Block
from wagtail.blocks.stream_block import StreamValue

if TYPE_CHECKING:
    # Bases for the type checker only: at runtime these must stay plain mixins,
    # or they could not be applied to the very classes they describe.
    from wagtail.blocks import ListBlock as _ListBase
    from wagtail.blocks import StreamBlock as _StreamBase
    from wagtail.blocks import StructBlock as _StructBase
else:
    _StructBase = _ListBase = _StreamBase = object

from .core import FabricatedFaker, registry, render_in_sandbox

logger = logging.getLogger(__name__)

# Distinguishes "the registry has nothing for this block type" from "the
# registry produced None as a genuine value".
_NOT_FAKED = object()

DEFAULT_PREVIEW_ITEM_COUNT = 2


def _try_faker(child_block: Block) -> Any:
    # `__class__`, not `type()`: a block may be a proxy standing in for another
    # (wagtail-block-reference does this for lazy/cyclic graphs), and a proxy
    # reports the block it points at through `__class__` — the same convention
    # Django's LazyObject follows. `type()` would see the proxy and find no
    # faker for it.
    faker = registry.lookup(child_block.__class__)
    if faker is None:
        return _NOT_FAKED
    try:
        if isinstance(faker, FabricatedFaker):
            return render_in_sandbox(lambda: faker(child_block))
        return faker(child_block)
    except Exception:
        # A faker is often DB- or network-adjacent (chooser lookups,
        # fabrication) and outside this package's control once a project
        # registers its own — one broken faker must not take down the
        # preview of every other field in the tree. Log it loudly (it's a
        # real bug to fix) and fall back to the field's own native value.
        logger.warning(
            "Faker for %s failed, falling back to its native preview value",
            child_block.__class__.__name__,
            exc_info=True,
        )
        return _NOT_FAKED


def _evaluate_callable(value: Any) -> Any:
    # Wagtail's own Block._evaluate_callable does exactly this, but only
    # exists from Wagtail ~7.3 onward — Wagtail 7.0's get_preview_value()
    # doesn't support a callable preview_value at all. Inlined so a
    # callable Meta.preview_value works on every Wagtail version this
    # package supports.
    return value() if callable(value) else value


def _has_explicit_default(block: Block) -> bool:
    # hasattr(block.meta, "default") is always True — every Block declares
    # a base Meta.default = None — so it can't tell "explicitly set" from
    # "never set". _constructor_args (set dynamically in Block.__new__,
    # hence the ty ignore) is Wagtail's own record of the literal kwargs a
    # block was constructed with; checking membership there is reliable.
    _args, kwargs = block._constructor_args  # ty: ignore[unresolved-attribute]
    return "default" in kwargs


def _resolve_child_value(child_block: Block) -> Any:
    """
    One child block's preview value, in precedence order: its own explicit
    preview_value/default, else a registered faker, else its own native
    get_preview_value(). Shared by StructBlock (once per named field) and
    ListBlock (once per generated item) — same rule, one place.

    An explicit preview_value is evaluated here rather than delegated to
    child_block.get_preview_value() — most child blocks (CharBlock, etc.)
    are plain Wagtail blocks, not this module's StructBlock/ListBlock, so
    their own get_preview_value() only supports a callable preview_value
    from Wagtail ~7.3 onward.
    """
    if hasattr(child_block.meta, "preview_value"):
        return child_block.normalize(_evaluate_callable(child_block.meta.preview_value))
    if _has_explicit_default(child_block):
        return child_block.get_preview_value()
    faked = _try_faker(child_block)
    return faked if faked is not _NOT_FAKED else child_block.get_preview_value()


class StructBlockPreviewMixin(_StructBase):
    """
    Generates get_preview_value() field by field instead of requiring a
    hand-authored preview_value for the whole block.

    Resolution order per field, most specific first:
      1. An explicit preview_value on that field's own declaration — always
         wins, no faker involved. Also how to hint a field the registry has
         no principled way to auto-fill (e.g. a nested StreamBlock).
      2. An explicit, meaningful default on that field's own declaration —
         an intentional default already *is* a reasonable preview value.
      3. A faker registered for that field's block type (or a base class),
         via the register_block_fakers hook or a FabricatedFaker. If the
         faker raises, this falls through to (4) instead of failing the
         whole block's preview.
      4. The field's own get_preview_value() — recurses for a nested
         StructBlock/ListBlock from this module, or falls back to Wagtail's
         own get_default() for a plain vanilla block.

    An explicit preview_value on *this* block as a whole is respected
    exactly as Wagtail's own Block.get_preview_value() already does, and
    skips the per-field walk entirely.

    Opt out via Meta:
      class Meta:
          fake = False               # disable entirely for this block —
                                      # falls back to Wagtail's own
                                      # get_preview_value()
          fake_exclude = ("body",)   # disable for just these fields —
                                      # each falls back to its own
                                      # get_preview_value()
    """

    def get_preview_value(self) -> dict:
        if hasattr(self.meta, "preview_value"):
            return self.normalize(_evaluate_callable(self.meta.preview_value))
        if getattr(self.meta, "fake", True) is False:
            return super().get_preview_value()

        excluded = getattr(self.meta, "fake_exclude", ())
        result = {}
        for name, child in self.child_blocks.items():
            if name in excluded:
                result[name] = child.get_preview_value()
            else:
                result[name] = _resolve_child_value(child)
        return self.normalize(result)


class ListBlockPreviewMixin(_ListBase):
    """
    Generates a small number of fake items instead of the single
    default-valued one Wagtail's own ListBlock.get_default() produces.
    Unlike StreamBlock, a ListBlock's item type is singular and known, so
    there's no open-endedness to work around — the same per-item resolution
    StructBlock uses for a field applies directly here.

    Item count: at least min_num (never below it), capped at max_num if
    set, otherwise DEFAULT_PREVIEW_ITEM_COUNT — enough to show it's a
    repeated element without generating an excessive preview.

    An explicit preview_value on the block as a whole is respected exactly
    as Wagtail's own Block.get_preview_value() already does, and skips
    generation entirely — the same rule StructBlock applies. Set
    `Meta.fake = False` to disable generation and fall back to Wagtail's
    own get_preview_value() instead.
    """

    @property
    def _preview_item_count(self) -> int:
        lo = max(self.meta.min_num or 0, 1)
        hi = self.meta.max_num
        count = max(lo, DEFAULT_PREVIEW_ITEM_COUNT)
        return count if hi is None else min(count, max(hi, lo))

    def get_preview_value(self) -> list:
        if hasattr(self.meta, "preview_value"):
            return self.normalize(_evaluate_callable(self.meta.preview_value))
        if getattr(self.meta, "fake", True) is False:
            return super().get_preview_value()

        items = [_resolve_child_value(self.child_block) for _ in range(self._preview_item_count)]
        return self.normalize(items)


class StreamBlockPreviewMixin(_StreamBase):
    """
    Fills a stream from the child types named in `Meta.preview_blocks`.

    A stream is the one shape this package cannot generate on its own — not
    because the child blocks are unfakeable, but because a stream is an
    arbitrary-length, arbitrary-composition sequence, so "which types, in what
    order, how many" has no principled answer. That is the author's knowledge,
    and naming the types is the whole of it:

        class Meta:
            preview_blocks = ["heading", "rich_text", "rich_text"]

    Each named type is then filled by the same per-block resolution a
    StructBlock field gets, so a child that gains a field, or a project that
    registers a faker for it, is picked up without touching this list.
    Repetition is meaningful — two `rich_text` entries are two paragraphs.

    A name no child block matches is skipped rather than raising: a stream's
    types often come from a registry a plugin contributes to, so a section may
    name one without depending on it being installed.

    Without `preview_blocks` this falls back to Wagtail's own behaviour, which
    is an empty stream. An explicit `preview_value` wins outright and
    `Meta.fake = False` disables generation, the same rules StructBlock and
    ListBlock apply.
    """

    def get_preview_value(self) -> StreamValue:
        if hasattr(self.meta, "preview_value"):
            return self.normalize(_evaluate_callable(self.meta.preview_value))
        if getattr(self.meta, "fake", True) is False:
            return super().get_preview_value()

        names = getattr(self.meta, "preview_blocks", None)
        if not names:
            return super().get_preview_value()

        # Native values, not the database representation normalize() expects.
        return StreamValue(
            self,
            [
                (name, _resolve_child_value(self.child_blocks[name]))
                for name in names
                if name in self.child_blocks
            ],
        )

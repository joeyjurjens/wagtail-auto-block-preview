import base64
import datetime
from collections.abc import Callable
from decimal import Decimal
from typing import Any, TypeVar

from django.utils.safestring import mark_safe

from wagtail.blocks import (
    BooleanBlock,
    CharBlock,
    ChoiceBlock,
    ChooserBlock,
    DateBlock,
    DateTimeBlock,
    DecimalBlock,
    EmailBlock,
    FloatBlock,
    IntegerBlock,
    MultipleChoiceBlock,
    PageChooserBlock,
    RawHTMLBlock,
    RichTextBlock,
    TextBlock,
    TimeBlock,
    URLBlock,
)

from faker import Faker

# One shared instance so every built-in faker draws from the same locale and
# seed state, keeping tone/register consistent across the whole set.
fake = Faker()

DEFAULT_IMAGE_WIDTH = 800
DEFAULT_IMAGE_HEIGHT = 600


class PlaceholderImage(str):
    """A data-URI that renders as a string but still takes an attribute.

    Wagtail's own `ImageBlock` assigns `contextual_alt_text`/`decorative` onto
    whatever its `image` child resolved to, which a plain `str` refuses. A
    subclass carries a `__dict__`, so the placeholder survives that step and is
    still just a URL everywhere else.
    """


def fake_image(
    width: int | None = None,
    height: int | None = None,
    ratio: str | None = None,
    label: str | None = None,
) -> PlaceholderImage:
    """
    A data-URI SVG placeholder of the given size — a labelled grey box, no
    database row, no static file, no urls.py wiring. Deliberately never
    picks a real image from the library: a block built for a specific
    aspect ratio shouldn't get a random image squashed into it, and there
    may be zero real images to pick from yet anyway.

    Pass either `ratio` (e.g. "16x9") with optionally one of `width`/
    `height` to derive the other, or `width`/`height` directly. With
    neither, falls back to an 800x600 box.
    """
    w, h = _resolve_image_dimensions(width, height, ratio)
    text = label or f"{w} × {h}"
    font_size = max(12, min(w, h) // 10)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
        f'<rect width="100%" height="100%" fill="#e5e7eb"/>'
        f'<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" '
        f'fill="#9ca3af" font-family="sans-serif" font-size="{font_size}">'
        f"{text}</text>"
        f"</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return PlaceholderImage(f"data:image/svg+xml;base64,{encoded}")


def _resolve_image_dimensions(
    width: int | None, height: int | None, ratio: str | None
) -> tuple[int, int]:
    if ratio:
        ratio_w, ratio_h = (int(part) for part in ratio.lower().split("x"))
        if width:
            return width, round(width * ratio_h / ratio_w)
        if height:
            return round(height * ratio_w / ratio_h), height
        return DEFAULT_IMAGE_WIDTH, round(DEFAULT_IMAGE_WIDTH * ratio_h / ratio_w)
    return width or DEFAULT_IMAGE_WIDTH, height or DEFAULT_IMAGE_HEIGHT


def _truncate(value: str, max_length: int | None) -> str:
    if max_length is not None and len(value) > max_length:
        return value[:max_length]
    return value


NumberT = TypeVar("NumberT", int, float, Decimal)


def _numeric_bounds(
    min_value: NumberT | None, max_value: NumberT | None, default_lo: NumberT, default_hi: NumberT
) -> tuple[NumberT, NumberT]:
    """
    Resolve a (lo, hi) range that always respects whichever of
    min_value/max_value the block actually declared, falling back to the
    defaults only on whichever side nothing was declared. If only one side
    was declared and it falls outside the unrelated default on the other
    side, collapse the range to the declared bound rather than ever
    producing a value that violates it.
    """
    lo = default_lo if min_value is None else min_value
    hi = default_hi if max_value is None else max_value
    if float(lo) > float(hi):
        if min_value is not None:
            lo = hi = min_value
        elif max_value is not None:
            lo = hi = max_value
    return lo, hi


def charblock_faker(block: CharBlock) -> str:
    max_length = block.field.max_length
    if max_length is None:
        return fake.sentence(nb_words=6).rstrip(".")
    return fake.text(max_nb_chars=max_length).replace("\n", " ").strip()


def textblock_faker(block: TextBlock) -> str:
    max_length = block.field.max_length
    return fake.paragraph() if max_length is None else fake.text(max_nb_chars=max_length)


def richtextblock_faker(block: RichTextBlock) -> str:
    max_length = block.max_length
    body = fake.paragraph() if max_length is None else fake.text(max_nb_chars=max_length)
    return f"<p>{body}</p>"


def urlblock_faker(block: URLBlock) -> str:
    return _truncate(fake.url(), block.field.max_length)


def emailblock_faker(block: EmailBlock) -> str:
    return _truncate(fake.email(), block.field.max_length)


def booleanblock_faker(block: BooleanBlock) -> bool:
    return True


def integerblock_faker(block: IntegerBlock) -> int:
    lo, hi = _numeric_bounds(block.field.min_value, block.field.max_value, 1, 100)
    return fake.random_int(min=lo, max=hi)


def floatblock_faker(block: FloatBlock) -> float:
    lo, hi = _numeric_bounds(block.field.min_value, block.field.max_value, 1.0, 100.0)
    if lo == hi:
        return lo
    return fake.pyfloat(min_value=lo, max_value=hi)


def decimalblock_faker(block: DecimalBlock) -> Decimal:
    field = block.field
    lo, hi = _numeric_bounds(field.min_value, field.max_value, Decimal(1), Decimal(100))
    if lo == hi:
        return lo
    right_digits = field.decimal_places
    left_digits = None
    if field.max_digits is not None:
        left_digits = max(field.max_digits - (field.decimal_places or 0), 1)
    return fake.pydecimal(
        left_digits=left_digits,
        right_digits=right_digits,
        min_value=float(lo),
        max_value=float(hi),
    )


def dateblock_faker(block: DateBlock) -> datetime.date:
    return fake.date_this_year()


def timeblock_faker(block: TimeBlock) -> datetime.time:
    return fake.time_object()


def datetimeblock_faker(block: DateTimeBlock) -> datetime.datetime:
    return fake.date_time_this_year()


def _choice_values(choices) -> list[str]:
    values = []
    for value, label in choices:
        if isinstance(label, (list, tuple)):
            # optgroup — (group_label, [(value, label), ...]); look inside
            values.extend(v for v, _ in label)
        elif value != "":
            values.append(value)
    return values


def choiceblock_faker(block: ChoiceBlock) -> str:
    choices = _choice_values(block.field.choices)
    return fake.random_element(choices) if choices else ""


def multiplechoiceblock_faker(block: MultipleChoiceBlock) -> list[str]:
    choices = _choice_values(block.field.choices)
    if not choices:
        return []
    count = fake.random_int(min=1, max=min(3, len(choices)))
    return list(fake.random_elements(choices, length=count, unique=True))


def rawhtmlblock_faker(block: RawHTMLBlock) -> str:
    max_length = block.field.max_length
    body = fake.paragraph() if max_length is None else fake.text(max_nb_chars=max_length)
    return f"<p>{body}</p>"


def embedblock_faker(block: Any) -> Any:
    # Not imported/type-hinted as EmbedValue itself — wagtail.embeds.blocks
    # imports wagtail.embeds.embeds, which imports the Embed *model* at
    # module level, unsafe to import at this module's eager-loaded top level.
    from wagtail.embeds.blocks import EmbedValue

    max_width = getattr(block.meta, "max_width", None)
    max_height = getattr(block.meta, "max_height", None)
    ratio = None if (max_width and max_height) else "16x9"
    image = fake_image(width=max_width, height=max_height, ratio=ratio)

    value = EmbedValue("https://example.com/fake-embed", max_width, max_height)
    # EmbedValue.html is a cached_property; assigning it directly (as any
    # cached_property allows) pre-fills the cache, so accessing it later —
    # e.g. rendering the block's preview — never runs the real
    # embed_to_frontend_html(), which would otherwise fetch the URL for real.
    value.html = mark_safe(f'<img src="{image}" alt="" style="max-width: 100%; height: auto;">')
    return value


def tableblock_faker(block: Any) -> dict:
    # Not imported/type-hinted as TableBlock itself — wagtail.contrib.table_block
    # pulls in wagtail.admin, unsafe to import at this module's eager-loaded top level.
    rows = block.table_options.get("startRows", 3)
    cols = block.table_options.get("startCols", 3)
    return {
        "data": [[fake.word() for _ in range(cols)] for _ in range(rows)],
        "first_row_is_table_header": False,
        "first_col_is_header": False,
        "table_header_choice": "neither",
    }


def chooserblock_faker(block: ChooserBlock) -> Any:
    """
    Picks an existing row from the chooser's own field.queryset (the
    ModelChoiceField Wagtail already builds for form validation) rather
    than creating one — there's no generically safe way to fabricate a
    valid instance of an arbitrary target model without knowing its
    required fields. field.queryset defaults to model_class.objects.all(),
    but a ChooserBlock subclass that overrides `field` to filter it gets
    that respected here too. Falls back to None (an empty chooser) if no
    rows exist yet, same as never registering a faker at all.

    Register a more specific faker (e.g. for a particular
    SnippetChooserBlock subclass or target model) to hand-pick an instance
    or generate one via a FabricatedFaker instead.
    """
    return block.field.queryset.order_by("?").first()


def construct_chooser_queryset(queryset, hook_name: str):
    """
    Run queryset through every function registered for hook_name, the same
    way a chooser's admin view applies its own construct_queryset_hook_name
    (see wagtail.admin.views.generic.chooser.BaseChooseView). Reusable in a
    project's own faker for a custom ChooserViewSet-based chooser — there's
    no way to discover a project's own hook_name from the block instance,
    since it lives on the admin view class, not the block.
    """
    from wagtail import hooks

    for hook in hooks.get_hooks(hook_name):
        queryset = hook(queryset, None)
    return queryset


def pagechooserblock_faker(block: PageChooserBlock) -> Any:
    # target_model only narrows to a single specific type when exactly one
    # page_type was given — with several, it falls back to the generic
    # Page model, so field.queryset alone could return a page of a type
    # this field never allows; type() applies that narrowing itself.
    # exclude(depth=1) and the hook match what Wagtail's own page chooser
    # applies, so this picks from the same pool an editor would see.
    queryset = block.field.queryset.exclude(depth=1)
    if len(block.target_models) > 1:
        queryset = queryset.type(*block.target_models)
    queryset = construct_chooser_queryset(queryset, "construct_page_chooser_queryset")
    return queryset.order_by("?").first()


def documentchooserblock_faker(block: Any) -> Any:
    queryset = construct_chooser_queryset(
        block.field.queryset, "construct_document_chooser_queryset"
    )
    return queryset.order_by("?").first()


BUILTIN_FAKERS: dict[type, Callable[[Any], Any]] = {
    CharBlock: charblock_faker,
    TextBlock: textblock_faker,
    RichTextBlock: richtextblock_faker,
    RawHTMLBlock: rawhtmlblock_faker,
    URLBlock: urlblock_faker,
    EmailBlock: emailblock_faker,
    BooleanBlock: booleanblock_faker,
    IntegerBlock: integerblock_faker,
    FloatBlock: floatblock_faker,
    DecimalBlock: decimalblock_faker,
    DateBlock: dateblock_faker,
    TimeBlock: timeblock_faker,
    DateTimeBlock: datetimeblock_faker,
    ChoiceBlock: choiceblock_faker,
    MultipleChoiceBlock: multiplechoiceblock_faker,
    ChooserBlock: chooserblock_faker,
    PageChooserBlock: pagechooserblock_faker,
}

# wagtail-auto-block-preview

While wagtail supports previews out of the box: [https://docs.wagtail.org/en/stable/topics/streamfield.html#configuring-block-previews](https://docs.wagtail.org/en/stable/topics/streamfield.html#configuring-block-previews), it requires you to manually set preview values for each block.

This can be quite some work and it's easy to forget updating the preview values if you change this block.

The `wagtail-auto-block-preview` package tries to bring automatic preview values using Faker. This could save some time, but you can also make your previews consistent with this if you'd like.

## Installation

```bash
pip install wagtail-auto-block-preview
```

Add `"wagtail_auto_block_preview"` to `INSTALLED_APPS` — Wagtail only discovers a package's `wagtail_hooks.py` (where the built-in fakers are registered) for apps listed there.

## Usage

Subclass this package's `StructBlock` instead of Wagtail's own (alias the import if you need both in the same file). Each field gets a preview value generated automatically, based on its block type:

```python
from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock
from wagtail_auto_block_preview import StructBlock, fake_image


class HeroBlock(StructBlock):
    heading = blocks.CharBlock()
    body = blocks.RichTextBlock()
    image = ImageChooserBlock(preview_value=fake_image(ratio="16x9"))
```

`heading`/`body` get a generated sentence/paragraph; `image` uses an explicit `preview_value` here since a chooser built for a specific aspect ratio shouldn't get a random image squashed into it (see "Choosers" below for the default chooser behaviour).

Per field, in order — the first one that applies wins:

1. An explicit `preview_value` on that field's own declaration.
2. An explicit `default` on that field's own declaration — an intentional default already *is* a reasonable preview value.
3. A registered faker for that field's block type.
4. The field's own native `get_preview_value()` (Wagtail's own fallback).

Opt out via `Meta` when a field's own faked value isn't right for a particular block, without having to give it an explicit `preview_value`/`default`:

```python
class HeroBlock(StructBlock):
    heading = blocks.CharBlock()
    body = blocks.RichTextBlock()

    class Meta:
        fake = False  # disable entirely — falls back to Wagtail's own get_preview_value()
        fake_exclude = ("body",)  # disable for just these fields
```

This package's `ListBlock` works the same way as `StructBlock` — subclass it instead of Wagtail's own to get a few fake items generated instead of the single default-valued one Wagtail's own `get_default()` produces. A `ListBlock`'s item type is singular and known, so each item is resolved the same way a `StructBlock` field is (item count: at least `min_num`, capped at `max_num`, 2 by default). `Meta.fake = False` disables generation the same way.

This package's `StreamBlock` needs one hint, because a stream is an open-ended, editor-chosen sequence: which types, in what order, how many. Name them and each one is filled by the same per-block resolution a `StructBlock` field gets:

```python
from wagtail_auto_block_preview import StreamBlock


class ContentStreamBlock(StreamBlock):
    heading = blocks.CharBlock()
    rich_text = blocks.RichTextBlock()

    class Meta:
        preview_blocks = ["heading", "rich_text", "rich_text"]
```

Repetition is meaningful — two `rich_text` entries are two paragraphs. Because only the *names* are listed, a child block that gains a field, or one a project registers a faker for, is picked up without touching this list.

`preview_blocks` also works as a constructor argument, which is the usual case when the stream's children come from a registry rather than a class body:

```python
content = ContentStreamBlock(preview_blocks=["heading", "rich_text"])
```

A name no child block matches is skipped rather than raising, so a stream whose types are contributed by a plugin can name one without depending on it. Without `preview_blocks` you get Wagtail's own behaviour — an empty stream. `Meta.fake = False` and an explicit `preview_value` behave as they do everywhere else.

### Choosers

A `ChooserBlock` (page, snippet, document, or a custom one) is faked by picking an existing row from the block's own `field.queryset` — the same `ModelChoiceField` Wagtail already builds for form validation — rather than creating one, since there's no generically safe way to fabricate a valid instance of an arbitrary model without knowing its required fields. If you subclass `ChooserBlock` and override `field` to filter its queryset, that's respected automatically. If no rows exist yet, the field falls back to `None`, same as an empty chooser.

For `PageChooserBlock` and `DocumentChooserBlock`, the pick is narrowed to match what a content editor would actually be offered in the real chooser: the tree root is excluded, page type restrictions are applied, and any `construct_page_chooser_queryset`/`construct_document_chooser_queryset` hook a project has already registered runs too. Permission filtering isn't included — a faker has no request/user to filter by — but anything a project already restricts through those hooks is respected.

A block built via `ChooserViewSet.get_block_class()` (as `DocumentChooserBlock` is, and as a project's own custom choosers typically are) works the same way, since it's just a `ChooserBlock` subclass with `target_model`/`widget` set — no special-casing needed. What it won't get automatically is a custom viewset's own `construct_<x>_chooser_queryset` hook, since that hook name lives on the admin view class, not on the block. Reuse `construct_chooser_queryset(queryset, hook_name)` in your own registered faker to replicate that:

```python
from wagtail_auto_block_preview import ValueFaker, construct_chooser_queryset


def widget_faker(block):
    queryset = construct_chooser_queryset(block.field.queryset, "construct_widget_chooser_queryset")
    return queryset.order_by("?").first()


@hooks.register("register_block_fakers")
def register_widget_faker():
    return [(WidgetChooserBlock, ValueFaker(widget_faker))]
```

To hand-pick a specific instance (e.g. always the same demo snippet, or one filtered by some field), register a more specific faker — see "Overriding and adding fakers" below.

### Overriding and adding fakers

Register a faker for a block type via the `register_block_fakers` hook, the same way Wagtail's own `wagtail_hooks.py` convention works:

```python
from wagtail import hooks
from wagtail_auto_block_preview import ValueFaker

from myapp.blocks import RatingBlock


@hooks.register("register_block_fakers")
def register_my_fakers():
    return [
        (RatingBlock, ValueFaker(lambda block: 4)),
    ]
```

A faker registered for a base class also applies to any subclass that doesn't register its own, resolved by MRO — most-specific wins.

This also overrides a *built-in* faker: register your own for `CharBlock`, `SnippetChooserBlock`, or any other block type the built-in hook already covers, and yours wins — the built-in hook runs last by design.

If a faker raises, that one field falls back to its own native preview value instead of taking down the whole block's preview — the failure is logged as a warning so it doesn't go unnoticed.

### Fabricating real objects

Some fields need a real, persisted, relational object — a chooser pointing at a snippet your code then calls `.get_absolute_url()` on, for example, where a bare stand-in object won't survive. `FabricatedFaker` runs its function inside a savepoint that's always rolled back afterwards, so nothing it creates is ever actually kept:

```python
from wagtail_auto_block_preview import FabricatedFaker

from myapp.factories import ProductFactory


@hooks.register("register_block_fakers")
def register_product_faker():
    return [(ProductChooserBlock, FabricatedFaker(lambda block: ProductFactory()))]
```

For a one-off fabrication needed in exactly one place, use `fabricated()` directly on a field instead of registering a hook:

```python
highlighted_product = ProductChooserBlock(
    preview_value=fabricated(lambda: ProductFactory()),
)
```

Fabrication mutes Django's model lifecycle signals (`pre_save`, `post_save`, `pre_delete`, `post_delete`, `m2m_changed`) for its duration, so it never triggers real side effects like search indexing or emails. If a project's own receiver lives on a *different* signal and also needs muting during fabrication, extend the set via a hook:

```python
@hooks.register("register_muted_signals")
def mute_extra_signals():
    return [my_app.signals.stock_changed]
```

### Placeholder images

`fake_image(width=None, height=None, ratio=None, label=None)` returns a data-URI SVG placeholder — no database row, no static file, no `urls.py` wiring required.

## Development

```bash
uv sync
uv run pytest tests/
uv run ruff check . && uv run ruff format --check .
uv run ty check wagtail_auto_block_preview/
```

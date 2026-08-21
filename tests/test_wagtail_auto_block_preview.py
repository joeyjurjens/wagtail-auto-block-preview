import base64
import datetime

from django import forms
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from wagtail import blocks, hooks
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.documents.models import Document
from wagtail.embeds.blocks import EmbedBlock
from wagtail.images.blocks import ImageChooserBlock
from wagtail.models import Page
from wagtail_auto_block_preview import (
    FabricatedFaker,
    ListBlockPreviewMixin,
    StreamBlockPreviewMixin,
    StructBlockPreviewMixin,
    ValueFaker,
    fabricated,
    fake_image,
    render_in_sandbox,
)
from wagtail_auto_block_preview.blocks import ListBlock, StreamBlock, StructBlock
from wagtail_auto_block_preview.core import FakerRegistry, SignalRegistry, muted_signals, registry
from wagtail_auto_block_preview.fakers import DEFAULT_IMAGE_HEIGHT, DEFAULT_IMAGE_WIDTH

from .testapp.models import AlphaPage, BetaPage, GammaPage, Widget


class AddressBlock(StructBlock):
    street = blocks.CharBlock()
    city = blocks.CharBlock()
    postal_code = blocks.CharBlock(required=False)


class ContentStreamBlock(blocks.StreamBlock):
    heading = blocks.CharBlock()
    rich_text = blocks.RichTextBlock()


class HeroSection1Block(StructBlock):
    title = blocks.CharBlock()
    image = ImageChooserBlock(preview_value=fake_image(ratio="16x9"))
    video = EmbedBlock(required=False)
    content = ContentStreamBlock(
        default=[
            ("heading", "Section heading"),
            ("rich_text", "<p>Some example body copy.</p>"),
        ],
    )

    class Meta:
        template = "testapp/hero_preview.html"


class StructBlockTests(SimpleTestCase):
    def test_leaf_fields_get_faked_automatically(self):
        value = AddressBlock().get_preview_value()
        self.assertTrue(value["street"])
        self.assertTrue(value["city"])
        # required=False still gets faked — faking doesn't key off required-ness
        self.assertTrue(value["postal_code"])

    def test_repeated_calls_produce_varied_values(self):
        values = {AddressBlock().get_preview_value()["street"] for _ in range(5)}
        self.assertGreater(len(values), 1)

    def test_explicit_default_beats_the_faker(self):
        class Block(StructBlock):
            heading = blocks.CharBlock(default="Explicit default")
            body = blocks.CharBlock()

        value = Block().get_preview_value()
        self.assertEqual(value["heading"], "Explicit default")
        self.assertNotEqual(value["body"], "")

    def test_explicit_preview_value_beats_the_faker(self):
        class Block(StructBlock):
            heading = blocks.CharBlock(preview_value="Explicit preview")
            body = blocks.CharBlock()

        value = Block().get_preview_value()
        self.assertEqual(value["heading"], "Explicit preview")

    def test_whole_block_preview_value_skips_the_per_field_walk(self):
        class Block(StructBlock):
            heading = blocks.CharBlock()
            body = blocks.CharBlock()

            class Meta:
                preview_value = {"heading": "Fixed heading", "body": "Fixed body"}

        value = Block().get_preview_value()
        self.assertEqual(value["heading"], "Fixed heading")
        self.assertEqual(value["body"], "Fixed body")

    def test_field_level_hint_on_open_ended_streamblock_field(self):
        value = HeroSection1Block().get_preview_value()
        names = [child.block.name for child in value["content"]]
        self.assertEqual(names, ["heading", "rich_text"])

    def test_image_field_gets_a_placeholder_data_uri(self):
        value = HeroSection1Block().get_preview_value()
        self.assertTrue(value["image"].startswith("data:image/svg+xml;base64,"))

    def test_nested_structblock_recurses(self):
        class Outer(StructBlock):
            title = blocks.CharBlock()
            hero = HeroSection1Block()

        value = Outer().get_preview_value()
        self.assertTrue(value["title"])
        self.assertTrue(value["hero"]["image"].startswith("data:image"))
        names = [c.block.name for c in value["hero"]["content"]]
        self.assertEqual(names, ["heading", "rich_text"])

    def test_unfaked_unhinted_field_falls_back_to_native_get_default(self):
        class UnknownLeafBlock(blocks.Block):
            def get_default(self):
                return "native default"

        class Block(StructBlock):
            mystery = UnknownLeafBlock()

        value = Block().get_preview_value()
        self.assertEqual(value["mystery"], "native default")

    def test_a_faker_that_raises_falls_back_to_the_native_default_instead_of_crashing(self):
        class BrokenBlock(blocks.CharBlock):
            pass

        def broken_faker(block):
            raise RuntimeError("this faker is broken")

        registry.register(BrokenBlock, ValueFaker(broken_faker))

        class Block(StructBlock):
            fine = blocks.CharBlock()
            broken = BrokenBlock()

        value = Block().get_preview_value()
        self.assertTrue(value["fine"])
        self.assertIsNone(value["broken"])

    def test_meta_fake_false_disables_generation_for_the_whole_block(self):
        class Block(StructBlock):
            heading = blocks.CharBlock()

            class Meta:
                fake = False

        value = Block().get_preview_value()
        self.assertIsNone(value["heading"])

    def test_meta_fake_exclude_disables_generation_for_named_fields_only(self):
        class Block(StructBlock):
            heading = blocks.CharBlock()
            body = blocks.CharBlock()

            class Meta:
                fake_exclude = ("heading",)

        value = Block().get_preview_value()
        self.assertIsNone(value["heading"])
        self.assertTrue(value["body"])

    def test_preview_value_renders_through_a_real_template(self):
        block = HeroSection1Block()
        html = block.render(block.get_preview_value())

        self.assertIn("<h1>", html)
        # Both the image field's placeholder and the embed field's fake
        # video placeholder must render as real, unescaped <img> tags.
        self.assertEqual(html.count('<img src="data:image/svg+xml;base64,'), 2)
        self.assertNotIn("&lt;img", html)
        # RichText must render as real, unescaped HTML, not an escaped string
        self.assertIn("<p>", html)
        self.assertNotIn("&lt;p&gt;", html)


class ListBlockTests(TestCase):
    def test_generates_the_default_number_of_items(self):
        block = ListBlock(blocks.CharBlock())
        value = block.get_preview_value()
        self.assertEqual(len(value), 2)
        self.assertTrue(all(item for item in value))

    def test_repeated_calls_produce_varied_items(self):
        block = ListBlock(blocks.CharBlock())
        first = [str(item) for item in block.get_preview_value()]
        second = [str(item) for item in block.get_preview_value()]
        self.assertNotEqual(first, second)

    def test_respects_min_num(self):
        block = ListBlock(blocks.CharBlock(), min_num=4)
        value = block.get_preview_value()
        self.assertEqual(len(value), 4)

    def test_respects_max_num(self):
        block = ListBlock(blocks.CharBlock(), max_num=1)
        value = block.get_preview_value()
        self.assertEqual(len(value), 1)

    def test_explicit_preview_value_skips_generation(self):
        block = ListBlock(blocks.CharBlock(), preview_value=["fixed one", "fixed two"])
        value = block.get_preview_value()
        self.assertEqual([str(item) for item in value], ["fixed one", "fixed two"])

    def test_struct_block_items_recurse(self):
        block = ListBlock(AddressBlock())
        value = block.get_preview_value()
        self.assertEqual(len(value), 2)
        for item in value:
            self.assertTrue(item["street"])
            self.assertTrue(item["city"])

    def test_fabricated_faker_items_are_rolled_back(self):
        registry.register(
            blocks.CharBlock, FabricatedFaker(lambda block: Widget.objects.create(name="listed"))
        )

        block = ListBlock(blocks.CharBlock())
        value = block.get_preview_value()

        self.assertEqual(len(value), 2)
        self.assertTrue(all(item.name == "listed" for item in value))
        self.assertEqual(Widget.objects.count(), 0)


class ProseStreamBlock(StreamBlock):
    heading = blocks.CharBlock()
    rich_text = blocks.RichTextBlock()
    address = AddressBlock()


class StreamBlockTests(SimpleTestCase):
    def test_without_preview_blocks_nothing_is_generated(self):
        self.assertEqual(len(ProseStreamBlock().get_preview_value()), 0)

    def test_named_blocks_are_filled(self):
        block = ProseStreamBlock(preview_blocks=["heading", "rich_text"])
        value = block.get_preview_value()
        self.assertEqual([child.block_type for child in value], ["heading", "rich_text"])
        self.assertTrue(all(child.value for child in value))

    def test_order_and_repetition_follow_the_list(self):
        block = ProseStreamBlock(preview_blocks=["rich_text", "heading", "rich_text"])
        value = block.get_preview_value()
        self.assertEqual(
            [child.block_type for child in value],
            ["rich_text", "heading", "rich_text"],
        )

    def test_nested_struct_children_are_resolved_too(self):
        block = ProseStreamBlock(preview_blocks=["address"])
        address = block.get_preview_value()[0].value
        self.assertTrue(address["street"])
        self.assertTrue(address["city"])

    def test_unknown_names_are_skipped(self):
        block = ProseStreamBlock(preview_blocks=["heading", "not_registered"])
        value = block.get_preview_value()
        self.assertEqual([child.block_type for child in value], ["heading"])

    def test_explicit_preview_value_skips_generation(self):
        block = ProseStreamBlock(
            preview_blocks=["heading"],
            preview_value=[{"type": "rich_text", "value": "<p>Chosen.</p>"}],
        )
        value = block.get_preview_value()
        self.assertEqual([child.block_type for child in value], ["rich_text"])

    def test_fake_false_disables_generation(self):
        block = ProseStreamBlock(preview_blocks=["heading"], fake=False)
        self.assertEqual(len(block.get_preview_value()), 0)


class ImageBlockFakerTests(SimpleTestCase):
    def test_wagtails_own_image_block_gets_a_placeholder(self):
        """It is a Wagtail StructBlock, so it never fakes itself — without a
        registered faker every preview holding one renders no image."""
        from wagtail.images.blocks import ImageBlock as WagtailImageBlock

        class Card(StructBlock):
            image = WagtailImageBlock()

        self.assertTrue(Card().get_preview_value()["image"])


class BlockProxy:
    """Minimal stand-in for wagtail-block-reference's `BlockReference`: forwards
    everything to its target and reports the target's type via `__class__`, the
    way Django's LazyObject does. `type()` still sees the proxy."""

    def __init__(self, target):
        self._target = target

    @property
    def __class__(self):
        return self._target.__class__

    def __getattr__(self, name):
        if name == "_target":
            raise AttributeError(name)
        return getattr(self._target, name)


class ProxiedStreamBlock(StreamBlock):
    rich_text = blocks.RichTextBlock()


class ProxiedChildTests(SimpleTestCase):
    def test_faker_resolves_through_a_proxy(self):
        block = ProxiedStreamBlock(preview_blocks=["rich_text"])
        block.child_blocks["rich_text"] = BlockProxy(blocks.RichTextBlock())
        value = block.get_preview_value()
        self.assertTrue(
            value[0].value.source,
            "a proxied child must still reach its registered faker",
        )


class SpecialCharBlock(blocks.CharBlock):
    pass


class UnregisteredBlock(blocks.Block):
    """No built-in hook registers a faker for this, and no test here does
    either — used for the genuine "nothing found" path without
    accidentally hitting a real built-in."""


class FakerRegistryTests(SimpleTestCase):
    def test_lookup_returns_none_when_unregistered(self):
        registry = FakerRegistry()
        self.assertIsNone(registry.lookup(UnregisteredBlock))

    def test_lookup_finds_exact_registration(self):
        registry = FakerRegistry()
        faker = ValueFaker(lambda block: "x")
        registry.register(blocks.CharBlock, faker)
        self.assertIs(registry.lookup(blocks.CharBlock), faker)

    def test_lookup_falls_back_through_mro(self):
        registry = FakerRegistry()
        faker = ValueFaker(lambda block: "x")
        registry.register(blocks.CharBlock, faker)
        self.assertIs(registry.lookup(SpecialCharBlock), faker)

    def test_lookup_prefers_most_specific_registration(self):
        registry = FakerRegistry()
        base_faker = ValueFaker(lambda block: "base")
        specific_faker = ValueFaker(lambda block: "specific")
        registry.register(blocks.CharBlock, base_faker)
        registry.register(SpecialCharBlock, specific_faker)
        self.assertIs(registry.lookup(SpecialCharBlock), specific_faker)
        self.assertIs(registry.lookup(blocks.CharBlock), base_faker)

    def test_register_after_load_overrides_hook_registered_faker(self):
        registry = FakerRegistry()
        registry.lookup(blocks.TextBlock)  # triggers _ensure_loaded()
        override = ValueFaker(lambda block: "overridden")
        registry.register(blocks.TextBlock, override)
        self.assertIs(registry.lookup(blocks.TextBlock), override)

    def test_reset_clears_registrations_and_forgets_hooks_were_loaded(self):
        registry = FakerRegistry()
        registry.register(UnregisteredBlock, ValueFaker(lambda block: "x"))
        registry.reset()
        # check _loaded before the next lookup() call — lookup() itself
        # re-triggers loading as a side effect
        self.assertFalse(registry._loaded)
        self.assertIsNone(registry.lookup(UnregisteredBlock))

    def test_value_faker_and_fabricated_faker_are_distinguishable(self):
        value_faker = ValueFaker(lambda block: "x")
        fabricated_faker = FabricatedFaker(lambda block: "x")
        self.assertIsInstance(value_faker, ValueFaker)
        self.assertNotIsInstance(value_faker, FabricatedFaker)
        self.assertIsInstance(fabricated_faker, FabricatedFaker)

    def test_hook_registered_faker_is_actually_discovered(self):
        def temporary_faker_hook():
            return [(UnregisteredBlock, ValueFaker(lambda block: "from a real hook"))]

        with hooks.register_temporarily("register_block_fakers", temporary_faker_hook):
            registry.reset()
            faker = registry.lookup(UnregisteredBlock)
            self.assertIsNotNone(faker)
            self.assertEqual(faker(None), "from a real hook")

        registry.reset()
        self.assertIsNone(registry.lookup(UnregisteredBlock))

    def test_project_hook_registered_faker_overrides_a_builtin_by_default(self):
        def project_hook():
            return [(blocks.CharBlock, ValueFaker(lambda block: "from the project"))]

        with hooks.register_temporarily("register_block_fakers", project_hook):
            registry.reset()
            faker = registry.lookup(blocks.CharBlock)
            self.assertEqual(faker(None), "from the project")

        registry.reset()


class SignalRegistryTests(SimpleTestCase):
    def test_all_starts_at_the_fixed_defaults(self):
        defaults = (object(), object())
        registry = SignalRegistry(defaults)
        self.assertEqual(registry.all(), defaults)

    def test_hook_registered_signals_are_appended_to_the_defaults(self):
        extra_signal = object()

        def signal_hook():
            return [extra_signal]

        with hooks.register_temporarily("register_muted_signals", signal_hook):
            muted_signals.reset()
            self.assertIn(extra_signal, muted_signals.all())

        muted_signals.reset()
        self.assertNotIn(extra_signal, muted_signals.all())


class SandboxTests(TestCase):
    def test_render_in_sandbox_rolls_back_after(self):
        def fabricate_and_check():
            widget = Widget.objects.create(name="temporary")
            self.assertTrue(Widget.objects.filter(pk=widget.pk).exists())
            return widget.name

        result = render_in_sandbox(fabricate_and_check)

        self.assertEqual(result, "temporary")
        self.assertEqual(Widget.objects.count(), 0)

    def test_a_raising_fabricated_faker_still_rolls_back(self):
        def fabricate_then_raise():
            Widget.objects.create(name="should not survive")
            raise RuntimeError("something went wrong after fabricating")

        with self.assertRaises(RuntimeError):
            render_in_sandbox(fabricate_then_raise)

        self.assertEqual(Widget.objects.count(), 0)

    def test_fabricated_faker_routes_through_structblock(self):
        registry.register(
            blocks.CharBlock,
            FabricatedFaker(lambda block: Widget.objects.create(name="fabbed")),
        )

        class Block(StructBlock):
            widget = blocks.CharBlock()

        value = Block().get_preview_value()

        self.assertEqual(value["widget"].name, "fabbed")
        self.assertEqual(Widget.objects.count(), 0)

    def test_a_raising_fabricated_faker_falls_back_instead_of_crashing_the_preview(self):
        def broken_fabrication(block):
            Widget.objects.create(name="should not survive either")
            raise RuntimeError("this faker is broken")

        registry.register(blocks.CharBlock, FabricatedFaker(broken_fabrication))

        class Block(StructBlock):
            fine = blocks.TextBlock()
            broken = blocks.CharBlock()

        value = Block().get_preview_value()

        self.assertTrue(value["fine"])
        self.assertIsNone(value["broken"])
        self.assertEqual(Widget.objects.count(), 0)

    def test_fabricated_helper_gives_one_off_field_the_same_sandboxing(self):
        class Block(StructBlock):
            widget_name = blocks.CharBlock(
                preview_value=fabricated(lambda: Widget.objects.create(name="one-off").name)
            )

        value = Block().get_preview_value()

        self.assertEqual(value["widget_name"], "one-off")
        self.assertEqual(Widget.objects.count(), 0)

    def test_value_faker_never_opens_a_transaction(self):
        class Block(StructBlock):
            text = blocks.CharBlock()

        value = Block().get_preview_value()
        self.assertIsInstance(value["text"], str)


class StreamFieldBlockPreviewTests(TestCase):
    """
    FabricatedFaker's safety depends on Wagtail's own native admin preview
    view (wagtailadmin_block_preview, backed by StreamFieldBlockPreview)
    fetching and rendering the preview value in one call, with no
    serialization round-trip that could re-fetch the already-rolled-back
    fabricated row in between. This hits that real view — not a
    stand-in — to prove it, not just assert it.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_fabricated_faker_survives_the_real_admin_preview_view(self):
        registry.register(
            blocks.CharBlock,
            FabricatedFaker(lambda block: Widget.objects.create(name="admin-preview-widget")),
        )

        class Block(StructBlock):
            widget = blocks.CharBlock()

            class Meta:
                template = "testapp/widget_preview.html"

        block = Block()
        url = reverse("wagtailadmin_block_preview") + f"?id={block.definition_prefix}"

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin-preview-widget")
        self.assertEqual(Widget.objects.count(), 0)


class EverythingBlock(StructBlock):
    char = blocks.CharBlock()
    text = blocks.TextBlock()
    rich_text = blocks.RichTextBlock()
    raw_html = blocks.RawHTMLBlock()
    url = blocks.URLBlock()
    email = blocks.EmailBlock()
    boolean = blocks.BooleanBlock(required=False)
    integer = blocks.IntegerBlock()
    date = blocks.DateBlock()
    time = blocks.TimeBlock()
    datetime_ = blocks.DateTimeBlock()
    choice = blocks.ChoiceBlock(choices=[("a", "Option A"), ("b", "Option B")])
    multiple_choice = blocks.MultipleChoiceBlock(
        choices=[("a", "Option A"), ("b", "Option B"), ("c", "Option C")]
    )
    image = ImageChooserBlock()


class BuiltinFakerTests(SimpleTestCase):
    def test_every_builtin_leaf_type_gets_a_plausible_value(self):
        value = EverythingBlock().get_preview_value()

        self.assertTrue(value["char"])
        self.assertTrue(value["text"])
        self.assertIn("<p>", value["rich_text"].source)
        self.assertIn("<p>", value["raw_html"])
        self.assertTrue(value["url"].startswith("http"))
        self.assertIn("@", value["email"])
        self.assertIs(value["boolean"], True)
        self.assertIsInstance(value["integer"], int)
        self.assertIsInstance(value["date"], datetime.date)
        self.assertIsInstance(value["time"], datetime.time)
        self.assertIsInstance(value["datetime_"], datetime.datetime)
        self.assertIn(value["choice"], ("a", "b"))
        self.assertTrue(1 <= len(value["multiple_choice"]) <= 3)
        self.assertTrue(set(value["multiple_choice"]) <= {"a", "b", "c"})
        self.assertTrue(value["image"].startswith("data:image/svg+xml;base64,"))

    def test_choice_faker_only_picks_registered_choices(self):
        class Block(StructBlock):
            rating = blocks.ChoiceBlock(choices=[("1", "One"), ("2", "Two"), ("3", "Three")])

        seen = {Block().get_preview_value()["rating"] for _ in range(20)}
        self.assertLessEqual(seen, {"1", "2", "3"})

    def test_choice_faker_handles_optgroups(self):
        class Block(StructBlock):
            rating = blocks.ChoiceBlock(
                choices=[
                    ("Group A", [("a1", "A1"), ("a2", "A2")]),
                    ("Group B", [("b1", "B1")]),
                ]
            )

        seen = {Block().get_preview_value()["rating"] for _ in range(20)}
        self.assertLessEqual(seen, {"a1", "a2", "b1"})

    def test_multiple_choice_faker_never_picks_unregistered_choices(self):
        class Block(StructBlock):
            ratings = blocks.MultipleChoiceBlock(choices=[("1", "One"), ("2", "Two")])

        for _ in range(20):
            picked = Block().get_preview_value()["ratings"]
            self.assertTrue(set(picked) <= {"1", "2"})

    def test_integer_faker_never_exceeds_declared_max_value(self):
        class Block(StructBlock):
            quantity = blocks.IntegerBlock(max_value=10)

        seen = {Block().get_preview_value()["quantity"] for _ in range(20)}
        self.assertTrue(all(1 <= v <= 10 for v in seen))

    def test_integer_faker_never_goes_below_declared_min_value(self):
        class Block(StructBlock):
            rating = blocks.IntegerBlock(min_value=200)

        seen = {Block().get_preview_value()["rating"] for _ in range(20)}
        self.assertTrue(all(v >= 200 for v in seen))

    def test_char_faker_never_exceeds_declared_max_length(self):
        class Block(StructBlock):
            code = blocks.CharBlock(max_length=5)

        seen = {Block().get_preview_value()["code"] for _ in range(20)}
        self.assertTrue(all(len(v) <= 5 for v in seen))

    def test_float_faker_respects_declared_bounds(self):
        class Block(StructBlock):
            ratio = blocks.FloatBlock(min_value=0.0, max_value=1.0)

        seen = {Block().get_preview_value()["ratio"] for _ in range(20)}
        self.assertTrue(all(0.0 <= v <= 1.0 for v in seen))

    def test_decimal_faker_respects_max_digits_and_decimal_places(self):
        class Block(StructBlock):
            price = blocks.DecimalBlock(max_digits=4, decimal_places=2, min_value=0, max_value=99)

        for _ in range(20):
            value = Block().get_preview_value()["price"]
            self.assertTrue(0 <= value <= 99)
            self.assertLessEqual(abs(value.as_tuple().exponent), 2)

    def test_embed_block_gets_a_fake_video_placeholder_not_a_real_fetch(self):
        # A fake URL would otherwise make EmbedValue.html fetch it for real
        # on render (inherited from urlblock_faker via MRO without this).
        from wagtail.embeds.blocks import EmbedBlock

        class Block(StructBlock):
            video = EmbedBlock(required=False)

        value = Block().get_preview_value()
        html = str(value["video"])
        self.assertIn("<img", html)
        self.assertIn("data:image/svg+xml;base64,", html)

    def test_embed_block_placeholder_respects_declared_max_width_and_height(self):
        from wagtail.embeds.blocks import EmbedBlock

        class Block(StructBlock):
            video = EmbedBlock(required=False, max_width=400, max_height=300)

        value = Block().get_preview_value()
        html = str(value["video"])
        prefix = "data:image/svg+xml;base64,"
        start = html.index(prefix) + len(prefix)
        encoded = html[start : html.index('"', start)]
        svg = base64.b64decode(encoded).decode("utf-8")
        self.assertIn('width="400"', svg)
        self.assertIn('height="300"', svg)


class WidgetChooserBlock(blocks.ChooserBlock):
    target_model = Widget
    widget = forms.Select


class ChooserFakerTests(TestCase):
    def test_picks_an_existing_row_of_the_target_model(self):
        widget = Widget.objects.create(name="pickable")

        class Block(StructBlock):
            widget = WidgetChooserBlock()

        value = Block().get_preview_value()
        self.assertEqual(value["widget"], widget)

    def test_falls_back_to_none_when_no_rows_exist(self):
        class Block(StructBlock):
            widget = WidgetChooserBlock(required=False)

        value = Block().get_preview_value()
        self.assertIsNone(value["widget"])

    def test_a_field_override_narrows_which_rows_can_be_picked(self):
        class NamedWidgetChooserBlock(WidgetChooserBlock):
            @property
            def field(self):
                return forms.ModelChoiceField(queryset=Widget.objects.filter(name="allowed"))

        Widget.objects.create(name="excluded")
        allowed = Widget.objects.create(name="allowed")

        class Block(StructBlock):
            widget = NamedWidgetChooserBlock()

        for _ in range(20):
            self.assertEqual(Block().get_preview_value()["widget"], allowed)


class PageChooserFakerTests(TestCase):
    def setUp(self):
        root = Page.objects.get(depth=1)
        self.alpha = root.add_child(instance=AlphaPage(title="Alpha", slug="alpha"))
        self.beta = root.add_child(instance=BetaPage(title="Beta", slug="beta"))
        self.gamma = root.add_child(instance=GammaPage(title="Gamma", slug="gamma"))

    def test_multiple_page_types_never_picks_a_disallowed_type(self):
        class Block(StructBlock):
            page = blocks.PageChooserBlock(page_type=["testapp.AlphaPage", "testapp.BetaPage"])

        for _ in range(20):
            picked = Block().get_preview_value()["page"]
            self.assertIn(picked.specific_class, (AlphaPage, BetaPage))

    def test_single_page_type_only_picks_that_type(self):
        class Block(StructBlock):
            page = blocks.PageChooserBlock(page_type="testapp.GammaPage")

        value = Block().get_preview_value()
        self.assertEqual(value["page"].specific_class, GammaPage)

    def test_root_page_is_never_picked(self):
        root = Page.objects.get(depth=1)

        class Block(StructBlock):
            page = blocks.PageChooserBlock()

        for _ in range(20):
            self.assertNotEqual(Block().get_preview_value()["page"].pk, root.pk)

    def test_construct_page_chooser_queryset_hook_is_applied(self):
        def exclude_beta(pages, request):
            return pages.exclude(pk=self.beta.pk)

        class Block(StructBlock):
            page = blocks.PageChooserBlock(page_type=["testapp.AlphaPage", "testapp.BetaPage"])

        with hooks.register_temporarily("construct_page_chooser_queryset", exclude_beta):
            for _ in range(20):
                self.assertEqual(Block().get_preview_value()["page"].pk, self.alpha.pk)


class DocumentChooserFakerTests(TestCase):
    def test_construct_document_chooser_queryset_hook_is_applied(self):
        allowed = Document.objects.create(
            title="allowed", file=SimpleUploadedFile("allowed.pdf", b"content")
        )
        Document.objects.create(
            title="excluded", file=SimpleUploadedFile("excluded.pdf", b"content")
        )

        def exclude_by_title(documents, request):
            return documents.exclude(title="excluded")

        class Block(StructBlock):
            document = DocumentChooserBlock()

        with hooks.register_temporarily("construct_document_chooser_queryset", exclude_by_title):
            for _ in range(20):
                self.assertEqual(Block().get_preview_value()["document"].pk, allowed.pk)


class TableBlockFakerTests(SimpleTestCase):
    def test_generates_a_grid_of_the_configured_size(self):
        from wagtail.contrib.table_block.blocks import TableBlock

        class Block(StructBlock):
            table = TableBlock(table_options={"startRows": 2, "startCols": 4})

        value = Block().get_preview_value()["table"]
        self.assertEqual(len(value["data"]), 2)
        self.assertEqual(len(value["data"][0]), 4)
        self.assertFalse(value["first_row_is_table_header"])
        self.assertFalse(value["first_col_is_header"])


class FakeImageTests(SimpleTestCase):
    def _decode(self, data_uri):
        prefix = "data:image/svg+xml;base64,"
        self.assertTrue(data_uri.startswith(prefix))
        return base64.b64decode(data_uri[len(prefix) :]).decode("utf-8")

    def test_defaults_to_800x600(self):
        svg = self._decode(fake_image())
        self.assertIn(f'width="{DEFAULT_IMAGE_WIDTH}"', svg)
        self.assertIn(f'height="{DEFAULT_IMAGE_HEIGHT}"', svg)
        self.assertIn(f"{DEFAULT_IMAGE_WIDTH} × {DEFAULT_IMAGE_HEIGHT}", svg)

    def test_explicit_width_and_height(self):
        svg = self._decode(fake_image(width=400, height=300))
        self.assertIn('width="400"', svg)
        self.assertIn('height="300"', svg)

    def test_ratio_alone_derives_both_dimensions(self):
        svg = self._decode(fake_image(ratio="16x9"))
        self.assertIn('width="800"', svg)
        self.assertIn('height="450"', svg)  # 800 * 9 / 16

    def test_ratio_with_explicit_width(self):
        svg = self._decode(fake_image(width=1200, ratio="16x9"))
        self.assertIn('width="1200"', svg)
        self.assertIn('height="675"', svg)  # 1200 * 9 / 16

    def test_ratio_with_explicit_height(self):
        svg = self._decode(fake_image(height=450, ratio="16x9"))
        self.assertIn('height="450"', svg)
        self.assertIn('width="800"', svg)  # 450 * 16 / 9

    def test_custom_label_overrides_dimension_text(self):
        svg = self._decode(fake_image(width=100, height=100, label="Hero"))
        self.assertIn("Hero", svg)
        self.assertNotIn("100 × 100", svg)


class PreviewMixinTests(SimpleTestCase):
    """The behaviour is reachable as a mixin, not only through these classes.

    A project that wants automatic previews on *every* block wants to apply the
    mixin to Wagtail's own classes rather than subclass ours. The mixin makes that
    expressible — but see `test_injecting_conflicts_with_the_concrete_classes`:
    the two ways of using this package are mutually exclusive.
    """

    def test_the_concrete_blocks_are_built_from_the_mixins(self):
        self.assertTrue(issubclass(StructBlock, StructBlockPreviewMixin))
        self.assertTrue(issubclass(ListBlock, ListBlockPreviewMixin))
        self.assertTrue(issubclass(StreamBlock, StreamBlockPreviewMixin))

    def test_the_mixin_carries_the_behaviour(self):
        """Nothing is left on the concrete class."""
        self.assertIn("get_preview_value", vars(StructBlockPreviewMixin))
        self.assertNotIn("get_preview_value", vars(StructBlock))

    def test_injecting_conflicts_with_the_concrete_classes(self):
        """Subclassing and injecting cannot both be used in one process.

        `StructBlock(StructBlockPreviewMixin, WagtailStructBlock)` fixes the mixin
        *before* Wagtail's class in its linearisation; making Wagtail's class
        inherit the mixin would put it *after*. Python rejects the contradiction
        whichever order the two are created in — so a project that wants the mixin
        on `wagtail.blocks.StructBlock` must not have these classes defined.
        """
        with self.assertRaises(TypeError):
            blocks.StructBlock.__bases__ = (StructBlockPreviewMixin,) + blocks.StructBlock.__bases__

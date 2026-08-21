from wagtail.blocks import ListBlock as WagtailListBlock
from wagtail.blocks import StreamBlock as WagtailStreamBlock
from wagtail.blocks import StructBlock as WagtailStructBlock

from .mixins import (
    ListBlockPreviewMixin,
    StreamBlockPreviewMixin,
    StructBlockPreviewMixin,
)


class StructBlock(StructBlockPreviewMixin, WagtailStructBlock):
    pass


class ListBlock(ListBlockPreviewMixin, WagtailListBlock):
    pass


class StreamBlock(StreamBlockPreviewMixin, WagtailStreamBlock):
    pass

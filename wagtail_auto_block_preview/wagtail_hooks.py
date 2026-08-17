from wagtail import hooks

from .core import ValueFaker
from .fakers import (
    BUILTIN_FAKERS,
    documentchooserblock_faker,
    embedblock_faker,
    fake_image,
    tableblock_faker,
)

try:
    from wagtail.images.blocks import ImageChooserBlock
except ImportError:  # wagtail.images not installed
    ImageChooserBlock: type | None = None

try:
    from wagtail.documents.blocks import DocumentChooserBlock
except ImportError:  # wagtail.documents not installed
    DocumentChooserBlock: type | None = None

try:
    from wagtail.embeds.blocks import EmbedBlock
except ImportError:  # wagtail.embeds not installed
    EmbedBlock: type | None = None

try:
    from wagtail.contrib.table_block.blocks import TableBlock
except ImportError:  # wagtail.contrib.table_block not installed
    TableBlock: type | None = None


@hooks.register("register_block_fakers", order=100)
def register_builtin_fakers():
    fakers = dict(BUILTIN_FAKERS)
    if ImageChooserBlock is not None:
        fakers[ImageChooserBlock] = lambda block: fake_image()
    if DocumentChooserBlock is not None:
        fakers[DocumentChooserBlock] = documentchooserblock_faker
    if EmbedBlock is not None:
        fakers[EmbedBlock] = embedblock_faker
    if TableBlock is not None:
        fakers[TableBlock] = tableblock_faker
    return [(block_class, ValueFaker(fn)) for block_class, fn in fakers.items()]

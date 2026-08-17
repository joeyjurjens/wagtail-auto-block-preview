from django.db import models

from wagtail.models import Page


class Widget(models.Model):
    """A minimal real model, used to verify FabricatedFaker actually
    persists a real row for the duration of a render and rolls it back
    afterwards — not tied to any particular chooser block implementation."""

    name = models.CharField(max_length=255)

    class Meta:
        app_label = "testapp"


class AlphaPage(Page):
    pass


class BetaPage(Page):
    pass


class GammaPage(Page):
    pass

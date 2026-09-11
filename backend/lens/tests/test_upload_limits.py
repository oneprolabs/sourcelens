"""Regression tests for configurable upload limits."""

from django.test import TestCase

from lens.datasource_services import get_datasource_upload_limits
from lens.models import GlobalSetting
from lens.serializers import GlobalSettingSerializer


class UploadLimitsTests(TestCase):
    """Keep default, override, and validation behavior consistent."""

    def test_defaults_and_overrides(self):
        """Missing values use defaults and saved values take effect."""

        self.assertEqual(get_datasource_upload_limits(), {
            "max_bytes": 52428800,
            "max_extracted_bytes": 104857600,
            "max_extracted_files": 300,
        })
        GlobalSetting.objects.create(
            key="lens.datasource_upload.max_bytes", value=73400320
        )
        self.assertEqual(
            get_datasource_upload_limits()["max_bytes"], 73400320
        )

    def test_invalid_limits_are_rejected(self):
        """Reject booleans, strings, fractions, and nonpositive values."""

        for name in get_datasource_upload_limits():
            for value in (True, False, 0, -1, 1.5, "100", None):
                with self.subTest(name=name, value=value):
                    serializer = GlobalSettingSerializer(data={
                        "key": f"lens.datasource_upload.{name}",
                        "value": value,
                    })
                    self.assertFalse(serializer.is_valid())

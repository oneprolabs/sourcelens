"""Control-plane provider for the LensNode file upload capability."""

from lens.plugins.contracts import ToolProviderError
from lens.plugins.providers.base import (
    DatasourceProvider,
    DatasourceProviderError,
)


PLUGIN_API_VERSION = 1
PLUGIN_KEY = "file_upload"
PLUGIN_VERSION = "1.0.0"


class FileUploadDatasourceProvider(DatasourceProvider):
    """Validate the connectionless managed workspace datasource."""

    key = PLUGIN_KEY

    def validate_connection(self, endpoint, connection_config):
        """Accept the empty endpoint used by a local LensNode capability."""

        del endpoint, connection_config
        return ""

    def validate_connection_scope(self, connection_scope):
        """Return an empty scope because files are local to the LensNode."""

        if connection_scope not in ({}, None):
            raise DatasourceProviderError("file upload scope must be empty")
        return {}

    def validate_datasource_source_type(self, source_type):
        """Require the manual upload runtime."""

        if source_type != "upload":
            raise DatasourceProviderError(
                "file upload datasource must use upload"
            )
        return source_type

    def validate_datasource_config(self, connection_scope, datasource_config):
        """Reject configuration because upload policy is host controlled."""

        self.validate_connection_scope(connection_scope)
        if datasource_config not in ({}, None):
            raise DatasourceProviderError(
                "file upload datasource config must be empty"
            )
        return {}

    def validate_live_connection(self, *args, **kwargs):
        """Local capabilities do not have a remote connection to probe."""

        del args, kwargs
        return {"status": "available"}


DATASOURCE_PROVIDER = FileUploadDatasourceProvider()


class FileUploadToolProvider:
    """Reject tool calls because this plugin exposes no model tools."""

    def validate_request(self, *args, **kwargs):
        """Raise the stable unsupported-tool error."""

        del args, kwargs
        raise ToolProviderError("PLUGIN_TOOL_UNSUPPORTED")


TOOL_PROVIDER = FileUploadToolProvider()
RPC_HANDLER = None
RPC_HTTP_ORIGINS = None

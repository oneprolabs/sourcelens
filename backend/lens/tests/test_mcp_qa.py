from django.test import SimpleTestCase

from lens.mcp_qa import QAMCPRequestError, validate_request


class QAMCPRequestTests(SimpleTestCase):
    def test_accepts_read_only_ask_request(self):
        request = validate_request(
            "sourcelens_ask",
            {"query": "Where is authentication configured?"},
        )
        self.assertEqual(request.max_results, 10)

    def test_rejects_management_tools(self):
        with self.assertRaisesMessage(
            QAMCPRequestError,
            "MCP_TOOL_UNSUPPORTED",
        ):
            validate_request("sourcelens_delete_workspace", {"query": "x"})

    def test_rejects_invalid_query_and_result_limit(self):
        for arguments in ({}, {"query": "x", "max_results": 0}):
            with self.assertRaises(QAMCPRequestError):
                validate_request("sourcelens_search", arguments)

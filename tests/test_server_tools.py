from mcp.server import MCPServer

from imap_connector.server import register_mail_tools


class FakeConnector:
    def __init__(self):
        self.calls = []

    def read_message(self, message_id, max_chars):
        self.calls.append(("stable", message_id, max_chars))
        return {"read_mode": "stable", "id": message_id}

    def read_message_by_reference(self, account, mailbox, message_ref, message_id_header, max_chars):
        self.calls.append(("reference", account, mailbox, message_ref, message_id_header, max_chars))
        return {"read_mode": "reference", "id": None, "message_ref": message_ref}


def setup_read_tool():
    server = MCPServer("test")
    fake = FakeConnector()
    register_mail_tools(server, lambda: fake)
    return fake, server._tool_manager.get_tool("read_message")


def test_read_message_schema_accepts_null_id_and_reference_selectors():
    _, tool = setup_read_tool()
    props = tool.parameters["properties"]
    assert "message_id" not in tool.parameters.get("required", [])
    assert {"account", "mailbox", "message_ref", "message_id_header"} <= set(props)
    assert props["message_id"].get("anyOf") == [
        {"type": "string"}, {"type": "null"}
    ]


def test_read_message_routes_null_id_to_reference_and_preserves_legacy_id():
    fake, tool = setup_read_tool()
    args = tool.fn_metadata.arg_model.model_validate({
        "message_id": None,
        "account": "naver",
        "mailbox": "INBOX",
        "message_ref": "a" * 64,
        "message_id_header": "<synthetic@example.invalid>",
    })
    result = tool.fn(**args.model_dump())
    assert result == {"read_mode": "reference", "id": None, "message_ref": "a" * 64}
    assert fake.calls == [(
        "reference", "naver", "INBOX", "a" * 64,
        "<synthetic@example.invalid>", 12000,
    )]

    stable = tool.fn(message_id="stable-id", max_chars=777)
    assert stable == {"read_mode": "stable", "id": "stable-id"}
    assert fake.calls[-1] == ("stable", "stable-id", 777)


def test_read_message_rejects_mixed_or_incomplete_selectors():
    fake, tool = setup_read_tool()
    mixed = tool.fn(
        message_id="stable-id",
        message_ref="a" * 64,
        message_id_header="<synthetic@example.invalid>",
    )
    incomplete = tool.fn(message_ref="a" * 64, account="naver")
    assert mixed == {"error": "Provide one message selector"}
    assert incomplete == {
        "error": "Provide message_id or account, message_ref, and message_id_header"
    }
    assert fake.calls == []

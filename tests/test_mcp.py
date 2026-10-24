"""Real stdio handshake; no mail server connections and no credentials."""
import asyncio
import json
from pathlib import Path
import sys
from mcp import Client
from mcp.client.stdio import StdioServerParameters


def test_stdio_protocol():
    async def check():
        root=Path(__file__).resolve().parents[1]
        env={'IMAP_ACCOUNTS_FILE':str(root/'accounts.example.json'),'PYTHONPATH':str(root)}
        params=StdioServerParameters(command=sys.executable,args=['-m','imap_connector.server'],env=env,cwd=root)
        async with Client(params) as client:
            listing=await client.list_tools()
            tools=listing.tools if hasattr(listing,'tools') else listing
            assert {t.name for t in tools}=={'list_accounts','list_folders','search_messages','read_message'}
            assert all(t.annotations.read_only_hint for t in tools)
            result=await client.call_tool('list_accounts',{})
            assert not result.is_error
            assert json.loads(result.content[0].text)=={'accounts':[{'account':'naver','configured':False},{'account':'work','configured':False}]}
            missing=await client.call_tool('read_message',{'message_id':'invalid'})
            assert 'Invalid message id' in str(missing.content)
    asyncio.run(check())

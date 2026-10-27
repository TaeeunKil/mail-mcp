import base64
import json
from pathlib import Path
import ssl
import pytest
from imap_connector.core import (Connector, ConnectorError, BoundedIMAP, load_accounts,
    make_id, decode_id, parse_imap, utf7_encode, utf7_decode, text_parts)

ACCOUNTS={'naver':{'host':'imap.naver.com','port':993,'username_env':'TEST_USER','password_env':'TEST_PASS'}}
HEADER=b'Subject: =?utf-8?b?7YWM7Iqk7Yq4?=\r\nFrom: sender@example.invalid\r\n\r\n'
PLAIN=b'("TEXT" "PLAIN" ("CHARSET" "UTF-8") NIL NIL "BASE64" 12 1 NIL NIL)'
ATTACH=b'("APPLICATION" "PDF" ("NAME" "secret.pdf") NIL NIL "BASE64" 999 NIL ("ATTACHMENT" ("FILENAME" "secret.pdf")))'

class Fake:
    def __init__(self,*args,**kwargs):
        self.calls=[]; self.validity=b'123'; self.large=False
        self.fetch_rows=[b'999 (UID 501)',b'1000 (UID 502)']
        self.search_result=b'501 502'
        self.part=PLAIN; self.body=base64.b64encode('안녕'.encode())
        assert kwargs['ssl_context'].verify_mode==ssl.CERT_REQUIRED
        assert kwargs['ssl_context'].check_hostname is True
        assert kwargs['timeout']==15
    def login(self,user,password): self.calls.append(('LOGIN',user)); return 'OK',[]
    def logout(self): self.calls.append(('LOGOUT',)); return 'BYE',[]
    def select(self,box,readonly=False):
        assert readonly is True
        self.calls.append(('EXAMINE',box)); return 'OK',[b'1000']
    def response(self,name): return name,[self.validity]
    def list(self,*args): return 'OK',[b'(\\HasNoChildren) "/" "INBOX"', ('(\\HasNoChildren) "/" "'+utf7_encode('보관함')+'"').encode()]
    def fetch(self,seq,query):
        self.calls.append(('FETCH',seq,query)); return 'OK',self.fetch_rows
    def uid(self,cmd,*args):
        self.calls.append(('UID',cmd,*args))
        if cmd=='SEARCH': return 'OK',[self.search_result]
        assert cmd=='FETCH'
        uid, query=args
        if 'BODYSTRUCTURE' in query:
            part=self.part.replace(b'12 1',b'999999 1') if self.large else self.part
            return 'OK',[b'1000 (UID '+uid.encode()+b' RFC822.SIZE 10000 BODYSTRUCTURE ('+part+b' '+ATTACH+b' "MIXED" NIL NIL))']
        assert 'BODY.PEEK' in query
        if '[HEADER]' in query: return 'OK',[(b'header',HEADER)]
        assert '[1]' in query  # attachment section 2 must NEVER be fetched
        return 'OK',[(b'body',self.body)]

@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('TEST_USER','dummy@example.invalid'); monkeypatch.setenv('TEST_PASS','dummy-not-a-secret')
    f=Fake(ssl_context=ssl.create_default_context(),timeout=15)
    return Connector(ACCOUNTS,lambda *a,**kw:f),f

def test_accounts_no_secrets(setup):
    c,f=setup
    assert c.list_accounts()==[{'account':'naver','configured':True}]
    assert not f.calls

def test_search_bounded_readonly(setup):
    c,f=setup
    result=c.search_messages('naver',text='hello " OR ALL',scan_limit=50)
    assert result['messages'][0]['headers']['Subject']=='테스트'
    assert result['has_older_messages'] is True
    assert ('FETCH','951:1000','(UID)') in f.calls
    search=next(x for x in f.calls if x[:2]==('UID','SEARCH'))
    assert search[-1]==b'"hello \\" OR ALL"'
    assert all(x[0] not in ('STORE','EXPUNGE','APPEND','CLOSE') for x in f.calls)

@pytest.mark.parametrize(('scan_limit','sequence_set','rows','search_result'),[
    (1,'1000:1000',[b'1000 (UID 502)'],b'502'),
    (10,'991:1000',[b'999 (UID 501)',b'1000 (UID 502)'],b'501 502'),
])
def test_search_accepts_wide_uidvalidity_as_opaque_marker(setup,scan_limit,sequence_set,rows,search_result):
    c,f=setup
    f.validity=b'4294967296'
    f.fetch_rows=rows
    f.search_result=search_result

    result=c.search_messages('naver',mailbox='INBOX',limit=1,scan_limit=scan_limit)

    assert ('FETCH',sequence_set,'(UID)') in f.calls
    assert len(result['messages'])==1
    assert decode_id(result['messages'][0]['id'])==['naver','INBOX',4294967296,502]
    assert c.read_message(result['messages'][0]['id'])['id']==result['messages'][0]['id']
    assert not any(x[0] in ('STORE','EXPUNGE','APPEND','CLOSE') for x in f.calls)

@pytest.mark.parametrize('validity',[b'0',b'9223372036854775808',b'not-a-number'])
def test_invalid_uidvalidity_fails_closed(setup,validity):
    c,f=setup; f.validity=validity
    with pytest.raises(ConnectorError,match='Invalid UIDVALIDITY'):
        c.search_messages('naver',limit=1,scan_limit=1)
    assert not any(x[0] in ('FETCH','UID') for x in f.calls)

@pytest.mark.parametrize(('rows','search_result','error'),[
    ([b'1000 (UID 0)'],b'0','Server returned an invalid UID'),
    ([b'1000 (UID 4294967296)'],b'4294967296','Server returned an invalid UID'),
    ([b'1000 (UID 502)'],b'0','Server returned an invalid UID search result'),
])
def test_search_rejects_out_of_range_uid_values(setup,rows,search_result,error):
    c,f=setup; f.fetch_rows=rows; f.search_result=search_result
    with pytest.raises(ConnectorError,match=error):
        c.search_messages('naver',limit=1,scan_limit=1)
    searched=any(x[:2]==('UID','SEARCH') for x in f.calls)
    assert searched is (error.endswith('search result'))

def test_read_decodes_skips_attachment(setup):
    c,f=setup
    result=c.read_message(make_id('naver','INBOX',123,502))
    assert result['text']=='안녕'
    assert not result['attachments_downloaded']
    assert result['content_is_untrusted']
    assert all('[2]' not in str(x) for x in f.calls)
    assert ('LOGOUT',) in f.calls

def test_html_body_returns_korean_text_without_active_markup(setup):
    c,f=setup
    html='<p>안녕하세요</p><script>실행하지 마세요</script><div>메일 <b>내용</b></div>'
    raw=html.encode('utf-8')
    encoded=base64.b64encode(raw)
    f.part=('("TEXT" "HTML" ("CHARSET" "UTF-8") NIL NIL "BASE64" '+str(len(encoded))+' 1 NIL NIL)').encode()
    f.body=encoded
    result=c.read_message(make_id('naver','INBOX',123,502))
    assert result['text']=='\n안녕하세요\n메일 내용'
    assert '실행하지 마세요' not in result['text']

def test_large_body_never_fetched(setup):
    c,f=setup; f.large=True
    result=c.read_message(make_id('naver','INBOX',123,502))
    assert result['truncated']
    assert not any('BODY.PEEK[1]' in str(x) for x in f.calls)

def test_validity_rejects_stale_ids(setup):
    c,f=setup
    with pytest.raises(ConnectorError,match='UIDVALIDITY'): c.read_message(make_id('naver','INBOX',124,502))
    assert not any(x[0]=='UID' for x in f.calls)

@pytest.mark.parametrize('field,value',[('mailbox','INBOX\r\nEXPUNGE'),('text','hello\x00'),('sender','a\nSTORE'),('since','01-Jan-2024'),('limit',51),('scan_limit',501)])
def test_validation(setup,field,value):
    c,f=setup
    with pytest.raises(ConnectorError): c.search_messages('naver',**{field:value})

@pytest.mark.parametrize('value',['???','e30',make_id('naver','INBOX',0,1),make_id('naver','INBOX',1,'1:*')])
def test_invalid_id(value):
    with pytest.raises(ConnectorError): decode_id(value)

def test_unknown_account_no_connection(setup):
    c,f=setup
    with pytest.raises(ConnectorError): c.search_messages('naver\nLOGIN')
    assert not f.calls

def test_mailbox_roundtrip_and_folders(setup):
    c,f=setup
    for name in ['INBOX','보관함','A&B','日本語/편지']:
        assert utf7_decode(utf7_encode(name))==name
    assert c.list_folders('naver')['folders'][1]['mailbox']=='보관함'

def test_config_rejects_secrets(tmp_path):
    path=tmp_path/'a.json'; bad=json.loads(json.dumps(ACCOUNTS)); bad['naver']['password']='bad'
    path.write_text(json.dumps({'accounts':bad}))
    with pytest.raises(ConnectorError): load_accounts(path)
    path.write_text(json.dumps({'accounts':ACCOUNTS}))
    assert load_accounts(path)==ACCOUNTS

def test_naver_example_config_is_single_account_and_secret_free():
    path=Path(__file__).resolve().parents[1]/'accounts.naver.example.json'
    accounts=load_accounts(path)
    assert list(accounts)==['naver']
    assert accounts['naver']['host']=='imap.naver.com'
    assert accounts['naver']['username_env']=='NAVER_IMAP_USER'
    assert accounts['naver']['password_env']=='NAVER_IMAP_APP_PASSWORD'

@pytest.mark.parametrize('field,value',[('host','localhost\r\n'),('port',False),('password_env','wrong-name'),('username_env',42)])
def test_config_invalid(tmp_path,field,value):
    a=json.loads(json.dumps(ACCOUNTS)); a['naver'][field]=value
    p=tmp_path/'a.json'; p.write_text(json.dumps({'accounts':a}))
    with pytest.raises(ConnectorError): load_accounts(p)

def test_server_errors_do_not_leak_credentials(setup):
    import imaplib
    c,f=setup
    def broken(*a): raise imaplib.IMAP4.error('dummy-not-a-secret')
    f.login=broken
    with pytest.raises(ConnectorError) as exc: c.list_folders('naver')
    assert 'dummy-not-a-secret' not in str(exc.value)
    assert ('LOGOUT',) in f.calls

def test_missing_credentials_block_before_connect(monkeypatch):
    monkeypatch.delenv('TEST_USER', raising=False)
    monkeypatch.delenv('TEST_PASS', raising=False)
    calls=[]
    c=Connector(ACCOUNTS,lambda *a,**kw:calls.append((a,kw)))
    with pytest.raises(ConnectorError,match='Set account credential environment variables'):
        c.list_folders('naver')
    assert calls==[]

def test_empty_mailbox(setup):
    c,f=setup; f.select=lambda *a,**k:('OK',[b'0'])
    assert c.search_messages('naver')['messages']==[]
    assert not any(x[0]=='FETCH' for x in f.calls)

def test_parser_literal_and_limit():
    assert parse_imap(b'(NIL "a\\"b" {3}\r\nfoo)')==[[None,b'a"b',b'foo']]
    with pytest.raises(ConnectorError): parse_imap(b'('*40+b')'*40)
    with pytest.raises(ConnectorError): parse_imap(b'{999999}\r\na')

def test_literal_budget_rejects_before_read():
    import time
    c=object.__new__(BoundedIMAP); c.deadline=time.monotonic()+20; c.remaining=100
    with pytest.raises(ConnectorError): c.read(101)
    c.deadline=time.monotonic()-1
    with pytest.raises(ConnectorError): c.read(1)

def test_attached_text_skipped():
    structure=parse_imap(b'("TEXT" "PLAIN" NIL NIL NIL "7BIT" 5 1 NIL ("ATTACHMENT" NIL))')[0]
    assert text_parts(structure)==[]

def test_korean_search_charset(setup):
    c,f=setup
    c.search_messages('naver',text='계약')
    call=next(x for x in f.calls if x[:2]==('UID','SEARCH'))
    assert call[2:6]==('CHARSET','UTF-8','UID','501,502')
    assert call[-1]=='"계약"'.encode()

def test_since_uses_imap_date_atom(setup):
    c,f=setup
    c.search_messages('naver',since='2024-01-02')
    call=next(x for x in f.calls if x[:2]==('UID','SEARCH'))
    assert call[-2:]==(b'SINCE',b'02-Jan-2024')

@pytest.mark.parametrize('since',['20240102','2024-1-2','2024-02-30'])
def test_since_rejects_non_iso_or_invalid_dates(setup,since):
    c,f=setup
    with pytest.raises(ConnectorError,match='since must be YYYY-MM-DD'):
        c.search_messages('naver',since=since)
    assert not f.calls

def test_bad_bodystructure_safe_error(setup):
    c,f=setup
    f.uid=lambda *a:('OK',[b'1 (UID 502 RFC822.SIZE 2)'])
    with pytest.raises(ConnectorError,match='IMAP operation failed'):
        c.read_message(make_id('naver','INBOX',123,502))

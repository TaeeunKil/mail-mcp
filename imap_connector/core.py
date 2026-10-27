"""Only EXAMINE/LIST/UID SEARCH/FETCH. Never STORE, EXPUNGE, APPEND or SMTP."""
import base64
import binascii
from contextlib import contextmanager
from datetime import date
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
import imaplib
import json
import os
import re
import ssl
import time


class ConnectorError(ValueError):
    """Safe, credential-free errors for tool clients."""


MAX_IMAP_UID = 2**32 - 1


class UIDValidityDiagnosticError(ConnectorError):
    """Fail-closed UIDVALIDITY error with a bounded, secret-free diagnostic."""

    def __init__(self, category, response_type, raw_number=None):
        self.category = category
        self.response_type = response_type
        self.raw_number = str(raw_number) if type(raw_number) is int else None
        super().__init__("Invalid UIDVALIDITY")

    def as_tool_result(self):
        result = {
            "error": "invalid_uidvalidity",
            "category": self.category,
            "response_type": self.response_type,
        }
        if self.raw_number is not None:
            result["raw_number"] = self.raw_number
        return result


def uidvalidity_response_type(value):
    if value is None:
        return "none"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, str):
        return "str"
    if type(value) is int:
        return "int"
    if isinstance(value, (list, tuple)):
        return "sequence"
    return "other"


def bounded_int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ConnectorError(f"Expected integer {low}..{high}")
    return value


def clean(value, maximum=512):
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConnectorError("Invalid text argument")
    return value


def quote(value):
    return '"' + clean(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def utf7_encode(value):
    chunks, run = [], []
    def flush():
        if run:
            chunks.append('&' + base64.b64encode(''.join(run).encode('utf-16be')).decode().rstrip('=').replace('/', ',') + '-')
            run.clear()
    for c in clean(value):
        if 32 <= ord(c) <= 126:
            flush(); chunks.append('&-' if c == '&' else c)
        else:
            run.append(c)
    flush()
    return ''.join(chunks)


def utf7_decode(value):
    def decode(m):
        data = m.group(1)
        if not data:
            return '&'
        return base64.b64decode(data.replace(',', '/') + '=' * (-len(data) % 4), validate=True).decode('utf-16be')
    try:
        return re.sub(r'&([^-]*)-', decode, value)
    except (ValueError, UnicodeError, binascii.Error):
        raise ConnectorError("Unsupported mailbox encoding") from None


def parse_imap(raw):
    """Small bounded parser for LIST / BODYSTRUCTURE; IMAP literals supported."""
    if not isinstance(raw, bytes) or len(raw) > 131072:
        raise ConnectorError("Server metadata too large")
    pos = 0
    def item(depth=0):
        nonlocal pos
        if depth > 30:
            raise ConnectorError("MIME nesting too deep")
        while pos < len(raw) and raw[pos:pos+1] == b' ': pos += 1
        if pos >= len(raw): raise ConnectorError("Invalid IMAP metadata")
        c = raw[pos:pos+1]; pos += 1
        if c == b'(':
            result = []
            while True:
                while pos < len(raw) and raw[pos:pos+1] == b' ': pos += 1
                if raw[pos:pos+1] == b')': pos += 1; return result
                result.append(item(depth+1))
        if c == b'"':
            out = bytearray()
            while pos < len(raw):
                c = raw[pos:pos+1]; pos += 1
                if c == b'"': return bytes(out)
                if c == b'\\': c = raw[pos:pos+1]; pos += 1
                out.extend(c)
            raise ConnectorError("Invalid quoted metadata")
        if c == b'{':
            end = raw.find(b'}\r\n', pos)
            if end < 0 or not raw[pos:end].isdigit(): raise ConnectorError("Invalid literal")
            n = int(raw[pos:end]); pos = end + 3
            if n > len(raw) - pos: raise ConnectorError("Truncated literal")
            out = raw[pos:pos+n]; pos += n; return out
        start = pos-1
        while pos < len(raw) and raw[pos:pos+1] not in (b' ', b')'): pos += 1
        atom = raw[start:pos]
        if not atom: raise ConnectorError("Invalid atom")
        return None if atom.upper() == b'NIL' else atom
    out = []
    while pos < len(raw):
        if raw[pos:].strip() == b'': break
        out.append(item())
    return out


class BoundedIMAP(imaplib.IMAP4_SSL):
    """Limit server reads (including hostile literal lengths) and session duration."""
    def __init__(self, *args, **kwargs):
        self.deadline = time.monotonic() + 60
        self.remaining = 4 * 1024 * 1024
        super().__init__(*args, **kwargs)

    def _check(self, size):
        remaining_time = self.deadline - time.monotonic()
        if size > 262144 or size > self.remaining or remaining_time <= 0:
            raise ConnectorError("Server response or session limit exceeded")
        if getattr(self, 'sock', None): self.sock.settimeout(min(15, remaining_time))

    def read(self, size):
        self._check(size)
        result = super().read(size); self.remaining -= len(result)
        return result

    def readline(self):
        self._check(0)
        result = super().readline(); self.remaining -= len(result)
        if self.remaining < 0: raise ConnectorError("Server response limit exceeded")
        return result


def load_accounts(path):
    try:
        with open(path, encoding='utf-8') as f:
            raw = f.read(65537)
        if len(raw) > 65536: raise ConnectorError("Config too large")
        root = json.loads(raw)
        if set(root) != {'accounts'} or not isinstance(root['accounts'], dict) or not 1 <= len(root['accounts']) <= 20:
            raise ConnectorError("Invalid accounts config")
        accounts = root['accounts']
        for name, a in accounts.items():
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', name): raise ConnectorError("Invalid account id")
            if not isinstance(a, dict) or set(a) != {'host', 'port', 'username_env', 'password_env'}: raise ConnectorError("Config accepts only host, port and environment references")
            if not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', a['host']): raise ConnectorError("Invalid host")
            bounded_int(a['port'], 1, 65535)
            for field in ('username_env', 'password_env'):
                if not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', a[field]): raise ConnectorError("Invalid environment reference")
        return accounts
    except ConnectorError: raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ConnectorError("Cannot load config; check JSON and environment reference fields") from None


def ok(result):
    status, data = result
    if status != 'OK': raise ConnectorError("Mail server rejected the read operation")
    return data


def header_dict(raw):
    msg = BytesParser(policy=policy.default).parsebytes(raw, headersonly=True)
    return {key: str(msg.get(key, ''))[:4096] for key in ('Subject', 'From', 'To', 'Date', 'Message-ID')}


def literal(data, limit):
    chunks = [x[1] for x in data if isinstance(x, tuple) and len(x) == 2 and isinstance(x[1], bytes)]
    if len(chunks) != 1 or len(chunks[0]) > limit: raise ConnectorError("Missing or oversized message section")
    return chunks[0]


def make_id(account, mailbox, validity, uid):
    return base64.urlsafe_b64encode(json.dumps([account, mailbox, validity, uid], separators=(',', ':')).encode()).decode().rstrip('=')


def decode_id(value):
    try:
        clean(value, 4096)
        parts = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if not isinstance(parts, list) or len(parts) != 4: raise ValueError()
        account, mailbox, validity, uid = parts
        clean(account, 40); clean(mailbox)
        bounded_int(validity, 1, MAX_IMAP_UID)
        bounded_int(uid, 1, MAX_IMAP_UID)
        return parts
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ConnectorError("Invalid message id") from None


def text_parts(structure, prefix='', depth=0):
    if depth > 20 or not isinstance(structure, list) or not structure: return []
    if isinstance(structure[0], list):
        n = next((i for i, x in enumerate(structure) if not isinstance(x, list)), len(structure))
        # Multipart disposition is after subtype and parameters.
        disposition = structure[n+2] if len(structure) > n+2 else None
        if isinstance(disposition, list) and disposition and disposition[0].upper() == b'ATTACHMENT': return []
        return [part for i, sub in enumerate(structure[:n], 1) for part in text_parts(sub, f'{prefix}.{i}' if prefix else str(i), depth+1)]
    if len(structure) < 8 or structure[0].upper() != b'TEXT' or structure[1].upper() not in (b'PLAIN', b'HTML'): return []
    params = structure[2] or []
    if not isinstance(params, list): return []
    p = {params[i].upper(): params[i+1] for i in range(0, len(params)-1, 2) if isinstance(params[i], bytes)}
    disp = structure[9] if len(structure) > 9 else None
    if b'NAME' in p or (isinstance(disp, list) and disp and (disp[0].upper() == b'ATTACHMENT' or (len(disp) > 1 and disp[1]))): return []
    return [{'section': prefix or '1', 'type': structure[1].decode('ascii').lower(), 'charset': (p.get(b'CHARSET') or b'utf-8').decode('ascii', 'replace'), 'encoding': structure[5].decode('ascii', 'replace'), 'bytes': int(structure[6])}]


class HTMLText(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
        if tag in ('p', 'br', 'div', 'li'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


class Connector:
    def __init__(self, accounts, factory=BoundedIMAP, environ=None):
        self.accounts=accounts
        self.factory=factory
        self.environ=os.environ if environ is None else environ

    def list_accounts(self):
        return [{'account': name, 'configured': bool(self.environ.get(a['username_env']) and self.environ.get(a['password_env']))} for name, a in self.accounts.items()]

    @contextmanager
    def connect(self, account):
        if not isinstance(account, str) or account not in self.accounts: raise ConnectorError("Unknown account")
        a = self.accounts[account]
        user, password = self.environ.get(a['username_env']), self.environ.get(a['password_env'])
        if not user or not password: raise ConnectorError("Set account credential environment variables locally; never send secrets in chat")
        clean(user, 320); clean(password, 2048)
        c = None
        try:
            c = self.factory(a['host'], a['port'], ssl_context=ssl.create_default_context(), timeout=15)
            ok(c.login(quote(user), password))
            yield c
        except ConnectorError: raise
        except (imaplib.IMAP4.error, OSError, UnicodeError, ValueError, TypeError, IndexError, AttributeError, KeyError):
            raise ConnectorError("IMAP operation failed; check local credentials, TLS and provider/admin IMAP policy") from None
        finally:
            if c is not None:
                try: c.logout()
                except Exception: pass

    def select(self, c, mailbox):
        data = ok(c.select(quote(utf7_encode(mailbox)), readonly=True))
        try:
            count = int(data[0])
        except (ValueError, TypeError, IndexError):
            raise ConnectorError("Invalid message count") from None
        if not 0 <= count <= MAX_IMAP_UID:
            raise ConnectorError("Invalid message count")
        response = c.response('UIDVALIDITY')
        if not isinstance(response, tuple) or len(response) != 2:
            raise UIDValidityDiagnosticError("invalid_response_shape", uidvalidity_response_type(response))
        code, values = response
        if code != 'UIDVALIDITY':
            raise UIDValidityDiagnosticError("unexpected_response_code", uidvalidity_response_type(code))
        if values is None:
            raise UIDValidityDiagnosticError("missing", "none")
        if not isinstance(values, (list, tuple)):
            raise UIDValidityDiagnosticError("invalid_response_data", uidvalidity_response_type(values))
        if not values:
            raise UIDValidityDiagnosticError("missing", "sequence")
        if len(values) != 1:
            raise UIDValidityDiagnosticError("multiple_values", "sequence")

        raw = values[0]
        response_type = uidvalidity_response_type(raw)
        if raw is None:
            raise UIDValidityDiagnosticError("missing", response_type)
        if isinstance(raw, bytes):
            try:
                raw_validity = raw.decode('ascii')
            except UnicodeDecodeError:
                raise UIDValidityDiagnosticError("non_ascii", response_type) from None
        elif isinstance(raw, str):
            raw_validity = raw
        elif type(raw) is int:
            if raw == 0:
                category = "zero"
            elif 1 <= raw <= MAX_IMAP_UID:
                category = "unexpected_type"
            else:
                category = "out_of_range"
            raw_number = raw if len(str(abs(raw))) <= 20 else None
            raise UIDValidityDiagnosticError(category, response_type, raw_number)
        else:
            raise UIDValidityDiagnosticError("unexpected_type", response_type)

        if re.fullmatch(r'[0-9]{1,20}', raw_validity):
            validity = int(raw_validity)
            if validity == 0:
                # Naver can return zero. Treat it as an unstable mailbox
                # marker: search may return headers from this session, but
                # no cross-session message ID may be issued from it.
                return count, 0
            if validity > MAX_IMAP_UID:
                raise UIDValidityDiagnosticError("out_of_range", response_type, validity)
        elif re.fullmatch(r'-[0-9]{1,19}', raw_validity):
            raise UIDValidityDiagnosticError("out_of_range", response_type, int(raw_validity))
        elif re.fullmatch(r'[0-9]{21,}', raw_validity):
            raise UIDValidityDiagnosticError("number_too_long", response_type)
        else:
            raise UIDValidityDiagnosticError("non_decimal", response_type)
        return count, validity

    def list_folders(self, account):
        with self.connect(account) as c:
            rows = ok(c.list('""', '"*"'))
            result=[]
            for row in rows[:200]:
                if row is None: continue
                if isinstance(row, tuple): row = row[0] + b'\r\n' + row[1]
                fields = parse_imap(row)
                if len(fields) != 3 or not isinstance(fields[2], bytes): continue
                flags = fields[0] or []
                result.append({'mailbox': utf7_decode(fields[2].decode('ascii')), 'selectable': b'\\Noselect' not in flags})
            return {'folders': result, 'truncated': len(rows) > 200}

    def search_messages(self, account, mailbox='INBOX', text='', sender='', since='', limit=20, scan_limit=200):
        bounded_int(limit, 1, 50); bounded_int(scan_limit, 1, 500)
        # Search uses typed filters, never raw IMAP expressions. UTF-8 quoted strings.
        criteria=[]
        for field, value in (('TEXT', text), ('FROM', sender)):
            if value: criteria.extend([field.encode(), quote(value).encode('utf-8')])
        if since:
            try:
                if not isinstance(since, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', since):
                    raise ValueError()
                d=date.fromisoformat(since)
                months=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
                criteria.extend([b'SINCE', f'{d.day:02}-{months[d.month-1]}-{d.year:04}'.encode()])
            except (ValueError, TypeError): raise ConnectorError("since must be YYYY-MM-DD") from None
        with self.connect(account) as c:
            count, validity = self.select(c, mailbox)
            read_mode = 'headers_only_uidvalidity_zero' if validity == 0 else 'search_and_read'
            if not count: return {'messages': [], 'scanned_recent': 0, 'scope': 'recent messages only', 'has_older_messages': False, 'read_mode': read_mode}
            rows = ok(c.fetch(f'{max(1,count-scan_limit+1)}:{count}', '(UID)'))
            uids=[]
            for row in rows:
                if isinstance(row, bytes):
                    match=re.search(rb'\bUID (\d+)\b', row)
                    if match:
                        try:
                            uids.append(bounded_int(int(match[1]), 1, MAX_IMAP_UID))
                        except ConnectorError:
                            raise ConnectorError("Server returned an invalid UID") from None
            if len(uids) > scan_limit: raise ConnectorError("Server exceeded search window")
            if not uids: return {'messages': [], 'scanned_recent': 0, 'scope': 'recent messages only', 'has_older_messages': count > scan_limit, 'read_mode': read_mode}
            uids=sorted(set(uids))
            uidset=','.join(str(u) for u in uids)
            charset='UTF-8' if any(any(b > 127 for b in item) for item in criteria) else None
            charset_args = ['CHARSET', charset] if charset else []
            found=ok(c.uid('SEARCH', *charset_args, 'UID', uidset, *criteria))
            try:
                matches=[bounded_int(int(x), 1, MAX_IMAP_UID) for x in (found[0] or b'').split()]
            except (ConnectorError, ValueError):
                raise ConnectorError("Server returned an invalid UID search result") from None
            if not set(matches) <= set(uids): raise ConnectorError("Server returned messages outside search scope")
            results=[]
            for uid in sorted(set(matches), reverse=True)[:limit]:
                raw=literal(ok(c.uid('FETCH', str(uid), '(BODY.PEEK[HEADER]<0.16384>)')), 16384)
                message = {'id': None if validity == 0 else make_id(account, mailbox, validity, uid), 'headers': header_dict(raw), 'headers_may_be_truncated': len(raw) == 16384}
                if validity == 0:
                    message.update(body_readable=False, body_readability_reason='UIDVALIDITY is zero; cross-session body reads are disabled.')
                results.append(message)
            return {'messages': results, 'scanned_recent': len(uids), 'scope': 'recent messages only, newest UID first', 'has_older_messages': count > scan_limit, 'more_matches_in_window': len(matches) > limit, 'read_mode': read_mode}

    def read_message(self, message_id, max_chars=12000):
        bounded_int(max_chars, 1, 30000)
        account, mailbox, validity, uid=decode_id(message_id)
        with self.connect(account) as c:
            _, current=self.select(c, mailbox)
            if current == 0: raise ConnectorError("UIDVALIDITY is zero; cross-session body reads are disabled")
            if validity != current: raise ConnectorError("Mailbox UIDVALIDITY changed; search again")
            rows=ok(c.uid('FETCH', str(uid), '(UID RFC822.SIZE BODYSTRUCTURE)'))
            if not rows or not isinstance(rows[0], bytes): raise ConnectorError("Message missing or unsupported BODYSTRUCTURE")
            parsed=parse_imap(rows[0]); attrs=parsed[1]
            fields={attrs[i].upper(): attrs[i+1] for i in range(0,len(attrs)-1,2)}
            if fields.get(b'UID') != str(uid).encode(): raise ConnectorError("Message UID mismatch")
            parts=text_parts(fields[b'BODYSTRUCTURE'])
            parts.sort(key=lambda p: p['type'] != 'plain')
            headers=header_dict(literal(ok(c.uid('FETCH', str(uid), '(BODY.PEEK[HEADER]<0.16384>)')),16384))
            result={'id':message_id, 'headers':headers, 'text':'', 'attachments_downloaded':False, 'content_is_untrusted':True, 'truncated':False}
            if not parts: result['note']='No supported non-attachment text part'; return result
            part=parts[0]
            if not 0 <= part['bytes'] <= 262144:
                result.update(truncated=True,note='Text part exceeds 256 KiB; body not downloaded'); return result
            raw=literal(ok(c.uid('FETCH',str(uid),f"(BODY.PEEK[{part['section']}]<0.262144>)")),262144)
            synthetic=(f"Content-Type: text/{part['type']}; charset=\"{part['charset']}\"\r\nContent-Transfer-Encoding: {part['encoding']}\r\n\r\n").encode()+raw
            msg=BytesParser(policy=policy.default).parsebytes(synthetic)
            payload=msg.get_payload(decode=True) or b''
            try: body=payload.decode(part['charset'],errors='replace')
            except LookupError: body=payload.decode('utf-8',errors='replace')
            if part['type']=='html':
                parser=HTMLText(); parser.feed(body); body=''.join(parser.parts)
            result.update(text=body[:max_chars],truncated=len(body)>max_chars or len(raw)<part['bytes'], selected_part=part['section'], other_text_parts_omitted=len(parts)>1)
            return result

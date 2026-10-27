"""Only EXAMINE/LIST/UID SEARCH/FETCH. Never STORE, EXPUNGE, APPEND or SMTP."""
import base64
import binascii
from contextlib import contextmanager
from datetime import date
from email import policy
from email.parser import BytesParser
from email.header import decode_header
from html.parser import HTMLParser
import hashlib
import hmac
import imaplib
import json
import os
import re
import ssl
import time
import unicodedata
from urllib.parse import unquote_to_bytes


class ConnectorError(ValueError):
    """Safe, credential-free errors for tool clients."""


MAX_IMAP_UID = 2**32 - 1
MAX_TEXT_PART_BYTES = 262144
MAX_ATTACHMENT_PARTS = 50
MAX_REFERENCE_CANDIDATES = 10
MAX_REFERENCE_SCAN = 500


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
    p = _parameter_map(params)
    disp = structure[9] if len(structure) > 9 else None
    disposition_params = _parameter_map(disp[1]) if isinstance(disp, list) and len(disp) > 1 else {}
    if any(key == 'name' or key.startswith('name*') for key in p) or any(key == 'filename' or key.startswith('filename*') for key in disposition_params) or (isinstance(disp, list) and disp and isinstance(disp[0], bytes) and disp[0].upper() == b'ATTACHMENT'):
        return []
    charset = p.get('charset', b'utf-8')
    encoding = structure[5] if isinstance(structure[5], bytes) else b'7bit'
    return [{'section': prefix or '1', 'type': structure[1].decode('ascii').lower(), 'charset': charset.decode('ascii', 'replace'), 'encoding': encoding.decode('ascii', 'replace'), 'bytes': int(structure[6])}]


def _parameter_map(value):
    if not isinstance(value, list):
        return {}
    return {
        key.decode('ascii', 'ignore').lower(): (val if isinstance(val, bytes) else b'')
        for key, val in zip(value[0::2], value[1::2])
        if isinstance(key, bytes)
    }


def _decode_mime_parameter(params, name):
    """Decode one bounded MIME parameter, including common RFC 2231 forms."""
    try:
        if name in params:
            raw = params[name].decode('utf-8', 'replace')
        elif name + '*' in params:
            raw = params[name + '*'].decode('ascii', 'replace')
            match = re.fullmatch(r"([^']*)'[^']*'(.*)", raw)
            if match is None:
                return raw
            raw = unquote_to_bytes(match.group(2)).decode(match.group(1) or 'utf-8', 'replace')
        else:
            segments = []
            for index in range(10):
                key = f'{name}*{index}*'
                plain_key = f'{name}*{index}'
                if key in params:
                    segments.append((True, params[key].decode('ascii', 'replace')))
                elif plain_key in params:
                    segments.append((False, params[plain_key].decode('utf-8', 'replace')))
                else:
                    break
            if not segments:
                return ''
            first = segments[0][1]
            charset = 'utf-8'
            if segments[0][0]:
                match = re.fullmatch(r"([^']*)'[^']*'(.*)", first)
                if match is not None:
                    charset, first = match.group(1) or 'utf-8', match.group(2)
            raw_bytes = bytearray()
            for index, (encoded, segment) in enumerate(segments):
                if encoded:
                    if index == 0 and segments[0][0] and "'" in segments[0][1]:
                        segment = first
                    raw_bytes.extend(unquote_to_bytes(segment))
                else:
                    raw_bytes.extend(segment.encode('utf-8'))
            raw = bytes(raw_bytes).decode(charset or 'utf-8', 'replace')
        # Decode RFC 2047 encoded-word parameters used by some servers.
        chunks = decode_header(raw)
        return ''.join(
            item.decode(encoding or 'utf-8', 'replace') if isinstance(item, bytes) else item
            for item, encoding in chunks
        )[:1024]
    except (LookupError, UnicodeError, ValueError):
        return ''


def sanitize_filename(value, fallback='attachment'):
    """Return a display-only filename; never use it to construct a filesystem path."""
    value = unicodedata.normalize('NFC', value if isinstance(value, str) else '')
    value = value.replace('\\', '/').split('/')[-1]
    value = ''.join(
        ch for ch in value
        if unicodedata.category(ch)[0] != 'C' and ch not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069'
    )
    value = re.sub(r'[<>:"|?*]', '_', value).strip(' .')
    if value in ('', '.', '..'):
        value = fallback
    stem = value.split('.', 1)[0].upper()
    if stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(?:COM|LPT)[1-9]', stem):
        value = '_' + value
    return value[:120].rstrip(' .') or fallback


def _message_id(raw_header):
    if not isinstance(raw_header, bytes) or len(raw_header) >= 16384:
        return None
    try:
        value = str(BytesParser(policy=policy.default).parsebytes(raw_header, headersonly=True).get('Message-ID', '')).strip()
    except (ValueError, UnicodeError):
        return None
    if len(value) > 512 or not re.fullmatch(r'<[!-~]+@[!-~]+>', value) or any(ch in value[1:-1] for ch in '<>"\\'):
        return None
    return value


def make_message_ref(account, mailbox, raw_header, size, internaldate):
    """Create a non-secret selector bound to visible message metadata, not an authorization token."""
    if _message_id(raw_header) is None:
        return None
    if type(size) is not int or not 0 <= size <= 2**63 - 1 or not isinstance(internaldate, bytes) or not internaldate or len(internaldate) > 128:
        return None
    fields = (
        b'mail-mcp-message-ref:v1', account.encode('utf-8'), mailbox.encode('utf-8'),
        raw_header, str(size).encode('ascii'), internaldate,
    )
    digest = hashlib.sha256()
    for field in fields:
        digest.update(len(field).to_bytes(4, 'big'))
        digest.update(field)
    return digest.hexdigest()


def attachment_parts(structure, prefix='', depth=0):
    """List attachment descriptors from BODYSTRUCTURE without fetching their bytes."""
    if depth > 20 or not isinstance(structure, list) or not structure:
        return []
    if isinstance(structure[0], list):
        child_count = next((i for i, item in enumerate(structure) if not isinstance(item, list)), len(structure))
        return [
            part
            for index, child in enumerate(structure[:child_count], 1)
            for part in attachment_parts(child, f'{prefix}.{index}' if prefix else str(index), depth + 1)
        ]
    if len(structure) < 7 or not isinstance(structure[0], bytes) or not isinstance(structure[1], bytes):
        return []
    major = structure[0].decode('ascii', 'ignore').lower()
    minor = structure[1].decode('ascii', 'ignore').lower()
    params = _parameter_map(structure[2])
    if major == 'message' and minor == 'rfc822':
        disposition_index = 11
    else:
        disposition_index = 9 if major == 'text' else 8
    disposition = structure[disposition_index] if len(structure) > disposition_index else None
    disposition_name = b''
    disposition_params = {}
    if isinstance(disposition, list) and disposition:
        if isinstance(disposition[0], bytes):
            disposition_name = disposition[0].upper()
        if len(disposition) > 1:
            disposition_params = _parameter_map(disposition[1])
    filename = _decode_mime_parameter(disposition_params, 'filename') or _decode_mime_parameter(params, 'name')
    if not filename and disposition_name != b'ATTACHMENT':
        return []
    try:
        size = int(structure[6])
    except (ValueError, TypeError):
        size = -1
    if size < 0 or size > 2**63 - 1:
        size = -1
    encoding = structure[5].decode('ascii', 'ignore').lower() if isinstance(structure[5], bytes) else ''
    if not re.fullmatch(r'[a-z0-9!#$&^_.+-]{1,64}', major) or not re.fullmatch(r'[a-z0-9!#$&^_.+-]{1,64}', minor):
        media_type = 'application/octet-stream'
    else:
        media_type = f'{major}/{minor}'
    return [{
        'section': prefix or '1',
        'media_type': media_type,
        'encoding': encoding,
        'size_bytes': size,
        'filename': sanitize_filename(filename, 'attachment'),
        'disposition': disposition_name.decode('ascii', 'ignore').lower() if disposition_name else '',
    }]


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

    def _fetch_fields(self, c, uid, query):
        rows = ok(c.uid('FETCH', str(uid), query))
        for row in rows:
            if not isinstance(row, bytes):
                continue
            parsed = parse_imap(row)
            if len(parsed) < 2 or not isinstance(parsed[1], list):
                continue
            attrs = parsed[1]
            fields = {
                attrs[index].upper(): attrs[index + 1]
                for index in range(0, len(attrs) - 1, 2)
                if isinstance(attrs[index], bytes)
            }
            if fields.get(b'UID') != str(uid).encode('ascii'):
                continue
            return fields
        raise ConnectorError('Message missing or unsupported metadata')

    def _snapshot(self, c, account, mailbox, uid):
        fields = self._fetch_fields(c, uid, '(UID RFC822.SIZE INTERNALDATE)')
        try:
            size = bounded_int(int(fields[b'RFC822.SIZE']), 0, 2**63 - 1)
        except (KeyError, ValueError, TypeError):
            raise ConnectorError('Invalid message size metadata') from None
        internaldate = fields.get(b'INTERNALDATE')
        if not isinstance(internaldate, bytes) or not internaldate or len(internaldate) > 128:
            raise ConnectorError('Invalid message date metadata')
        raw_header = literal(ok(c.uid('FETCH', str(uid), '(BODY.PEEK[HEADER]<0.16384>)')), 16384)
        message_ref = make_message_ref(account, mailbox, raw_header, size, internaldate)
        return {
            'raw_header': raw_header,
            'headers': header_dict(raw_header),
            'message_id_header': _message_id(raw_header),
            'size': size,
            'internaldate': internaldate,
            'message_ref': message_ref,
        }

    def _candidate_uids(self, c, count):
        if count <= 0:
            return []
        rows = ok(c.fetch(f'{max(1, count - MAX_REFERENCE_SCAN + 1)}:{count}', '(UID)'))
        uids = []
        for row in rows:
            if isinstance(row, bytes):
                match = re.search(rb'\bUID (\d+)\b', row)
                if match:
                    try:
                        uids.append(bounded_int(int(match[1]), 1, MAX_IMAP_UID))
                    except ConnectorError:
                        raise ConnectorError('Server returned an invalid UID') from None
        if len(uids) > MAX_REFERENCE_SCAN:
            raise ConnectorError('Server exceeded search window')
        return sorted(set(uids))

    def _resolve_zero_reference(self, c, account, mailbox, message_ref, message_id_header, count):
        if not isinstance(message_ref, str) or not re.fullmatch(r'[0-9a-f]{64}', message_ref):
            raise ConnectorError('Invalid message reference')
        if not isinstance(message_id_header, str) or len(message_id_header) > 512 or not re.fullmatch(r'<[!-~]+@[!-~]+>', message_id_header) or any(ch in message_id_header[1:-1] for ch in '<>"\\'):
            raise ConnectorError('Invalid Message-ID selector')
        uids = self._candidate_uids(c, count)
        if not uids:
            raise ConnectorError('Message reference is stale or ambiguous')
        found = ok(c.uid('SEARCH', 'UID', ','.join(map(str, uids)), 'HEADER', 'MESSAGE-ID', quote(message_id_header)))
        try:
            matches = [bounded_int(int(value), 1, MAX_IMAP_UID) for value in (found[0] or b'').split()]
        except (ConnectorError, ValueError, TypeError):
            raise ConnectorError('Server returned an invalid UID search result') from None
        if not set(matches) <= set(uids) or len(set(matches)) > MAX_REFERENCE_CANDIDATES:
            raise ConnectorError('Message reference is stale or ambiguous')
        exact = []
        for uid in sorted(set(matches)):
            snapshot = self._snapshot(c, account, mailbox, uid)
            if snapshot['message_id_header'] != message_id_header:
                continue
            if snapshot['message_ref'] and hmac.compare_digest(snapshot['message_ref'], message_ref):
                exact.append((uid, snapshot))
        if len(exact) != 1:
            raise ConnectorError('Message reference is stale or ambiguous')
        return exact[0]

    def _resolve_selector(self, c, message_id='', account='naver', mailbox='INBOX', message_ref='', message_id_header=''):
        if message_id:
            if message_ref or message_id_header:
                raise ConnectorError('Provide one message selector')
            account, mailbox, validity, uid = decode_id(message_id)
            _, current = self.select(c, mailbox)
            if current == 0 or validity != current:
                raise ConnectorError('Mailbox identity changed; search again')
            snapshot = self._snapshot(c, account, mailbox, uid)
            return account, mailbox, uid, snapshot, current
        if not message_ref or not message_id_header:
            raise ConnectorError('Provide a stable message ID or a message reference with Message-ID')
        count, current = self.select(c, mailbox)
        if current != 0:
            raise ConnectorError('Mailbox identity changed; search again')
        uid, snapshot = self._resolve_zero_reference(c, account, mailbox, message_ref, message_id_header, count)
        return account, mailbox, uid, snapshot, current

    def _verify_snapshot(self, c, account, mailbox, uid, original):
        current = self._snapshot(c, account, mailbox, uid)
        if current['raw_header'] != original['raw_header'] or current['size'] != original['size'] or current['internaldate'] != original['internaldate']:
            raise ConnectorError('Message changed during read; search again')
        if original['message_ref'] and (not current['message_ref'] or not hmac.compare_digest(current['message_ref'], original['message_ref'])):
            raise ConnectorError('Message changed during read; search again')
        return current

    def _bodystructure(self, c, uid):
        fields = self._fetch_fields(c, uid, '(UID RFC822.SIZE BODYSTRUCTURE)')
        structure = fields.get(b'BODYSTRUCTURE')
        if not isinstance(structure, list):
            raise ConnectorError('Message missing or unsupported BODYSTRUCTURE')
        try:
            size = bounded_int(int(fields[b'RFC822.SIZE']), 0, 2**63 - 1)
        except (KeyError, ValueError, TypeError):
            raise ConnectorError('Invalid message size metadata') from None
        return size, structure

    def _text_body(self, c, uid, headers, max_chars, expected_size=None):
        size, structure = self._bodystructure(c, uid)
        if expected_size is not None and size != expected_size:
            raise ConnectorError('Message changed during read; search again')
        parts = text_parts(structure)
        parts.sort(key=lambda part: part['type'] != 'plain')
        result = {'headers': headers, 'text': '', 'attachments_downloaded': False, 'content_is_untrusted': True, 'truncated': False}
        if not parts:
            result['note'] = 'No supported non-attachment text part'
            return result
        part = parts[0]
        if not 0 <= part['bytes'] <= MAX_TEXT_PART_BYTES:
            result.update(truncated=True, note='Text part exceeds 256 KiB; body not downloaded')
            return result
        raw = literal(ok(c.uid('FETCH', str(uid), f"(BODY.PEEK[{part['section']}]<0.{MAX_TEXT_PART_BYTES}>)")), MAX_TEXT_PART_BYTES)
        synthetic = (f"Content-Type: text/{part['type']}; charset=\"{part['charset']}\"\r\nContent-Transfer-Encoding: {part['encoding']}\r\n\r\n").encode() + raw
        msg = BytesParser(policy=policy.default).parsebytes(synthetic)
        payload = msg.get_payload(decode=True) or b''
        try:
            body = payload.decode(part['charset'], errors='replace')
        except LookupError:
            body = payload.decode('utf-8', errors='replace')
        if part['type'] == 'html':
            parser = HTMLText()
            parser.feed(body)
            body = ''.join(parser.parts)
        result.update(text=body[:max_chars], truncated=len(body) > max_chars or len(raw) < part['bytes'], selected_part=part['section'], other_text_parts_omitted=len(parts) > 1)
        return result

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
            read_mode = 'reference_revalidated_uidvalidity_zero' if validity == 0 else 'search_and_read'
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
                if validity == 0:
                    snapshot = self._snapshot(c, account, mailbox, uid)
                    message = {
                        'id': None,
                        'message_ref': snapshot['message_ref'],
                        'headers': snapshot['headers'],
                        'headers_may_be_truncated': len(snapshot['raw_header']) == 16384,
                        'body_readable': bool(snapshot['message_ref']),
                    }
                    if not snapshot['message_ref']:
                        message['body_readability_reason'] = 'A unique bounded Message-ID and complete header snapshot are required for safe revalidation.'
                    else:
                        message['message_id_header'] = snapshot['message_id_header']
                        message['read_mode'] = 'same-message-reference-revalidated-per-call'
                else:
                    raw = literal(ok(c.uid('FETCH', str(uid), '(BODY.PEEK[HEADER]<0.16384>)')), 16384)
                    message = {'id': make_id(account, mailbox, validity, uid), 'headers': header_dict(raw), 'headers_may_be_truncated': len(raw) == 16384}
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

    def read_message_by_reference(self, account, mailbox, message_ref, message_id_header, max_chars=12000):
        """Read text using a fresh UID resolved and revalidated inside this IMAP session."""
        bounded_int(max_chars, 1, 30000)
        with self.connect(account) as c:
            count, validity = self.select(c, mailbox)
            if validity != 0:
                raise ConnectorError('Mailbox identity changed; search again')
            uid, snapshot = self._resolve_zero_reference(c, account, mailbox, message_ref, message_id_header, count)
            result = self._text_body(c, uid, snapshot['headers'], max_chars, snapshot['size'])
            self._verify_snapshot(c, account, mailbox, uid, snapshot)
            result.update(
                id=None,
                message_ref=message_ref,
                message_id_header=message_id_header,
                attachments_downloaded=False,
                content_is_untrusted=True,
                read_mode='same-session-uid-with-pre-and-post-header-revalidation',
            )
            return result

    def _attachment_descriptors(self, structure):
        parts = attachment_parts(structure)
        descriptors = []
        for part in parts[:MAX_ATTACHMENT_PARTS]:
            item = dict(part)
            item['server_reported_size_bytes'] = item.pop('size_bytes')
            item['size_basis'] = 'RFC 3501 BODYSTRUCTURE body octets in transfer encoding'
            if item['server_reported_size_bytes'] < 0:
                item['estimated_decoded_size_bytes'] = None
                item['estimate'] = 'server did not report a valid octet count'
            elif item['encoding'] in ('7bit', '8bit', 'binary'):
                item['estimated_decoded_size_bytes'] = item['server_reported_size_bytes']
                item['estimate'] = 'same as reported size for this transfer encoding'
            elif item['encoding'] == 'base64' and item['server_reported_size_bytes'] >= 0:
                item['estimated_decoded_size_bytes'] = (item['server_reported_size_bytes'] * 3) // 4
                item['estimate'] = 'rough estimate; BODYSTRUCTURE size does not expose base64 line wrapping or final padding'
            else:
                item['estimated_decoded_size_bytes'] = None
                item['estimate'] = 'not available without fetching content'
            item['content_is_untrusted'] = True
            descriptors.append(item)
        return descriptors, len(parts) > MAX_ATTACHMENT_PARTS

    @contextmanager
    def _open_selected_message(self, message_id='', account='naver', mailbox='INBOX', message_ref='', message_id_header=''):
        if message_id:
            selected_account, _, _, _ = decode_id(message_id)
        else:
            selected_account = account
        with self.connect(selected_account) as c:
            resolved_account, resolved_mailbox, uid, snapshot, validity = self._resolve_selector(
                c, message_id, account, mailbox, message_ref, message_id_header
            )
            yield c, resolved_account, resolved_mailbox, uid, snapshot, validity

    def list_attachments(self, message_id='', account='naver', mailbox='INBOX', message_ref='', message_id_header=''):
        """List attachment metadata only. No body section is fetched."""
        with self._open_selected_message(message_id, account, mailbox, message_ref, message_id_header) as opened:
            c, account, mailbox, uid, snapshot, _ = opened
            size, structure = self._bodystructure(c, uid)
            if size != snapshot['size']:
                raise ConnectorError('Message changed during read; list attachments again')
            descriptors, truncated = self._attachment_descriptors(structure)
            self._verify_snapshot(c, account, mailbox, uid, snapshot)
            return {
                'message_id': message_id or None,
                'message_ref': message_ref or None,
                'attachments': descriptors,
                'truncated': truncated,
                'content_is_untrusted': True,
            }

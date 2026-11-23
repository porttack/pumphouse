"""
Passkey (WebAuthn) authentication, alongside the legacy HTTP Basic auth.

A request is authenticated if EITHER:
  - it carries a valid passkey session cookie (``ph_pk``), or
  - it carries valid Basic auth credentials (fallback; keeps Scriptable/curl
    working and avoids lockout if every passkey is lost).

State (all under DATA_DIR, shared by the gunicorn workers via files):
  - passkey_secret      signing key for session + challenge cookies (0600)
  - passkeys.json       registered credentials

Registration requires being already authenticated (Basic or passkey), done from
/passkeys. Login is usernameless (discoverable credentials, user verification
required).
"""
import fcntl
import json
import os
import secrets
import time
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from flask import (Blueprint, Response, g, jsonify, redirect, request,
                   render_template_string)
from itsdangerous import BadSignature, URLSafeTimedSerializer
from webauthn import (generate_authentication_options,
                      generate_registration_options, options_to_json,
                      verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                      PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement,
                                      UserVerificationRequirement)

passkeys_bp = Blueprint('passkeys', __name__)

DATA_DIR = Path.home() / '.local/share/pumphouse'
SECRET_FILE = DATA_DIR / 'passkey_secret'
CRED_FILE = DATA_DIR / 'passkeys.json'

# Hosts that may serve passkey login, mapped to their relying-party ID.  A
# passkey is bound to its RP ID; using the parent domain for the
# onblackberryhill.com hosts lets ONE passkey work on the Cloudflare endpoints
# (apex/www via tunnel) and the direct pumphouse. host.  tplinkdns is a
# different domain, so it needs its own passkey.
ALLOWED_RP_IDS = {
    'onblackberryhill.com': 'onblackberryhill.com',
    'www.onblackberryhill.com': 'onblackberryhill.com',
    'pumphouse.onblackberryhill.com': 'onblackberryhill.com',
    'onblackberryhill2.tplinkdns.com': 'onblackberryhill2.tplinkdns.com',
}
RP_NAME = 'Pumphouse'
USER_ID = b'pumphouse-owner'
USER_NAME = 'owner'

SESSION_COOKIE = 'ph_pk'
BASIC_COOKIE = 'ph_pk_basic'      # set after a successful Basic-auth login
BASIC_MAX_AGE = 12 * 3600
CHALLENGE_COOKIE = 'ph_pk_chal'
SESSION_MAX_AGE = 30 * 24 * 3600
CHALLENGE_MAX_AGE = 300


# ── secret + storage ─────────────────────────────────────────────────────────

def _secret():
    """Load the signing key, creating it atomically on first use (2 workers)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return SECRET_FILE.read_text().strip()


_ser = None


def _serializer(salt):
    global _ser
    if _ser is None:
        _ser = _secret()
    return URLSafeTimedSerializer(_ser, salt=salt)


def _load_creds():
    try:
        return json.loads(CRED_FILE.read_text()).get('credentials', [])
    except (FileNotFoundError, ValueError):
        return []


def _mutate_creds(fn):
    """Read-modify-write the credential file under an exclusive lock."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock = DATA_DIR / 'passkeys.lock'
    with open(lock, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        creds = _load_creds()
        result = fn(creds)
        tmp = CRED_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps({'credentials': creds}, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, CRED_FILE)
        return result


# ── auth checks ──────────────────────────────────────────────────────────────

def _basic_ok():
    try:
        from monitor.web import check_auth  # lazy: web imports this module
    except ImportError:
        return False
    a = request.authorization
    return bool(a and check_auth(a.username, a.password))


def _session_ok():
    btok = request.cookies.get(BASIC_COOKIE)
    if btok:
        try:
            _serializer('basic-session').loads(btok, max_age=BASIC_MAX_AGE)
            return True
        except BadSignature:
            pass
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return False
    try:
        data = _serializer('session').loads(tok, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return False
    # Session dies if its credential was removed from /passkeys.
    cid = data.get('cid')
    return any(c['id'] == cid for c in _load_creds())


def is_authed():
    return _session_ok() or _basic_ok()


def requires_auth(f=None, *, page=True):
    """Decorator. page=True redirects browsers to /login; page=False returns a
    401 (JSON-friendly). Basic auth is always accepted."""
    def wrap(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if is_authed():
                g.gated = True
                return fn(*args, **kwargs)
            if page and request.method == 'GET':
                return redirect('/login?next=' + quote(request.full_path.rstrip('?'), safe='/'))
            return Response('Authentication required', 401,
                            {'WWW-Authenticate': 'Basic realm="Pumphouse"'})
        decorated._auth = 'login'     # read by /sitemap
        return decorated
    return wrap(f) if f else wrap


def inline_auth(fn):
    """Marker for views that call is_authed() themselves (no behaviour change);
    lets /sitemap show them as login-protected."""
    fn._auth = 'login'
    return fn


def _set_cookie(resp, name, value, max_age):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=True,
                    samesite='Lax', path='/')


def _rp():
    """(rp_id, origin) for this request, or None if the host isn't allowed."""
    host = request.host.split(':')[0].lower()
    if host not in ALLOWED_RP_IDS:
        return None
    return ALLOWED_RP_IDS[host], f'https://{request.host}'


def _bad_host():
    return jsonify(ok=False, error='Passkeys only work on: ' + ', '.join(sorted(ALLOWED_RP_IDS))), 400


def _safe_next(n):
    return n if n and n.startswith('/') and not n.startswith('//') else '/'


# ── registration (must already be authenticated) ─────────────────────────────

@passkeys_bp.route('/passkey/register/options', methods=['POST'])
@requires_auth(page=False)
def register_options():
    rp = _rp()
    if not rp:
        return _bad_host()
    rp_id, _ = rp
    existing = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(c['id']))
                for c in _load_creds() if c['rp_id'] == rp_id]
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=RP_NAME, user_id=USER_ID, user_name=USER_NAME,
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED))
    resp = Response(options_to_json(opts), mimetype='application/json')
    _set_cookie(resp, CHALLENGE_COOKIE,
                _serializer('chal-reg').dumps(bytes_to_base64url(opts.challenge)),
                CHALLENGE_MAX_AGE)
    return resp


@passkeys_bp.route('/passkey/register/verify', methods=['POST'])
@requires_auth(page=False)
def register_verify():
    rp = _rp()
    if not rp:
        return _bad_host()
    rp_id, origin = rp
    try:
        chal = base64url_to_bytes(_serializer('chal-reg').loads(
            request.cookies.get(CHALLENGE_COOKIE, ''), max_age=CHALLENGE_MAX_AGE))
        body = request.get_json(force=True)
        v = verify_registration_response(
            credential=body['credential'], expected_challenge=chal,
            expected_rp_id=rp_id, expected_origin=origin,
            require_user_verification=True)
    except Exception as e:  # BadSignature, bad JSON, InvalidRegistrationResponse…
        return jsonify(ok=False, error=f'registration failed: {e}'), 400

    name = (body.get('name') or '').strip()[:60] or 'Passkey'
    entry = {
        'id': bytes_to_base64url(v.credential_id),
        'public_key': bytes_to_base64url(v.credential_public_key),
        'sign_count': v.sign_count,
        'rp_id': rp_id,
        'name': name,
        'created': int(time.time()),
        'last_used': None,
    }
    _mutate_creds(lambda cs: cs.append(entry))
    resp = jsonify(ok=True)
    resp.delete_cookie(CHALLENGE_COOKIE, path='/')
    return resp


@passkeys_bp.route('/passkey/delete', methods=['POST'])
@requires_auth(page=False)
def delete_passkey():
    cid = (request.get_json(silent=True) or request.form).get('id')
    _mutate_creds(lambda cs: cs.__setitem__(slice(None), [c for c in cs if c['id'] != cid]))
    return jsonify(ok=True)


# ── login (usernameless) ─────────────────────────────────────────────────────

@passkeys_bp.route('/passkey/login/options', methods=['POST'])
def login_options():
    rp = _rp()
    if not rp:
        return _bad_host()
    opts = generate_authentication_options(
        rp_id=rp[0], user_verification=UserVerificationRequirement.REQUIRED)
    resp = Response(options_to_json(opts), mimetype='application/json')
    _set_cookie(resp, CHALLENGE_COOKIE,
                _serializer('chal-login').dumps(bytes_to_base64url(opts.challenge)),
                CHALLENGE_MAX_AGE)
    return resp


@passkeys_bp.route('/passkey/login/verify', methods=['POST'])
def login_verify():
    rp = _rp()
    if not rp:
        return _bad_host()
    rp_id, origin = rp
    try:
        chal = base64url_to_bytes(_serializer('chal-login').loads(
            request.cookies.get(CHALLENGE_COOKIE, ''), max_age=CHALLENGE_MAX_AGE))
        body = request.get_json(force=True)
        cred_id = body['credential']['id']
        cred = next((c for c in _load_creds()
                     if c['id'] == cred_id and c['rp_id'] == rp_id), None)
        if not cred:
            raise ValueError('unknown passkey')
        v = verify_authentication_response(
            credential=body['credential'], expected_challenge=chal,
            expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=base64url_to_bytes(cred['public_key']),
            credential_current_sign_count=cred['sign_count'],
            require_user_verification=True)
    except Exception as e:
        return jsonify(ok=False, error=f'login failed: {e}'), 401

    def touch(cs):
        for c in cs:
            if c['id'] == cred_id:
                c['sign_count'] = v.new_sign_count
                c['last_used'] = int(time.time())
    _mutate_creds(touch)

    resp = jsonify(ok=True, next=_safe_next(body.get('next')))
    _set_cookie(resp, SESSION_COOKIE,
                _serializer('session').dumps({'cid': cred_id}), SESSION_MAX_AGE)
    resp.delete_cookie(CHALLENGE_COOKIE, path='/')
    return resp


@passkeys_bp.route('/logout')
def logout():
    resp = redirect('/login')
    resp.delete_cookie(SESSION_COOKIE, path='/')
    resp.delete_cookie(BASIC_COOKIE, path='/')
    return resp


# ── pages ────────────────────────────────────────────────────────────────────

_BASE_CSS = """
body{font-family:system-ui,sans-serif;max-width:480px;margin:10vh auto;padding:0 16px;
background:#111;color:#eee}
button{font:inherit;padding:12px 18px;border-radius:8px;border:0;background:#2d7ff9;
color:#fff;width:100%;margin:8px 0;cursor:pointer}
button.sec{background:#333}button.del{background:#8a2b2b;width:auto;padding:6px 12px}
a{color:#7ab0ff}.err{color:#ff8080;min-height:1.4em}
table{width:100%;border-collapse:collapse}td{padding:8px 4px;border-bottom:1px solid #333}
small{color:#999}
"""

_B64 = """
const b64u=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const unb64u=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'==='.slice((s.length+3)%4)),c=>c.charCodeAt(0));
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(async r=>({ok:r.ok,j:await r.json().catch(()=>({}))}));
"""

_LOGIN = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Pumphouse sign in</title><style>{{css}}</style>
<h2>Pumphouse</h2>
<button id=go>Sign in with passkey</button>
<div class=err id=err></div>
<small>No passkey on this device? <a href="/login/basic?next={{next|urlencode}}">Use password</a></small>
<script>{{b64|safe}}
const next={{next|tojson}};
go.onclick=async()=>{err.textContent='';
 try{
  const o=await post('/passkey/login/options');
  if(!o.ok)throw new Error(o.j.error||'options failed');
  const p=o.j;p.challenge=unb64u(p.challenge);
  const c=await navigator.credentials.get({publicKey:p});
  const r=await post('/passkey/login/verify',{next,credential:{id:c.id,rawId:b64u(c.rawId),type:c.type,
   response:{authenticatorData:b64u(c.response.authenticatorData),clientDataJSON:b64u(c.response.clientDataJSON),
   signature:b64u(c.response.signature),userHandle:c.response.userHandle?b64u(c.response.userHandle):null},
   clientExtensionResults:c.getClientExtensionResults()}});
  if(!r.ok)throw new Error(r.j.error||'login failed');
  location.href=r.j.next;
 }catch(e){err.textContent=e.message}};
</script>"""

_MANAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Passkeys</title><style>{{css}}</style>
<h2>Passkeys</h2>
{% if not creds %}<p><b>You're signed in, but no passkey is set up yet.</b><br>
Tap the blue button below. Your browser or phone will then ask for fingerprint, face or PIN to create it.</p>{% endif %}
<table>{% for c in creds %}<tr><td>{{c.name}}<br><small>{{c.rp_id}} · added {{c.created|ts}}
 · {{ 'used ' ~ (c.last_used|ts) if c.last_used else 'never used' }}</small></td>
 <td><button class=del data-id="{{c.id}}">Remove</button></td></tr>
{% else %}<tr><td><small>None registered yet.</small></td></tr>{% endfor %}</table>
<p><input id=name placeholder="Name (e.g. Pixel 9)" style="width:100%;padding:10px;box-sizing:border-box"></p>
<button id=add style="font-size:1.15em;padding:16px">&#128273; Create a passkey on this device</button>
<div class=err id=err></div>
<p><a href="/dashboard">Dashboard</a> · <a href="/logout">Sign out</a></p>
<script>{{b64|safe}}
add.onclick=async()=>{err.textContent='';
 try{
  const o=await post('/passkey/register/options');
  if(!o.ok)throw new Error(o.j.error||'options failed');
  const p=o.j;p.challenge=unb64u(p.challenge);p.user.id=unb64u(p.user.id);
  (p.excludeCredentials||[]).forEach(x=>x.id=unb64u(x.id));
  const c=await navigator.credentials.create({publicKey:p});
  const r=await post('/passkey/register/verify',{name:name.value,credential:{id:c.id,rawId:b64u(c.rawId),type:c.type,
   response:{attestationObject:b64u(c.response.attestationObject),clientDataJSON:b64u(c.response.clientDataJSON),
   transports:c.response.getTransports?c.response.getTransports():[]},clientExtensionResults:c.getClientExtensionResults()}});
  if(!r.ok)throw new Error(r.j.error||'registration failed');
  location.reload();
 }catch(e){err.textContent=e.message}};
document.querySelectorAll('button.del').forEach(b=>b.onclick=async()=>{
 if(confirm('Remove this passkey?')){await post('/passkey/delete',{id:b.dataset.id});location.reload()}});
</script>"""


@passkeys_bp.app_template_filter('ts')
def _ts_filter(t):
    return time.strftime('%Y-%m-%d', time.localtime(t)) if t else ''


@passkeys_bp.route('/login')
def login_page():
    nxt = _safe_next(request.args.get('next'))
    if is_authed():
        # Nothing enrolled yet -> go set one up rather than to the target page.
        return redirect(nxt if _load_creds() else '/passkeys')
    return render_template_string(_LOGIN, css=_BASE_CSS, b64=_B64, next=nxt)


@passkeys_bp.route('/login/basic')
@requires_auth(page=False)
def login_basic():
    """Triggers the browser's Basic-auth prompt, then continues to ``next``."""
    resp = redirect(_safe_next(request.args.get('next')))
    if _basic_ok():
        # Browsers only resend cached Basic creds under this path, so carry the
        # login across pages with a short-lived cookie.
        _set_cookie(resp, BASIC_COOKIE,
                    _serializer('basic-session').dumps('ok'), BASIC_MAX_AGE)
    return resp


@passkeys_bp.route('/passkeys')
@requires_auth
def manage_page():
    return render_template_string(_MANAGE, css=_BASE_CSS, b64=_B64, creds=_load_creds())


def protect_blueprint(bp):
    """Require login for every route in a blueprint (pages redirect to /login,
    everything else gets 401)."""
    @bp.before_request
    def _gate():
        if is_authed():
            g.gated = True
            return None
        if request.method == 'GET' and not request.path.startswith('/api/'):
            return redirect('/login?next=' + quote(request.full_path.rstrip('?'), safe='/'))
        return Response('Authentication required', 401,
                        {'WWW-Authenticate': 'Basic realm="Pumphouse"'})
    bp._auth = 'login'      # read by /sitemap


# ── "full access" = logged in, OR maintenance sharing switched on ────────────
# The owner can flip maintenance sharing from the /water page. While it is ON,
# logged-out visitors get the same full view + controls as a logged-in owner;
# turning it OFF takes that away again immediately (also gates /control/<token>
# and the data APIs, so URLs seen during the window stop working).

MAINT_FILE = DATA_DIR / 'water_full_access.json'


def maint_state():
    """{'enabled': bool, 'since': epoch|None}. Missing/corrupt file = off."""
    try:
        d = json.loads(MAINT_FILE.read_text())
        return {'enabled': bool(d.get('enabled')), 'since': d.get('since')}
    except (FileNotFoundError, ValueError):
        return {'enabled': False, 'since': None}


def set_maint(enabled):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state = {'enabled': bool(enabled), 'since': int(time.time())}
    tmp = MAINT_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state))
    os.replace(tmp, MAINT_FILE)
    return state


def full_access():
    return is_authed() or maint_state()['enabled']


def requires_full_access(f=None, *, page=False):
    """Like requires_auth, but maintenance sharing also lets the request in.
    page=True redirects browsers to /login; otherwise a JSON 401."""
    def wrap(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if full_access():
                g.gated = True
                return fn(*args, **kwargs)
            if page and request.method == 'GET':
                return redirect('/login?next=' + quote(request.full_path.rstrip('?'), safe='/'))
            return jsonify(ok=False, error='authentication required'), 401
        decorated._auth = 'maint'     # read by /sitemap
        return decorated
    return wrap(f) if f else wrap


@passkeys_bp.route('/api/maintenance_access', methods=['GET', 'POST'])
@requires_auth(page=False)
def maintenance_access():
    """Owner-only: read or set whether logged-out visitors get the full /water view."""
    if request.method == 'POST':
        body = request.get_json(silent=True) or {}
        return jsonify(ok=True, **set_maint(bool(body.get('enabled'))))
    return jsonify(ok=True, **maint_state())


# ── tunnel vs direct access ──────────────────────────────────────────────────
# Public pages are served through the Cloudflare tunnel (cloudflared connects to
# Flask from loopback and Cloudflare always adds CF-Ray). Anything else is
# "direct" access (LAN or the DNS-only :6443 hostname) and must log in, except
# the paths below. Requiring the loopback peer as well as CF-Ray means a direct
# visitor can't claim tunnel status by sending a CF-Ray header themselves.

DIRECT_EXEMPT_PREFIXES = ('/api/', '/login', '/logout', '/passkey', '/static/')
DIRECT_EXEMPT_PATHS = ('/ping',)


def is_tunnel_request():
    return bool(request.headers.get('CF-Ray')) and request.remote_addr in ('127.0.0.1', '::1')


def direct_exempt(path):
    """True if this path never needs login even for direct access (APIs used by
    devices/scripts, the login flow itself, uptime ping). Non-API routes that
    require their own login still enforce it via their decorator."""
    return path in DIRECT_EXEMPT_PATHS or path.startswith(DIRECT_EXEMPT_PREFIXES)


def require_login_for_direct_access(app):
    """Register the app-wide gate: non-API pages need login unless they arrive
    through the Cloudflare tunnel."""
    @app.before_request
    def _direct_gate():
        if is_tunnel_request() or direct_exempt(request.path) or is_authed():
            return None
        if request.method == 'GET':
            return redirect('/login?next=' + quote(request.full_path.rstrip('?'), safe='/'))
        return Response('Authentication required', 401,
                        {'WWW-Authenticate': 'Basic realm="Pumphouse"'})


# ── signed, expiring links (for ntfy attachments) ────────────────────────────
# ntfy's servers fetch notification images and can't log in, so those URLs carry
# an HMAC signature + expiry instead.

def sign_path(path, ttl=24 * 3600):
    """Return path + ?e=<expiry>&s=<sig>, valid for ttl seconds."""
    import hashlib
    import hmac
    exp = int(time.time()) + ttl
    sig = hmac.new(_secret().encode(), f'{path}|{exp}'.encode(), hashlib.sha256).hexdigest()[:32]
    return f'{path}?e={exp}&s={sig}'


def valid_signature(path):
    import hashlib
    import hmac
    try:
        exp = int(request.args.get('e', ''))
        sig = request.args.get('s', '')
    except ValueError:
        return False
    if exp < time.time():
        return False
    want = hmac.new(_secret().encode(), f'{path}|{exp}'.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, want)


def no_shared_cache_for_gated(app):
    """Responses from login/maintenance-gated routes must never be stored by a
    shared cache. Cloudflare caches .jpg/.wav/.mp4 URLs by extension and would
    otherwise serve an owner's (authorized) response to anonymous visitors."""
    @app.after_request
    def _private(resp):
        if getattr(g, 'gated', False):
            cc = resp.headers.get('Cache-Control', '')
            if not cc or 'no-store' not in cc:
                resp.headers['Cache-Control'] = (
                    cc.replace('public', 'private') if 'max-age' in cc else 'private, no-store')
                if 'private' not in resp.headers['Cache-Control']:
                    resp.headers['Cache-Control'] += ', private'
        return resp

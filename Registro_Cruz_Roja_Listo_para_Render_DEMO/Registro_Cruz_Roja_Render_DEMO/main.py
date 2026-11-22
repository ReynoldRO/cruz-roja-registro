#!/usr/bin/env python3
"""Registro de turnos QR para móvil, con SQLite y servidor HTTP estándar."""
from __future__ import annotations
import base64
import csv
import datetime as dt
import hashlib
import hmac
import html
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent
INSTANCE = Path(os.environ.get('INSTANCE_DIR', ROOT / 'instance'))
INSTANCE.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = INSTANCE / 'config.json'
DB_PATH = INSTANCE / 'marcaciones.sqlite3'
SPLINE_URL = os.environ.get('SPLINE_URL', 'https://prod.spline.design/o7iHSd2VqAQyrOFn/scene.splinecode')


def load_config():
    if CONFIG_PATH.is_file():
        current = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    else:
        current = {
            'qr_token': secrets.token_urlsafe(30),
            'admin_password': secrets.token_urlsafe(14),
            'cookie_secret': secrets.token_hex(32),
        }
        CONFIG_PATH.write_text(json.dumps(current, indent=2), encoding='utf-8')
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
    return {
        'qr_token': os.environ.get('QR_TOKEN') or current['qr_token'],
        'admin_password': os.environ.get('ADMIN_PASSWORD') or current['admin_password'],
        'cookie_secret': os.environ.get('COOKIE_SECRET') or current['cookie_secret'],
    }


CONFIG = load_config()
SESSION_SECONDS = 60 * 60 * 8
SCAN_SECONDS = 60 * 15
TZ_UTC = dt.timezone.utc
try:
    TZ_LOCAL = ZoneInfo('America/Costa_Rica')  # Hora local para informes mensuales
except ZoneInfoNotFoundError:
    # Windows puede no incluir la base de zonas IANA; Costa Rica utiliza UTC-6.
    TZ_LOCAL = dt.timezone(dt.timedelta(hours=-6), name='America/Costa_Rica')
HTTP_PORT = int(os.environ.get('PORT', '8765'))
# Versión de demostración: acceso directo sin QR, limitado a redes privadas.
# En implementación real, configurar MODO_PRUEBA=0 y activar el flujo con QR.
MODO_PRUEBA = os.environ.get('MODO_PRUEBA', '1').lower() in {'1', 'true', 'si', 'yes'}
# Demostración pública sin QR: facilita pruebas desde celulares fuera del Wi-Fi local.
# NO USAR CON DATOS REALES NI COMO CONTROL OFICIAL DE ASISTENCIA.
MODO_DEMO_WEB = os.environ.get('MODO_DEMO_WEB', '0').lower() in {'1', 'true', 'si', 'yes'}


def utc_now():
    return dt.datetime.now(TZ_UTC)


def iso_utc(now=None):
    return (now or utc_now()).isoformat(timespec='seconds')


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 15000')
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    with db() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS operarios (
                codigo TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                apellido TEXT NOT NULL,
                creado_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turnos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL REFERENCES operarios(codigo),
                entrada_utc TEXT NOT NULL,
                entrada_celular TEXT NOT NULL,
                entrada_zona TEXT NOT NULL,
                salida_utc TEXT,
                salida_celular TEXT,
                salida_zona TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_turno_abierto
                ON turnos(codigo) WHERE salida_utc IS NULL;
            CREATE INDEX IF NOT EXISTS ix_turnos_entrada ON turnos(entrada_utc DESC);
            CREATE TABLE IF NOT EXISTS intentos_admin (
                ip TEXT NOT NULL, fallo_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_intentos_admin ON intentos_admin(ip, fallo_utc);
            CREATE TABLE IF NOT EXISTS sesiones (
                sid_hash TEXT PRIMARY KEY,
                administrador INTEGER NOT NULL DEFAULT 0,
                permiso_escaneo_hasta TEXT,
                csrf TEXT NOT NULL,
                expira_utc TEXT NOT NULL
            );
        ''')


def session_hash(sid):
    return hashlib.sha256(sid.encode('utf-8')).hexdigest()


def secure_cookie_value(sid):
    digest = hmac.new(CONFIG['cookie_secret'].encode('utf-8'), sid.encode('utf-8'), hashlib.sha256).hexdigest()
    return sid + '.' + digest


def verify_cookie(raw):
    if not raw or '.' not in raw:
        return None
    sid, mac = raw.rsplit('.', 1)
    if len(sid) > 128 or not re.fullmatch(r'[A-Za-z0-9_-]{20,128}', sid):
        return None
    if not hmac.compare_digest(mac, secure_cookie_value(sid).rsplit('.', 1)[1]):
        return None
    return sid


def new_session(conn, is_admin=False, grant=False):
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = utc_now()
    grant_until = iso_utc(now + dt.timedelta(seconds=SCAN_SECONDS)) if grant else None
    conn.execute('''INSERT INTO sesiones(sid_hash, administrador, permiso_escaneo_hasta, csrf, expira_utc)
      VALUES (?, ?, ?, ?, ?)''', (session_hash(sid), int(is_admin), grant_until, csrf,
                                  iso_utc(now + dt.timedelta(seconds=SESSION_SECONDS))))
    return sid


def get_session(conn, sid):
    if not sid:
        return None
    row = conn.execute('SELECT * FROM sesiones WHERE sid_hash=?', (session_hash(sid),)).fetchone()
    if not row or row['expira_utc'] <= iso_utc():
        return None
    return row


def valid_scan(session):
    return bool(session and session['permiso_escaneo_hasta']
                and session['permiso_escaneo_hasta'] > iso_utc())


def valid_local_timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        stamp = dt.datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            return False
        # Es solo informativo: nunca se usa como hora oficial de registro.
        return 2020 <= stamp.year <= 2100
    except ValueError:
        return False


def safe_name(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-zÀ-ÿÑñ\s'.-]{2,70}", value.strip()))


def safe_code(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{2,25}', value.strip()))


def csv_cell(value):
    value = str(value or '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


def mes_actual():
    return utc_now().astimezone(TZ_LOCAL).strftime('%Y-%m')


def mes_validado(value):
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', value or ''):
        return mes_actual()
    return value


def limites_mes_utc(mes):
    anio, mes_num = map(int, mes.split('-'))
    inicio = dt.datetime(anio, mes_num, 1, tzinfo=TZ_LOCAL)
    if mes_num == 12:
        fin = dt.datetime(anio + 1, 1, 1, tzinfo=TZ_LOCAL)
    else:
        fin = dt.datetime(anio, mes_num + 1, 1, tzinfo=TZ_LOCAL)
    return inicio.astimezone(TZ_UTC), fin.astimezone(TZ_UTC)


def fecha_utc(value):
    instante = dt.datetime.fromisoformat(value)
    if instante.tzinfo is None:
        raise ValueError('La fecha del servidor debe incluir zona horaria')
    return instante.astimezone(TZ_UTC)


def formato_horas(segundos):
    minutos = max(0, int((segundos + 30) // 60))
    return f'{minutos // 60} h {minutos % 60:02d} min'


def formato_local(value):
    if not value:
        return 'Pendiente'
    return fecha_utc(value).astimezone(TZ_LOCAL).strftime('%d/%m/%Y %H:%M')


def horas_mes(conn, mes):
    inicio, fin = limites_mes_utc(mes)
    operarios = conn.execute("""SELECT o.*, EXISTS(SELECT 1 FROM turnos t
        WHERE t.codigo=o.codigo AND t.salida_utc IS NULL) AS presente
        FROM operarios o ORDER BY o.apellido COLLATE NOCASE, o.nombre COLLATE NOCASE""").fetchall()
    turnos = conn.execute("""SELECT id, codigo, entrada_utc, salida_utc
        FROM turnos WHERE entrada_utc < ? AND (salida_utc > ? OR salida_utc IS NULL)
        ORDER BY entrada_utc DESC""", (iso_utc(fin), iso_utc(inicio))).fetchall()
    acumulados = {o['codigo']: {'segundos': 0., 'turnos': 0, 'abiertos': 0} for o in operarios}
    detalle = {}
    for t in turnos:
        total = acumulados[t['codigo']]
        if not t['salida_utc']:
            total['abiertos'] += 1
            detalle.setdefault(t['codigo'], []).append((t, 0.))
            continue
        inicio_turno = fecha_utc(t['entrada_utc'])
        fin_turno = fecha_utc(t['salida_utc'])
        segundos = max(0., (min(fin_turno, fin) - max(inicio_turno, inicio)).total_seconds())
        if segundos > 0:
            total['segundos'] += segundos
            total['turnos'] += 1
            detalle.setdefault(t['codigo'], []).append((t, segundos))
    return operarios, acumulados, detalle


def render(name, **vars):
    body = (ROOT / 'templates' / name).read_text(encoding='utf-8')
    for k, v in vars.items():
        body = body.replace('@@' + k + '@@', html.escape(str(v), quote=True))
    return body.encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    server_version = 'RegistroQR/1.0'

    def log_message(self, fmt, *args):
        # No registrar rutas de QR con su token.
        path = self.path.split('?', 1)[0]
        safe = '/q/[oculto]' if path.startswith('/q/') else path
        print('%s - %s %s' % (self.address_string(), self.command, safe))

    def headers_done(self, status=200, mime='text/html; charset=utf-8',
                     cookie_sid=None, file_name=None, extra=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        if cookie_sid:
            attributes = ['HttpOnly', 'SameSite=Lax', 'Path=/', f'Max-Age={SESSION_SECONDS}']
            if os.environ.get('COOKIE_SECURE', '').lower() in {'1', 'true', 'yes'}:
                attributes.append('Secure')
            self.send_header('Set-Cookie', 'rqrs=' + secure_cookie_value(cookie_sid) + '; ' + '; '.join(attributes))
        if file_name:
            self.send_header('Content-Disposition', f'attachment; filename="{file_name}"')
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()

    def respond(self, payload, status=200, mime='text/html; charset=utf-8', **kwargs):
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        self.headers_done(status, mime, **kwargs)
        self.wfile.write(payload)

    def json_response(self, obj, status=200):
        self.respond(json.dumps(obj, ensure_ascii=False), status, 'application/json; charset=utf-8')

    def redirect(self, url, sid=None):
        self.headers_done(303, cookie_sid=sid, extra={'Location': url})

    def es_celular(self):
        ua = self.headers.get('User-Agent', '')
        return bool(re.search(r'Android|iPhone|iPod|iPad|Mobile|Windows Phone', ua, re.I))

    def cliente_de_red_privada(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            return ip.is_loopback or ip.is_private
        except ValueError:
            return False

    def acceso_directo_permitido(self):
        # Acceso local de pruebas; no verifica identidad ni presencia física.
        local = MODO_PRUEBA and self.cliente_de_red_privada() and (
            self.es_celular() or self.client_address[0] in {'127.0.0.1', '::1'}
        )
        # Publicación de demostración (Render u otro host): solo navegador móvil.
        # El User-Agent se puede falsificar. Nunca usar con personal real.
        return local or (MODO_DEMO_WEB and self.es_celular())

    def puede_marcar(self):
        return self.es_celular() or self.acceso_directo_permitido()

    def session_id(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            return None
        return verify_cookie(cookie['rqrs'].value) if 'rqrs' in cookie else None

    def form_data(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length < 1 or length > 10240:
            raise ValueError('El formulario excede el tamaño permitido.')
        raw = self.rfile.read(length)
        if self.headers.get('Content-Type', '').startswith('application/json'):
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('JSON inválido.')
            return data
        return {k: v[-1] for k, v in parse_qs(raw.decode('utf-8'), keep_blank_values=True).items()}

    def require_json_scan(self, conn, data):
        sid = self.session_id()
        session = get_session(conn, sid)
        if not valid_scan(session):
            self.json_response({'ok': False, 'error': 'Escaneá nuevamente el QR para continuar.'}, 403)
            return None
        if data.get('csrf') != session['csrf']:
            self.json_response({'ok': False, 'error': 'Sesión inválida. Volvé a escanear el QR.'}, 403)
            return None
        return session

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith('/static/'):
            return self.static(path)
        if path == '/':
            # Acceso directo de demostración desde celular en Wi-Fi local
            # (o desde localhost de la PC para desarrollo).
            if self.acceso_directo_permitido():
                with db() as conn:
                    sid = self.session_id()
                    session = get_session(conn, sid)
                    if session:
                        conn.execute('UPDATE sesiones SET permiso_escaneo_hasta=? WHERE sid_hash=?',
                                     (iso_utc(utc_now() + dt.timedelta(seconds=SCAN_SECONDS)), session_hash(sid)))
                    else:
                        sid = new_session(conn, grant=True)
                return self.redirect('/registro', sid)
            return self.respond(render('landing.html'))
        if path.startswith('/q/'):
            if not self.puede_marcar():
                return self.respond(render('solo-celular.html'), 403)
            token = path[len('/q/'):]
            if not hmac.compare_digest(token, CONFIG['qr_token']):
                return self.respond(render('landing.html', ERROR='Código QR inválido.'), 404)
            with db() as conn:
                sid = self.session_id()
                session = get_session(conn, sid)
                if session:
                    conn.execute('UPDATE sesiones SET permiso_escaneo_hasta=? WHERE sid_hash=?',
                                 (iso_utc(utc_now() + dt.timedelta(seconds=SCAN_SECONDS)), session_hash(sid)))
                else:
                    sid = new_session(conn, grant=True)
            return self.redirect('/registro', sid)
        if path == '/registro':
            if not self.puede_marcar():
                return self.respond(render('solo-celular.html'), 403)
            with db() as conn:
                session = get_session(conn, self.session_id())
            if not valid_scan(session):
                return self.redirect('/')
            return self.respond(render('registro.html', CSRF=session['csrf'], SPLINE_URL=SPLINE_URL,
                                       SCAN_STATUS=('ACCESO DIRECTO · DEMO' if self.acceso_directo_permitido()
                                                    else 'Sesión de QR activa')))
        if path == '/admin':
            with db() as conn:
                session = get_session(conn, self.session_id())
                if not session or not session['administrador']:
                    return self.respond(render('admin-login.html', ERROR=''))
                query = parse_qs(urlparse(self.path).query)
                mes = mes_validado(query.get('mes', [mes_actual()])[0])
                seleccionado = query.get('operario', [''])[0].strip().upper()
                rows = conn.execute("""SELECT t.*, o.nombre, o.apellido FROM turnos t
                    JOIN operarios o ON t.codigo=o.codigo ORDER BY t.id DESC LIMIT 150""").fetchall()
                total = conn.execute('SELECT COUNT(*) FROM turnos').fetchone()[0]
                presentes = conn.execute('SELECT COUNT(*) FROM turnos WHERE salida_utc IS NULL').fetchone()[0]
                operarios, acumulados, detalle = horas_mes(conn, mes)
            host = self.headers.get('Host', 'localhost:8765')
            protocol = 'https' if os.environ.get('COOKIE_SECURE', '') == '1' else 'http'
            base_url = os.environ.get('PUBLIC_BASE_URL') or f'{protocol}://{host}'
            table_rows = ''.join('<tr>' + ''.join('<td>' + html.escape(str(v or '—')) + '</td>' for v in (
                r['codigo'], r['nombre'] + ' ' + r['apellido'], r['entrada_celular'],
                r['salida_celular'] or 'SIN SALIDA', r['entrada_utc'], r['salida_utc'] or '—')) + '</tr>' for r in rows)
            if not table_rows:
                table_rows = '<tr><td colspan="6" class="empty">Aún no hay registros.</td></tr>'
            links = []
            for o in operarios:
                a = acumulados[o['codigo']]
                encoded = urlencode({'mes': mes, 'operario': o['codigo']})
                nombre_completo = html.escape(o['nombre'] + ' ' + o['apellido'])
                codigo = html.escape(o['codigo'])
                estado = ('<span class="pill pill-open">En turno</span>' if o['presente']
                          else '<span class="pill pill-off">Fuera de turno</span>')
                links.append(f'<tr data-persona="{codigo} {nombre_completo}">'
                             f'<td><a class="person-link" href="/admin?{encoded}#detalle-operario">{codigo}</a></td>'
                             f'<td>{nombre_completo}</td><td>{estado}</td>'
                             f'<td>{a["turnos"]}</td><td><strong>{formato_horas(a["segundos"])}</strong></td>'
                             f'<td><a class="detail-link" href="/admin?{encoded}#detalle-operario">Ver detalle →</a></td></tr>')
            person_rows = ''.join(links) or '<tr><td colspan="6" class="empty">Todavía no hay operarios registrados.</td></tr>'
            detalle_html = '<div class="empty-detail">Seleccioná un operario de la lista para revisar sus turnos del mes.</div>'
            if seleccionado and seleccionado in acumulados:
                op = next(o for o in operarios if o['codigo'] == seleccionado)
                a = acumulados[seleccionado]
                tr = []
                for turno, segundos in detalle.get(seleccionado, []):
                    estado = 'Abierto · no contabilizado' if not turno['salida_utc'] else formato_horas(segundos)
                    tr.append('<tr><td>' + html.escape(formato_local(turno['entrada_utc'])) +
                              '</td><td>' + html.escape(formato_local(turno['salida_utc'])) +
                              '</td><td>' + html.escape(estado) + '</td></tr>')
                contenido = ''.join(tr) or '<tr><td colspan="3" class="empty">Sin turnos en este mes.</td></tr>'
                detalle_html = (f'<div class="person-detail-head"><div><span class="section-kicker">PERFIL / {html.escape(seleccionado)}</span>'
                                f'<h3>{html.escape(op["nombre"] + " " + op["apellido"])}</h3></div>'
                                f'<div class="detail-total"><small>HORAS DEL MES</small><strong>{formato_horas(a["segundos"])}</strong></div></div>'
                                f'<p class="detail-note">{a["turnos"]} turno(s) cerrado(s) en el mes. '
                                'Los turnos abiertos no se suman hasta registrar la salida.</p>'
                                '<div class="table-scroller"><table class="details-table"><thead><tr>'
                                '<th>Entrada · Costa Rica</th><th>Salida · Costa Rica</th><th>Horas del mes</th>'
                                '</tr></thead><tbody>' + contenido + '</tbody></table></div>')
            total_segundos = sum(a['segundos'] for a in acumulados.values())
            raw = render('admin.html', BASE_URL=base_url, CSRF=session['csrf'],
                         TOTAL=str(total), PRESENTES=str(presentes), TOTAL_PERSONAS=str(len(operarios)),
                         TOTAL_HORAS=formato_horas(total_segundos), MES=mes,
                         ROWS='__ROWS__', PERSON_ROWS='__PERSON_ROWS__', PERSON_DETAIL='__PERSON_DETAIL__')
            raw = raw.replace(b'__ROWS__', table_rows.encode('utf-8'))
            raw = raw.replace(b'__PERSON_ROWS__', person_rows.encode('utf-8'))
            raw = raw.replace(b'__PERSON_DETAIL__', detalle_html.encode('utf-8'))
            return self.respond(raw)
        if path == '/admin/horas.csv':
            with db() as conn:
                s = get_session(conn, self.session_id())
                if not s or not s['administrador']:
                    return self.respond('No autorizado', 403)
                mes = mes_validado(parse_qs(urlparse(self.path).query).get('mes', [mes_actual()])[0])
                operarios, acumulados, _ = horas_mes(conn, mes)
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Mes (Costa Rica)', 'Código', 'Nombre', 'Apellido',
                             'Turnos cerrados', 'Minutos trabajados', 'Horas registradas', 'Estado'])
            for op in operarios:
                a = acumulados[op['codigo']]
                minutos = max(0, int((a['segundos'] + 30) // 60))
                writer.writerow([mes, csv_cell(op['codigo']), csv_cell(op['nombre']), csv_cell(op['apellido']),
                                 a['turnos'], minutos, formato_horas(a['segundos']),
                                 'En turno' if op['presente'] else 'Fuera de turno'])
            return self.respond('\ufeff' + output.getvalue(), mime='text/csv; charset=utf-8',
                                file_name=f'horas-personal-{mes}.csv')
        if path in {'/admin/qr.png', '/admin/qr-link'}:
            with db() as conn:
                s = get_session(conn, self.session_id())
            if not s or not s['administrador']:
                return self.respond('No autorizado', 403)
            base = parse_qs(urlparse(self.path).query).get('base', [os.environ.get('PUBLIC_BASE_URL', '')])[0].strip().rstrip('/')
            parsed = urlparse(base)
            if not (parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username
                    and not parsed.password and len(base) <= 250 and not parsed.path.strip('/')
                    and not parsed.query and not parsed.fragment):
                return self.respond('Ingresá una URL base válida: http://IP:8765 o https://dominio', 400)
            url = base + '/q/' + CONFIG['qr_token']
            if path == '/admin/qr-link':
                return self.json_response({'url': url})
            try:
                import qrcode
                img = qrcode.make(url, box_size=9, border=3)
                result = io.BytesIO()
                img.save(result, format='PNG')
                return self.respond(result.getvalue(), mime='image/png')
            except ImportError:
                return self.respond('Falta dependencia qrcode: pip install "qrcode[pil]"', 503)
        if path == '/admin/export.csv':
            with db() as conn:
                s = get_session(conn, self.session_id())
                if not s or not s['administrador']:
                    return self.respond('No autorizado', 403)
                rows = conn.execute('''SELECT t.*, o.nombre, o.apellido FROM turnos t
                    JOIN operarios o ON t.codigo=o.codigo ORDER BY t.id DESC''').fetchall()
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Codigo operario', 'Nombre', 'Apellido', 'Entrada celular', 'Zona entrada',
                'Entrada servidor UTC', 'Salida celular', 'Zona salida', 'Salida servidor UTC'])
            for r in rows:
                writer.writerow([csv_cell(r[k]) for k in ('codigo', 'nombre', 'apellido', 'entrada_celular',
                  'entrada_zona', 'entrada_utc', 'salida_celular', 'salida_zona', 'salida_utc')])
            return self.respond('\ufeff' + output.getvalue(), mime='text/csv; charset=utf-8',
                                file_name='marcaciones-cruz-roja.csv')
        return self.respond('Página no encontrada', 404)

    def static(self, path):
        file = (ROOT / path.lstrip('/')).resolve()
        static_dir = (ROOT / 'static').resolve()
        if static_dir not in file.parents or not file.is_file():
            return self.respond('No encontrado', 404)
        kind = {'.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                '.svg': 'image/svg+xml'}.get(file.suffix, 'application/octet-stream')
        return self.respond(file.read_bytes(), mime=kind)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.form_data()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return self.json_response({'ok': False, 'error': str(exc)}, 400)
        if path == '/admin/login':
            password = str(data.get('password', ''))
            ip = self.client_address[0]
            with db() as conn:
                limite = iso_utc(utc_now() - dt.timedelta(minutes=15))
                conn.execute('DELETE FROM intentos_admin WHERE fallo_utc < ?', (limite,))
                fallos = conn.execute('SELECT COUNT(*) FROM intentos_admin WHERE ip=?', (ip,)).fetchone()[0]
                if fallos >= 8:
                    return self.respond(render('admin-login.html', ERROR='Demasiados intentos. Intentá de nuevo en 15 minutos.'), 429)
                if not hmac.compare_digest(password, CONFIG['admin_password']):
                    conn.execute('INSERT INTO intentos_admin VALUES (?, ?)', (ip, iso_utc()))
                    return self.respond(render('admin-login.html', ERROR='Contraseña incorrecta.'), 401)
                conn.execute('DELETE FROM intentos_admin WHERE ip=?', (ip,))
                sid = self.session_id()
                s = get_session(conn, sid)
                if s:
                    conn.execute('UPDATE sesiones SET administrador=1 WHERE sid_hash=?', (session_hash(sid),))
                else:
                    sid = new_session(conn, is_admin=True)
            return self.redirect('/admin', sid)
        if path == '/admin/logout':
            with db() as conn:
                sid = self.session_id()
                s = get_session(conn, sid)
                if s and s['administrador'] and data.get('csrf') == s['csrf']:
                    conn.execute('DELETE FROM sesiones WHERE sid_hash=?', (session_hash(sid),))
            return self.redirect('/admin')
        if path in {'/api/estado', '/api/registrar'} and not self.puede_marcar():
            return self.json_response({'ok': False, 'error': 'El registro solo está disponible desde celulares.'}, 403)
        if path not in {'/api/estado', '/api/registrar'}:
            return self.json_response({'ok': False, 'error': 'Ruta no disponible.'}, 404)
        with db() as conn:
            s = self.require_json_scan(conn, data)
            if not s:
                return
            codigo = str(data.get('codigo', '')).strip().upper()
            if not safe_code(codigo):
                return self.json_response({'ok': False, 'error': 'El código debe tener de 2 a 25 letras o números.'}, 422)
            if path == '/api/estado':
                op = conn.execute('SELECT nombre, apellido FROM operarios WHERE codigo=?', (codigo,)).fetchone()
                turno = conn.execute('''SELECT entrada_celular, entrada_utc FROM turnos
                    WHERE codigo=? AND salida_utc IS NULL''', (codigo,)).fetchone()
                return self.json_response({'ok': True, 'accion': 'salida' if turno else 'entrada',
                    'nombre': op['nombre'] if op else '', 'apellido': op['apellido'] if op else '',
                    'entrada': turno['entrada_celular'] if turno else None})
            nombre = str(data.get('nombre', '')).strip()
            apellido = str(data.get('apellido', '')).strip()
            reloj = data.get('hora_celular')
            zona = str(data.get('zona', 'Desconocida'))[:80]
            if not safe_name(nombre) or not safe_name(apellido):
                return self.json_response({'ok': False, 'error': 'Ingresá nombre y apellido válidos.'}, 422)
            if not valid_local_timestamp(reloj):
                return self.json_response({'ok': False, 'error': 'No se recibió una fecha válida del celular.'}, 422)
            with conn:  # Transacción atómica: no perder entrada/salida ni permiso.
                conn.execute('BEGIN IMMEDIATE')
                # Revalidar sesión tras adquirir el bloqueo de escritura.
                s = get_session(conn, self.session_id())
                if not valid_scan(s) or data.get('csrf') != s['csrf']:
                    return self.json_response({'ok': False, 'error': 'Escaneá nuevamente el QR.'}, 403)
                op = conn.execute('SELECT * FROM operarios WHERE codigo=?', (codigo,)).fetchone()
                turno = conn.execute('SELECT id FROM turnos WHERE codigo=? AND salida_utc IS NULL', (codigo,)).fetchone()
                ahora = iso_utc()
                if op and (op['nombre'].casefold() != nombre.casefold() or op['apellido'].casefold() != apellido.casefold()):
                    return self.json_response({'ok': False, 'error': 'Este código pertenece a otro nombre registrado.'}, 409)
                if not op:
                    conn.execute('INSERT INTO operarios VALUES (?, ?, ?, ?)', (codigo, nombre, apellido, ahora))
                if turno:
                    conn.execute('UPDATE turnos SET salida_utc=?, salida_celular=?, salida_zona=? WHERE id=?',
                                 (ahora, reloj, zona, turno['id']))
                    accion = 'salida'
                else:
                    conn.execute('''INSERT INTO turnos(codigo, entrada_utc, entrada_celular, entrada_zona)
                        VALUES (?, ?, ?, ?)''', (codigo, ahora, reloj, zona))
                    accion = 'entrada'
                conn.execute('UPDATE sesiones SET permiso_escaneo_hasta=NULL WHERE sid_hash=?',
                             (session_hash(self.session_id()),))
            return self.json_response({'ok': True, 'accion': accion, 'nombre': nombre,
                                      'codigo': codigo, 'hora_celular': reloj, 'hora_servidor_utc': ahora})


def start():
    init_db()
    host = os.environ.get('HOST', '0.0.0.0')
    print('\n' + '=' * 57)
    print('  REGISTRO DE PERSONAL · CRUZ ROJA (prototipo)')
    print(f'  Ingresar en esta PC: http://localhost:{HTTP_PORT}')
    print(f'  Panel administrativo: http://localhost:{HTTP_PORT}/admin')
    print(f'  Contraseña admin: {CONFIG["admin_password"]}')
    if MODO_PRUEBA:
        print('  DEMO LOCAL SIN QR: formulario directo en http://localhost:%d' % HTTP_PORT)
        print('  CELULAR LOCAL: http://IP-DE-TU-PC:%d (ambos en misma red Wi-Fi)' % HTTP_PORT)
    if MODO_DEMO_WEB:
        print('  DEMO WEB SIN QR: acceso directo desde celulares usando la URL pública HTTPS.')
        print('  ADVERTENCIA: cualquiera con el enlace puede suplantar códigos de operario.')
        print('  NO USAR CON DATOS REALES NI PARA ASISTENCIA INSTITUCIONAL.')
    print('  IMPORTANTE: usar una IP de red o URL HTTPS para el QR móvil.')
    print('  Claves locales guardadas en instance/config.json (NO compartir).')
    print('=' * 57 + '\n')
    ThreadingHTTPServer((host, HTTP_PORT), Handler).serve_forever()


if __name__ == '__main__':
    start()

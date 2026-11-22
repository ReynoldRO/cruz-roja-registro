"""Pruebas de integración del flujo real sobre HTTP; no requieren pytest."""
import http.cookiejar
import json
import os
from pathlib import Path
import re
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib import error, parse, request

PROJECT = Path(__file__).resolve().parents[1]


def opener(mobile=False):
    client = request.build_opener(request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if mobile:
        client.addheaders = [('User-Agent', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1')]
    return client


def get(client, url):
    with client.open(url, timeout=4) as r:
        return r.read(), r.status


def post(client, url, data, json_type=True):
    if json_type:
        content = json.dumps(data).encode()
        content_type = 'application/json'
    else:
        content = parse.urlencode(data).encode()
        content_type = 'application/x-www-form-urlencoded'
    req = request.Request(url, data=content, headers={'Content-Type': content_type})
    try:
        with client.open(req, timeout=5) as r:
            raw = r.read()
            return (json.loads(raw) if json_type else raw), r.status
    except error.HTTPError as exc:
        raw = exc.read()
        return (json.loads(raw) if json_type else raw), exc.code


class RegistroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.base = f'http://127.0.0.1:{cls.port}'
        env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(cls.port), 'INSTANCE_DIR': cls.tempdir.name, 'MODO_PRUEBA': '0'}
        cls.proc = subprocess.Popen([sys.executable, '-u', str(PROJECT / 'main.py')], cwd=str(PROJECT), env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        config = Path(cls.tempdir.name) / 'config.json'
        for _ in range(60):
            if config.exists():
                try:
                    get(opener(), cls.base + '/')
                    break
                except OSError:
                    pass
            time.sleep(.1)
        else:
            raise RuntimeError('Servidor no inició')
        cls.config = json.loads(config.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.tempdir.cleanup()

    def scan(self, client):
        body, status = get(client, self.base + '/q/' + self.config['qr_token'])
        self.assertEqual(status, 200)
        body = body.decode()
        self.assertIn('id="turno-form"', body)
        return re.search(r'data-csrf="([^"]+)"', body).group(1)

    def test_full_cycle_across_two_cellphones_and_admin(self):
        phone1, phone2, administrator = opener(True), opener(True), opener()
        body, _ = get(phone1, self.base + '/registro')
        self.assertNotIn(b'id="turno-form"', body)
        csrf = self.scan(phone1)
        p = {'csrf': csrf, 'codigo': 'C-472', 'nombre': 'Ana', 'apellido': 'Solano',
             'hora_celular': '2026-11-22T14:30:00-06:00', 'zona': 'America/Costa_Rica'}
        state, code = post(phone1, self.base + '/api/estado', {'codigo': p['codigo'], 'csrf': csrf})
        self.assertEqual(code, 200)
        self.assertEqual(state['accion'], 'entrada')
        result, code = post(phone1, self.base + '/api/registrar', p)
        self.assertEqual(code, 200)
        self.assertEqual(result['accion'], 'entrada')
        _, code = post(phone1, self.base + '/api/registrar', p)
        self.assertEqual(code, 403, 'No se debe permitir marcar dos veces con un solo escaneo')

        csrf2 = self.scan(phone2)
        state, code = post(phone2, self.base + '/api/estado', {'codigo': p['codigo'], 'csrf': csrf2})
        self.assertEqual(code, 200)
        self.assertEqual(state['accion'], 'salida')
        self.assertEqual(state['nombre'], 'Ana')
        p['csrf'] = csrf2
        p['hora_celular'] = '2026-11-22T17:00:00-06:00'
        result, code = post(phone2, self.base + '/api/registrar', p)
        self.assertEqual(code, 200)
        self.assertEqual(result['accion'], 'salida')

        csrf3 = self.scan(phone1)
        state, _ = post(phone1, self.base + '/api/estado', {'codigo': p['codigo'], 'csrf': csrf3})
        self.assertEqual(state['accion'], 'entrada')
        p.update(csrf=csrf3, nombre='Otra')
        _, code = post(phone1, self.base + '/api/registrar', p)
        self.assertEqual(code, 409, 'Un código no puede usarse con otros nombres')

        _, code = post(administrator, self.base + '/admin/login', {'password': 'NO'}, False)
        self.assertEqual(code, 401)
        html_body, code = post(administrator, self.base + '/admin/login',
                               {'password': self.config['admin_password']}, False)
        self.assertEqual(code, 200)
        self.assertIn(b'C-472', html_body)
        img, code = get(administrator, self.base + '/admin/qr.png?base=' + parse.quote(self.base))
        self.assertEqual(code, 200)
        self.assertTrue(img.startswith(b'\x89PNG'))
        link, code = get(administrator, self.base + '/admin/qr-link?base=' + parse.quote(self.base))
        self.assertEqual(code, 200)
        self.assertIn('/q/' + self.config['qr_token'], json.loads(link)['url'])
        csv_bytes, code = get(administrator, self.base + '/admin/export.csv')
        self.assertEqual(code, 200)
        self.assertIn('Solano', csv_bytes.decode('utf-8'))

    def test_mobile_only_and_monthly_hours(self):
        desktop, phone, administrator = opener(), opener(True), opener()
        try:
            get(desktop, self.base + '/q/' + self.config['qr_token'])
        except error.HTTPError as exc:
            self.assertEqual(exc.code, 403)
            self.assertIn(b'celular', exc.read())
        else:
            self.fail('La marcación en modo normal debe estar limitada a móviles')
        self.scan(phone)
        with sqlite3.connect(Path(self.tempdir.name) / 'marcaciones.sqlite3') as conn:
            conn.execute("INSERT INTO operarios VALUES('OP-MES', 'Laura', 'Mora', '2026-10-01T00:00:00+00:00')")
            # Turno 4h que cruza de octubre a noviembre en Costa Rica:
            # 31/oct 23:00 a 01/nov 03:00 CR -> 1h octubre, 3h noviembre.
            conn.execute("""INSERT INTO turnos (codigo, entrada_utc, entrada_celular, entrada_zona,
                salida_utc, salida_celular, salida_zona)
                VALUES ('OP-MES','2026-11-01T05:00:00+00:00','2026-10-31T23:00:00-06:00','America/Costa_Rica',
                '2026-11-01T09:00:00+00:00','2026-11-01T03:00:00-06:00','America/Costa_Rica')""")
            conn.execute("""INSERT INTO turnos (codigo, entrada_utc, entrada_celular, entrada_zona)
                VALUES ('OP-MES','2026-11-20T10:00:00+00:00','2026-11-20T04:00:00-06:00','America/Costa_Rica')""")
        login, status = post(administrator, self.base + '/admin/login',
                             {'password': self.config['admin_password']}, False)
        self.assertEqual(status, 200)
        self.assertIn(b'OP-MES', login)
        nov, status = get(administrator, self.base + '/admin?mes=2026-11&operario=OP-MES')
        self.assertEqual(status, 200)
        self.assertIn(b'3 h 00 min', nov)
        self.assertIn(b'Laura Mora', nov)
        self.assertIn(b'Abierto', nov)
        oct, status = get(administrator, self.base + '/admin?mes=2026-10&operario=OP-MES')
        self.assertEqual(status, 200)
        self.assertIn(b'1 h 00 min', oct)
        csv_bytes, status = get(administrator, self.base + '/admin/horas.csv?mes=2026-11')
        self.assertEqual(status, 200)
        self.assertIn('OP-MES', csv_bytes.decode('utf-8-sig'))
        self.assertIn('3 h 00 min', csv_bytes.decode('utf-8-sig'))
        unauth = opener()
        try:
            get(unauth, self.base + '/admin/horas.csv?mes=2026-11')
        except error.HTTPError as exc:
            self.assertEqual(exc.code, 403)
        else:
            self.fail('El CSV mensual no debe ser público')


class AccesoDirectoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.base = f'http://127.0.0.1:{cls.port}'
        env = {**os.environ, 'HOST': '127.0.0.1', 'PORT': str(cls.port),
               'INSTANCE_DIR': cls.tempdir.name, 'MODO_PRUEBA': '1'}
        cls.proc = subprocess.Popen([sys.executable, '-u', str(PROJECT / 'main.py')], cwd=str(PROJECT), env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(60):
            try:
                get(opener(), cls.base + '/')
                break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError('Servidor de prueba directa no inició')

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.tempdir.cleanup()

    def test_formulario_directo_sin_qr_y_resumen_mensual(self):
        phone = opener(True)
        body, status = get(phone, self.base + '/')
        self.assertEqual(status, 200)
        self.assertIn(b'id="turno-form"', body)
        self.assertIn(b'ACCESO DIRECTO', body)
        csrf = re.search(rb'data-csrf="([^"]+)"', body).group(1).decode()
        registro = {'csrf': csrf, 'codigo': 'CR-321', 'nombre': 'Luis', 'apellido': 'Araya',
                    'hora_celular': '2026-11-22T14:40:00-06:00', 'zona': 'America/Costa_Rica'}
        estado, code = post(phone, self.base + '/api/estado', {'codigo': 'CR-321', 'csrf': csrf})
        self.assertEqual(code, 200)
        self.assertEqual(estado['accion'], 'entrada')
        respuesta, code = post(phone, self.base + '/api/registrar', registro)
        self.assertEqual(code, 200)
        self.assertEqual(respuesta['accion'], 'entrada')
        _, code = post(phone, self.base + '/api/registrar', registro)
        self.assertEqual(code, 403)
        body, status = get(phone, self.base + '/')
        self.assertEqual(status, 200)
        csrf2 = re.search(rb'data-csrf="([^"]+)"', body).group(1).decode()
        estado, code = post(phone, self.base + '/api/estado', {'codigo': 'CR-321', 'csrf': csrf2})
        self.assertEqual(estado['accion'], 'salida')
        registro['csrf'] = csrf2
        respuesta, code = post(phone, self.base + '/api/registrar', registro)
        self.assertEqual(code, 200)
        self.assertEqual(respuesta['accion'], 'salida')
        cfg = json.loads((Path(self.tempdir.name) / 'config.json').read_text())
        admin = opener()
        raw, code = post(admin, self.base + '/admin/login', {'password': cfg['admin_password']}, False)
        self.assertEqual(code, 200)
        self.assertIn(b'CR-321', raw)
        panel, code = get(admin, self.base + '/admin?mes=2026-11&operario=CR-321')
        self.assertEqual(code, 200)
        self.assertIn(b'Luis Araya', panel)
        self.assertIn(b'HORAS DEL MES', panel)
        csv_data, code = get(admin, self.base + '/admin/horas.csv?mes=2026-11')
        self.assertEqual(code, 200)
        self.assertIn(b'CR-321', csv_data)


if __name__ == '__main__':
    unittest.main()

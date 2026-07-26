import os
import sys
import time
import json

# 🛠️ PARCHE INTEGRADO DE RUTAS PARA PYTHONANYWHERE (PYTHON 3.10)
path = '/home/villasdelcountry3/.local/lib/python3.10/site-packages'
if path not in sys.path:
    sys.path.insert(0, path)

from flask import Flask, render_template, request, redirect, session, jsonify, send_file, url_for, flash, get_flashed_messages, send_from_directory
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import io
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from pywebpush import webpush, WebPushException

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'clave_secreta_2026_nutricion')

DB_PATH = os.path.join(os.path.dirname(__file__), 'control_acceso.db')

VAPID_PUBLIC_KEY = "BFj0sRirBshj1t1ebwKHeTd3C8kFcBv6qfPT4SuxDUJRltpP3qtUy6WEwCtwT3hEiVu3vgIJyKSlMga-9IFG9d0"
VAPID_PRIVATE_KEY_PATH = os.path.join(os.path.dirname(__file__), "private_key.pem")
VAPID_CLAIMS = {
    "sub": "mailto:admin@villasdelcountry3.com"
}

def enviar_notificacion_push(subscription_info, titulo, mensaje, url="/colonos"):
    payload = json.dumps({
        "title": titulo,
        "body": mensaje,
        "url": url
    })
    try:
        webpush(
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY_PATH,
            vapid_claims=VAPID_CLAIMS
        )
        return True
    except WebPushException as ex:
        print(f"Error enviando Web Push: {ex}")
        if ex.response is not None and ex.response.status_code in [404, 410]:
            try:
                conn = conectar_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM suscripciones_push WHERE subscription_json LIKE ?", (f"%{subscription_info.get('endpoint', '')}%",))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"Error limpiando suscripción obsoleta: {e}")
        return False

REGISTRO_INTENTOS = {}

def es_spam(ip_o_id, limite_peticiones=5, ventana_segundos=10):
    ahora = time.time()
    if ip_o_id not in REGISTRO_INTENTOS:
        REGISTRO_INTENTOS[ip_o_id] = []
    REGISTRO_INTENTOS[ip_o_id] = [t for t in REGISTRO_INTENTOS[ip_o_id] if ahora - t < ventana_segundos]
    if len(REGISTRO_INTENTOS[ip_o_id]) >= limite_peticiones:
        return True
    REGISTRO_INTENTOS[ip_o_id].append(ahora)
    return False

def obtener_hora_mexico():
    return datetime.now(ZoneInfo('America/Mexico_City'))

def conectar_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def purgar_mensajes_antiguos_5_dias():
    try:
        conn = conectar_db()
        cursor = conn.cursor()
        hace_5_dias = obtener_hora_mexico().timestamp() - 432000
        cursor.execute("SELECT id, fecha, tipo_solicitud FROM reservas WHERE tipo_solicitud IN ('Queja', 'Sugerencia', 'Felicitación', 'Aviso a Caseta')")
        registros = cursor.fetchall()
        ids_purgar = []
        for r in registros:
            f_str = r['fecha']
            try:
                dt_obj = datetime.strptime(f_str, '%d/%m/%Y %H:%M').replace(tzinfo=ZoneInfo('America/Mexico_City'))
                if dt_obj.timestamp() < hace_5_dias:
                    ids_purgar.append(r['id'])
            except Exception:
                pass
        if ids_purgar:
            placeholders = ','.join(['?'] * len(ids_purgar))
            cursor.execute(f"DELETE FROM reservas WHERE id IN ({placeholders})", ids_purgar)
            conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error durante la purga de 5 días: {e}")

def inicializar_base_datos():
    try:
        conn = conectar_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS configuracion_caseta (
                clave VARCHAR(50) PRIMARY KEY,
                valor VARCHAR(255)
            )
        """)
        cursor.execute("""
            INSERT OR IGNORE INTO configuracion_caseta (clave, valor)
            VALUES ('pass_monitor', ?)
        """, (generate_password_hash('caseta2026'),))
        cursor.execute("""
            INSERT OR IGNORE INTO configuracion_caseta (clave, valor)
            VALUES ('pass_scanner', ?)
        """, (generate_password_hash('scanner2026'),))
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS suscripciones_push (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                colono_id INTEGER,
                subscription_json TEXT UNIQUE,
                fecha_registro TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS comite_avisos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                titulo TEXT NOT NULL,
                mensaje TEXT NOT NULL,
                fecha TEXT NOT NULL,
                autor TEXT
            )
        """)
        try: cursor.execute("ALTER TABLE colonos ADD COLUMN dispositivo_id TEXT")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE colonos ADD COLUMN dispositivo_id_2 TEXT")
        except sqlite3.OperationalError: pass
        try: cursor.execute("ALTER TABLE colonos ADD COLUMN max_dispositivos INTEGER DEFAULT 1")
        except sqlite3.OperationalError: pass
        conn.commit()
        cursor.close()
        conn.close()
        purgar_mensajes_antiguos_5_dias()
    except Exception as e:
        print(f"Error al inicializar la base de datos: {e}")

inicializar_base_datos()

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "Villas del Country III",
        "short_name": "Villas Country",
        "description": "Portal de Control de Acceso Residencial",
        "start_url": "/colonos",
        "display": "standalone",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "orientation": "portrait",
        "icons": [
            {"src": "https://cdn-icons-png.flaticon.com/512/619/619032.png", "sizes": "192x192", "type": "image/png"},
            {"src": "https://cdn-icons-png.flaticon.com/512/619/619032.png", "sizes": "512x512", "type": "image/png"}
        ]
    })

@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')

@app.route('/api/push/public-key', methods=['GET'])
def get_push_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})

@app.route('/api/push/subscribe', methods=['POST'])
def subscribe_push():
    if 'colono_id' not in session:
        return jsonify({"success": False, "error": "No hay sesión activa"}), 401
    data = request.get_json() or {}
    subscription = data.get('subscription')
    if not subscription:
        return jsonify({"success": False, "error": "Suscripción vacía"}), 400
    conn = conectar_db()
    cursor = conn.cursor()
    fecha_str = obtener_hora_mexico().strftime('%d/%m/%Y %H:%M')
    sub_json_str = json.dumps(subscription)
    cursor.execute("""
        INSERT OR REPLACE INTO suscripciones_push (colono_id, subscription_json, fecha_registro)
        VALUES (?, ?, ?)
    """, (session['colono_id'], sub_json_str, fecha_str))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"success": True, "message": "Dispositivo registrado para alertas push."})

@app.route('/api/verificar-mis-notificaciones', methods=['GET'])
def verificar_mis_notificaciones():
    if 'colono_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401
    colono_id = session['colono_id']
    conexion = conectar_db()
    cursor = conexion.cursor()
    cursor.execute("""
        SELECT estatus, fecha 
        FROM reservas 
        WHERE colono_id = ? AND tipo_solicitud = 'Aviso a Caseta' 
        ORDER BY id DESC LIMIT 1
    """, (colono_id,))
    aviso = cursor.fetchone()
    cursor.execute("""
        SELECT estatus 
        FROM reservas 
        WHERE colono_id = ? AND (tipo_solicitud = 'Reserva' OR area_comun LIKE '%Palapa%') 
        ORDER BY id DESC LIMIT 1
    """, (colono_id,))
    reserva_comun = cursor.fetchone()
    conexion.close()
    mensaje_leido = None
    if aviso and aviso['estatus'] and 'Leído |' in aviso['estatus']:
        mensaje_leido = aviso['estatus'].split(' | ')[1]
    palapa_estatus = None
    if reserva_comun:
        palapa_estatus = reserva_comun['estatus']
    return jsonify({
        'mensaje_leido': mensaje_leido,
        'palapa_estatus': palapa_estatus
    })

def verificar_o_migrar_password(password_ingresada, password_db):
    if not password_db:
        return False
    if password_db.startswith('pbkdf2:') or password_db.startswith('scrypt:'):
        return check_password_hash(password_db, password_ingresada)
    else:
        return password_ingresada == password_db

def extraer_timestamp_qr(token, estatus, area_comun):
    try:
        if token and 'P-VCountry-' in token:
            return int(token.split('-')[-1])
        if estatus and 'P-VCountry-' in estatus:
            return int(estatus.split('-')[-1])
        if area_comun and 'P-VCountry-' in area_comun:
            parte_token = area_comun.split('P-VCountry-')[1]
            token_limpio = parte_token.split(')')[0].split(' | ')[0].strip()
            return int(token_limpio.split('-')[-1])
    except:
        pass
    return None

@app.route('/colonos', methods=['GET', 'POST'])
def gestionar_colonos():
    purgar_mensajes_antiguos_5_dias()
    conn = conectar_db()
    cursor = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action_type')
        client_ip = request.remote_addr
        if action == 'login_vecino':
            if es_spam(f"login_{client_ip}", limite_peticiones=5, ventana_segundos=15):
                flash("⚠️ Demasiados intentos seguidos. Espera 15 segundos por seguridad.", "error")
                cursor.close()
                conn.close()
                return redirect(url_for('gestionar_colonos'))
            u = request.form['username'].strip().lower()
            p = request.form['password']
            dispositivo_actual = request.form.get('dispositivo_id', '').strip()
            cursor.execute("SELECT * FROM colonos")
            filas_colonos = cursor.fetchall()
            login_exitoso = False
            for c in filas_colonos:
                user_def = f"{c['calle'].strip().lower()[0]}{c['numero_casa'].strip().lower().replace(' ', '')}"
                if u == user_def and verificar_o_migrar_password(p, c['password']):
                    max_permitidos = c['max_dispositivos'] if c['max_dispositivos'] else 1
                    d1 = c['dispositivo_id']
                    d2 = c['dispositivo_id_2']
                    if dispositivo_actual and (dispositivo_actual == d1 or dispositivo_actual == d2):
                        pass
                    elif not d1:
                        cursor.execute("UPDATE colonos SET dispositivo_id = ? WHERE id = ?", (dispositivo_actual, c['id']))
                        conn.commit()
                    elif max_permitidos >= 2 and not d2 and dispositivo_actual != d1:
                        cursor.execute("UPDATE colonos SET dispositivo_id_2 = ? WHERE id = ?", (dispositivo_actual, c['id']))
                        conn.commit()
                    else:
                        flash(f"⛔ Acceso Denegado: Tu domicilio tiene un límite de {max_permitidos} dispositivo(s) autorizado(s) y ya fue alcanzado. Solicita a administración ampliar el cupo.", "error")
                        cursor.close()
                        conn.close()
                        return redirect(url_for('gestionar_colonos'))
                    session['colono_id'] = c['id']
                    flash("Sesión iniciada correctamente.", "exito")
                    login_exitoso = True
                    if not (c['password'].startswith('pbkdf2:') or c['password'].startswith('scrypt:')):
                        cursor.execute("UPDATE colonos SET password = ? WHERE id = ?", (generate_password_hash(p), c['id']))
                        conn.commit()
                    break
            if not login_exitoso:
                flash("Usuario o contraseña incorrectos.", "error")
        elif action == 'actualizar_seguridad' and 'colono_id' in session:
            cursor.execute("SELECT * FROM colonos WHERE id=?", (session['colono_id'],))
            colono = cursor.fetchone()
            if colono and colono['pago_al_corriente'] == 1:
                cursor.execute("""
                    UPDATE colonos SET status_visitas=?, nota_paqueteria=?, instruccion_guardia=?
                    WHERE id=?
                """, (request.form['status_visitas'], request.form['nota_paqueteria'], request.form['instruccion_guardia'], session['colono_id']))
                conn.commit()
                flash("Indicaciones para caseta actualizadas.", "exito")
            else:
                flash("❌ Acción bloqueada: Su cuenta presenta un adeudo de mantenimiento.", "error")
        elif action == 'buzon' and 'colono_id' in session:
            if es_spam(f"buzon_{session['colono_id']}", limite_peticiones=3, ventana_segundos=30):
                flash("⚠️ Por favor espera unos segundos antes de enviar otro mensaje.", "error")
            else:
                cursor.execute("SELECT * FROM colonos WHERE id=?", (session['colono_id'],))
                colono = cursor.fetchone()
                if colono and colono['pago_al_corriente'] == 1:
                    fecha_actual = obtener_hora_mexico().strftime('%d/%m/%Y %H:%M')
                    cursor.execute("""
                        INSERT INTO reservas (colono_id, tipo_solicitud, area_comun, fecha, estatus)
                        VALUES (?, ?, ?, ?, 'Pendiente')
                    """, (session['colono_id'], request.form['tipo_solicitud'], request.form['detalle'], fecha_actual))
                    conn.commit()
                    flash("Mensaje enviado correctamente.", "exito")
                else:
                    flash("❌ Acción bloqueada: Su cuenta presenta un adeudo de mantenimiento.", "error")
        elif action == 'reserva' and 'colono_id' in session:
            cursor.execute("SELECT * FROM colonos WHERE id=?", (session['colono_id'],))
            colono = cursor.fetchone()
            if colono and colono['pago_al_corriente'] == 1:
                cursor.execute("""
                    INSERT INTO reservas (colono_id, tipo_solicitud, area_comun, fecha, horario, estatus)
                    VALUES (?, ?, ?, ?, ?, 'Pendiente')
                """, (session['colono_id'], 'Reserva', request.form['area'], request.form['fecha'], request.form['horario']))
                conn.commit()
                flash("Solicitud de reserva enviada.", "exito")
            else:
                flash("❌ Acción bloqueada: Su cuenta presenta un adeudo de mantenimiento.", "error")
        cursor.close()
        conn.close()
        return redirect(url_for('gestionar_colonos'))
    error, exito = None, None
    for category, message in get_flashed_messages(with_categories=True):
        if category == 'exito': exito = message
        elif category == 'error': error = message
    colono_sel = None
    solicitudes = []
    cursor.execute("SELECT * FROM comite")
    comite = cursor.fetchall()
    cursor.execute("SELECT * FROM comite_avisos ORDER BY id DESC LIMIT 10")
    avisos_comite = cursor.fetchall()
    if 'colono_id' in session:
        cursor.execute("SELECT * FROM colonos WHERE id = ?", (session['colono_id'],))
        colono_sel = cursor.fetchone()
        cursor.execute("""
            SELECT reservas.*, colonos.calle, colonos.numero_casa, colonos.nombre_titular
            FROM reservas
            JOIN colonos ON reservas.colono_id = colonos.id
            WHERE reservas.colono_id = ?
            ORDER BY reservas.id DESC
        """, (session['colono_id'],))
        filas_res = cursor.fetchall()
        for f in filas_res:
            d = dict(f)
            ts = extraer_timestamp_qr(None, d['estatus'], d['area_comun'])
            d['fecha_qr_visible'] = datetime.fromtimestamp(ts, tz=ZoneInfo('America/Mexico_City')).strftime('%d/%m/%Y %H:%M') if ts else None
            d['fecha_publicado'] = d['fecha'] if d.get('fecha') and str(d['fecha']) != 'None' else obtener_hora_mexico().strftime('%d/%m/%Y %H:%M')
            if d['tipo_solicitud'] == 'Aviso a Caseta' and d['estatus'] and 'Leído |' in d['estatus']:
                d['fecha_lectura_aviso'] = d['estatus'].split(' | ')[1]
                d['estatus_limpio'] = 'Leído'
            else:
                d['fecha_lectura_aviso'] = None
                d['estatus_limpio'] = d['estatus']
            solicitudes.append(d)
    cursor.close()
    conn.close()
    return render_template('colonos.html', colono_sel=colono_sel, comite=comite, avisos_comite=avisos_comite, solicitudes=solicitudes, error=error, exito=exito)

@app.route('/api/resetear-dispositivo/<int:colono_id>', methods=['POST'])
def resetear_dispositivo(colono_id):
    if not session.get('admin_logeado'):
        return jsonify({'success': False, 'message': 'No autorizado'}), 401
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE colonos SET dispositivo_id = NULL, dispositivo_id_2 = NULL WHERE id = ?", (colono_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True, 'message': 'Dispositivos desvinculados con éxito.'})

@app.route('/api/cambiar-limite-dispositivos/<int:colono_id>/<int:limite>', methods=['POST'])
def cambiar_limite_dispositivos(colono_id, limite):
    if not session.get('admin_logeado'):
        return jsonify({'success': False, 'message': 'No autorizado'}), 401
    if limite < 1:
        limite = 1
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE colonos SET max_dispositivos = ? WHERE id = ?", (limite, colono_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True, 'message': f'Límite actualizado a {limite} dispositivo(s).'})

@app.route('/cambiar_password', methods=['POST'])
def cambiar_password():
    if 'colono_id' not in session: return redirect('/colonos')
    nueva_pass_hash = generate_password_hash(request.form['new_pass'])
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE colonos SET password = ? WHERE id = ?", (nueva_pass_hash, session['colono_id']))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/colonos')

@app.route('/salir_vecino')
def salir_vecino():
    session.pop('colono_id', None)
    return redirect('/colonos')

@app.route('/colonos/deshabilitar-pase/<int:id>')
def colono_deshabilitar_pase(id):
    if 'colono_id' not in session: return redirect('/colonos')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE reservas
        SET estatus = 'Deshabilitado'
        WHERE id = ? AND colono_id = ? AND estatus NOT IN ('Ingresado', 'Deshabilitado')
    """, (id, session['colono_id']))
    conn.commit()
    cursor.close()
    conn.close()
    flash("El pase fue deshabilitado correctamente.", "exito")
    return redirect('/colonos')

@app.route('/control')
def vista_control():
    if not session.get('admin_logeado'): return redirect('/login')
    purgar_mensajes_antiguos_5_dias()
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT reservas.*, colonos.calle, colonos.numero_casa, colonos.nombre_titular
        FROM reservas
        JOIN colonos ON reservas.colono_id = colonos.id
        ORDER BY reservas.id DESC
    """)
    filas = cursor.fetchall()
    solicitudes = []
    for f in filas:
        d = dict(f)
        ts = extraer_timestamp_qr(None, d['estatus'], d['area_comun'])
        d['fecha_qr_visible'] = datetime.fromtimestamp(ts, tz=ZoneInfo('America/Mexico_City')).strftime('%d/%m/%Y %H:%M') if ts else d['fecha']
        if d['tipo_solicitud'] == 'Aviso a Caseta' and d['estatus'] and 'Leído |' in d['estatus']:
            d['fecha_lectura_aviso'] = d['estatus'].split(' | ')[1]
            d['estatus_limpio'] = 'Leído'
        else:
            d['fecha_lectura_aviso'] = None
            d['estatus_limpio'] = d['estatus']
        solicitudes.append(d)
    cursor.execute("SELECT * FROM colonos ORDER BY calle")
    colonos = cursor.fetchall()
    cursor.execute("SELECT * FROM comite")
    comite = cursor.fetchall()
    cursor.execute("SELECT * FROM comite_avisos ORDER BY id DESC")
    avisos_comite = cursor.fetchall()
    cursor.execute("SELECT * FROM usuarios")
    usuarios = cursor.fetchall()
    cursor.execute("SELECT * FROM configuracion_caseta")
    passwords = cursor.fetchall()
    pass_dict = {p['clave']: "********" for p in passwords}
    cursor.close()
    conn.close()
    return render_template('control.html', colonos=colonos, solicitudes=solicitudes, comite=comite, avisos_comite=avisos_comite, usuarios=usuarios, pass_caseta=pass_dict)

@app.route('/control/enviar-comunicado', methods=['POST'])
def enviar_comunicado_comite():
    if not session.get('admin_logeado'):
        return redirect('/login')
    titulo = request.form.get('titulo', '').strip()
    mensaje = request.form.get('mensaje', '').strip()
    if not titulo or not mensaje:
        flash("El título y el mensaje son obligatorios.", "error")
        return redirect('/control')
    conn = conectar_db()
    cursor = conn.cursor()
    fecha_str = obtener_hora_mexico().strftime('%d/%m/%Y %H:%M')
    autor_str = session.get('admin_nombre', 'Comité de Administración')
    cursor.execute("""
        INSERT INTO comite_avisos (titulo, mensaje, fecha, autor)
        VALUES (?, ?, ?, ?)
    """, (titulo, mensaje, fecha_str, autor_str))
    conn.commit()
    cursor.execute("SELECT subscription_json FROM suscripciones_push")
    suscripciones = cursor.fetchall()
    enviados = 0
    for s in suscripciones:
        try:
            sub_info = json.loads(s['subscription_json'])
            if enviar_notificacion_push(
                subscription_info=sub_info,
                titulo=f"📢 {titulo}",
                mensaje=mensaje,
                url="/colonos"
            ):
                enviados += 1
        except Exception:
            pass
    cursor.close()
    conn.close()
    flash(f"Comunicado publicado y enviado a {enviados} dispositivo(s).", "exito")
    return redirect('/control')

@app.route('/control/eliminar-comunicado/<int:id>')
def eliminar_comunicado(id):
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM comite_avisos WHERE id = ?", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Comunicado eliminado del panel.", "exito")
    return redirect('/control')

@app.route('/control/actualizar_passwords_caseta', methods=['POST'])
def actualizar_passwords_caseta():
    if not session.get('admin_logeado'): return redirect('/login')
    pass_monitor = request.form.get('pass_monitor', '').strip()
    pass_scanner = request.form.get('pass_scanner', '').strip()
    conn = conectar_db()
    cursor = conn.cursor()
    if pass_monitor and pass_monitor != "********":
        cursor.execute("UPDATE configuracion_caseta SET valor = ? WHERE clave = 'pass_monitor'", (generate_password_hash(pass_monitor),))
    if pass_scanner and pass_scanner != "********":
        cursor.execute("UPDATE configuracion_caseta SET valor = ? WHERE clave = 'pass_scanner'", (generate_password_hash(pass_scanner),))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/aprobar_reserva/<int:id>')
def aprobar_reserva(id):
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE reservas SET estatus = 'Aprobada' WHERE id = ?", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/eliminar_solicitud/<int:id>')
def eliminar_solicitud(id):
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM reservas WHERE id = ?", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/control/eliminar_solicitudes_masivas', methods=['POST'])
def eliminar_solicitudes_masivas():
    if not session.get('admin_logeado'): return redirect('/login')
    ids_a_eliminar = request.form.getlist('ids_seleccionados')
    if not ids_a_eliminar: return redirect('/control')
    conn = conectar_db()
    cursor = conn.cursor()
    try:
        ids_limpios = [int(x) for x in ids_a_eliminar]
        placeholders = ','.join(['?'] * len(ids_limpios))
        cursor.execute(f"DELETE FROM reservas WHERE id IN ({placeholders})", ids_limpios)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"Error crítico en la eliminación masiva: {e}")
    finally:
        cursor.close()
        conn.close()
    return redirect('/control')

@app.route('/guardar_colono', methods=['POST'])
def guardar_colono():
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    pass_default_hash = generate_password_hash('1234')
    cursor.execute("""
        INSERT INTO colonos (calle, numero_casa, nombre_titular, telefono_1, telefono_2, password, pago_al_corriente, max_dispositivos)
        VALUES (?, ?, ?, ?, ?, ?, 1, 1)
    """, (request.form['calle'], request.form['numero_casa'], request.form['nombre'], request.form['telefono_1'], request.form['telefono_2'], pass_default_hash))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/editar_colono', methods=['POST'])
def editar_colono():
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE colonos SET calle=?, numero_casa=?, nombre_titular=?, telefono_1=?, telefono_2=?
        WHERE id=?
    """, (request.form['calle'], request.form['numero_casa'], request.form['nombre'], request.form['telefono_1'], request.form['telefono_2'], request.form['id']))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/cambiar_pago/<int:id>/<int:status>')
def cambiar_pago(id, status):
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE colonos SET pago_al_corriente = ? WHERE id = ?", (status, id))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/control/cambiar_pago_masivo', methods=['POST'])
def cambiar_pago_masivo():
    if not session.get('admin_logeado'):
        return redirect('/login')
    ids_colonos = request.form.getlist('ids_colonos')
    nuevo_estatus = int(request.form.get('nuevo_estatus', 1))
    if ids_colonos:
        conn = conectar_db()
        cursor = conn.cursor()
        try:
            placeholders = ','.join(['?'] * len(ids_colonos))
            query = f"UPDATE colonos SET pago_al_corriente = ? WHERE id IN ({placeholders})"
            cursor.execute(query, [nuevo_estatus] + ids_colonos)
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Error al actualizar lote de pagos: {e}")
        finally:
            cursor.close()
            conn.close()
    return redirect('/control')

@app.route('/eliminar_colono/<int:id>')
def eliminar_colono(id):
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM colonos WHERE id = ?", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/control/reset-password-colono/<int:colono_id>', methods=['POST'])
def reset_password_colono(colono_id):
    if not session.get('admin_logeado'):
        return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    pass_default_hash = generate_password_hash('1234')
    cursor.execute("UPDATE colonos SET password = ? WHERE id = ?", (pass_default_hash, colono_id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Contraseña del domicilio restablecida a 1234 correctamente.", "exito")
    return redirect('/control')

@app.route('/guardar_comite', methods=['POST'])
def guardar_comite():
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO comite (nombre, cargo, telefono) VALUES (?, ?, ?)",
                   (request.form['nombre'], request.form['cargo'], request.form['telefono']))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/editar_comite', methods=['POST'])
def editar_comite():
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE comite SET nombre=?, cargo=?, telefono=? WHERE id=?",
                   (request.form['nombre'], request.form['cargo'], request.form['telefono'], request.form['id']))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/eliminar_comite/<int:id>')
def eliminar_comite(id):
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM comite WHERE id = ?", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/guardar_admin', methods=['POST'])
def guardar_admin():
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    pass_hash = generate_password_hash(request.form['password'])
    cursor.execute("INSERT INTO usuarios (username, password, nombre) VALUES (?, ?, ?)",
                   (request.form['username'], pass_hash, request.form['nombre']))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/eliminar_admin/<int:id>')
def eliminar_admin(id):
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE id = ?", (id,))
    admin = cursor.fetchone()
    if admin and admin['username'].lower() != 'alejandro':
        cursor.execute("DELETE FROM usuarios WHERE id = ?", (id,))
        conn.commit()
    cursor.close()
    conn.close()
    return redirect('/control')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn = conectar_db()
        cursor = conn.cursor()
        username_ingresado = request.form['username'].strip()
        password_ingresada = request.form['password']
        cursor.execute("SELECT * FROM usuarios WHERE username=?", (username_ingresado,))
        user = cursor.fetchone()
        if user and verificar_o_migrar_password(password_ingresada, user['password']):
            session['admin_logeado'] = True
            session['admin_nombre'] = user['nombre']
            if not (user['password'].startswith('pbkdf2:') or user['password'].startswith('scrypt:')):
                cursor.execute("UPDATE usuarios SET password = ? WHERE id = ?", (generate_password_hash(password_ingresada), user['id']))
                conn.commit()
            cursor.close()
            conn.close()
            return redirect('/control')
        cursor.close()
        conn.close()
    return render_template('Login.html')

@app.route('/logout')
def logout():
    session.pop('admin_logeado', None)
    session.pop('admin_nombre', None)
    return redirect('/login')

@app.route('/')
@app.route('/vigilancia')
def vista_vigilancia():
    if not session.get('caseta_monitor_autenticada'):
        return redirect('/login/caseta/monitor')
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM colonos ORDER BY calle")
    colonos = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template('vigilancia.html', colonos=colonos)

@app.route('/login/caseta/<tipo>', methods=['GET', 'POST'])
def login_caseta(tipo):
    if tipo not in ['monitor', 'scanner']:
        return "Página no encontrada", 404
    error = None
    if request.method == 'POST':
        password_ingresada = request.form.get('password')
        conn = conectar_db()
        cursor = conn.cursor()
        clave_db = 'pass_monitor' if tipo == 'monitor' else 'pass_scanner'
        cursor.execute("SELECT valor FROM configuracion_caseta WHERE clave = ?", (clave_db,))
        res = cursor.fetchone()
        if res and verificar_o_migrar_password(password_ingresada, res['valor']):
            session[f'caseta_{tipo}_autenticada'] = True
            if not (res['valor'].startswith('pbkdf2:') or res['valor'].startswith('scrypt:')):
                cursor.execute("UPDATE configuracion_caseta SET valor = ? WHERE clave = ?", (generate_password_hash(password_ingresada), clave_db))
                conn.commit()
            cursor.close()
            conn.close()
            destino = '/vigilancia' if tipo == 'monitor' else '/guardia/scanner'
            return redirect(destino)
        else:
            cursor.close()
            conn.close()
            error = "Contraseña incorrecta para el acceso de caseta."
    return f'''
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Acceso Restringido - Caseta</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body class="bg-dark text-white d-flex align-items-center" style="height: 100vh;">
        <div class="container" style="max-width: 400px;">
            <div class="card p-4 text-dark shadow-lg">
                <h3 class="text-center mb-3">🛡️ Seguridad Caseta</h3>
                <p class="text-muted text-center small">Ingresa la contraseña del <strong>{tipo.upper()}</strong></p>
                {f'<div class="alert alert-danger p-2 small text-center">{error}</div>' if error else ''}
                <form method="POST">
                    <div class="mb-3">
                        <input type="password" name="password" class="form-control text-center fs-5" placeholder="Contraseña" required autocomplete="off">
                    </div>
                    <button type="submit" class="btn btn-primary w-100">Ingresar al Sistema</button>
                </form>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/api/crear-qr', methods=['POST'])
def crear_qr():
    if 'colono_id' not in session:
        return jsonify({'success': False, 'message': 'Sesión no válida'}), 401
    if es_spam(f"qr_{session['colono_id']}", limite_peticiones=10, ventana_segundos=30):
        return jsonify({'success': False, 'message': '⚠️ Límite de generación alcanzado. Espera 30 segundos.'}), 429
    datos = request.get_json() or {}
    nombre_visitante = datos.get('name', '').strip() or datos.get('nombre', '').strip()
    es_permanente = datos.get('permanente', False) or datos.get('frecuente', False)
    if not nombre_visitante:
        return jsonify({'success': False, 'message': 'El nombre del visitante es obligatorio.'}), 400
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("SELECT calle, numero_casa, pago_al_corriente FROM colonos WHERE id = ?", (session['colono_id'],))
    colono = cursor.fetchone()
    if not colono:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Domicilio no encontrado.'}), 404
    if colono['pago_al_corriente'] != 1:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Cuenta bloqueada por mantenimiento.'}), 403
    primera_letra = colono['calle'].strip().lower()[0] if colono['calle'] else 'x'
    identificador_casa = f"{primera_letra}{colono['numero_casa']}".replace(' ', '')
    timestamp_creacion = int(time.time())
    modalidad = "PERM" if es_permanente else "TEMP"
    token_pase = f"P-VCountry-{modalidad}-{identificador_casa}-{timestamp_creacion}"
    tipo_registro = 'PaseQR_Frecuente' if es_permanente else 'PaseQR'
    cursor.execute("""
        INSERT INTO reservas (colono_id, tipo_solicitud, area_comun, estatus)
        VALUES (?, ?, ?, ?)
    """, (session['colono_id'], tipo_registro, f"Invitado: {nombre_visitante}", token_pase))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True, 'token': token_pase, 'nombre': nombre_visitante})

@app.route('/api/vigilancia-datos', methods=['GET'])
def vigilancia_datos():
    if not session.get('caseta_monitor_autenticada'):
        return jsonify({"error": "No autorizado"}), 401
    conn = conectar_db()
    cursor = conn.cursor()
    query = """
        SELECT c.id, c.calle, c.numero_casa, c.nombre_titular, c.pago_al_corriente,
               c.status_visitas, c.nota_paqueteria, c.instruccion_guardia,
               c.telefono_1, c.telefono_2,
               (SELECT s.area_comun
                FROM reservas s
                WHERE s.colono_id = c.id
                  AND s.tipo_solicitud = 'Aviso a Caseta'
                  AND s.estatus = 'Pendiente'
                ORDER BY s.id DESC LIMIT 1) AS aviso_corto
        FROM colonos c
        ORDER BY c.calle
    """
    cursor.execute(query)
    res = cursor.fetchall()
    cursor.close()
    conn.close()
    lista_colonos = []
    for c in res:
        lista_colonos.append({
            'id': c['id'],
            'calle': c['calle'],
            'numero_casa': c['numero_casa'],
            'nombre_titular': c['nombre_titular'],
            'pago_al_corriente': c['pago_al_corriente'],
            'status_visitas': c['status_visitas'] if c['status_visitas'] else 'Solo con previa autorización / Aviso',
            'nota_paqueteria': c['nota_paqueteria'] if c['nota_paqueteria'] else '',
            'instruccion_guardia': c['instruccion_guardia'] if c['instruccion_guardia'] else '',
            'telefono_1': c['telefono_1'],
            'telefono_2': c['telefono_2'],
            'aviso_corto': c['aviso_corto']
        })
    return jsonify(lista_colonos)

@app.route('/api/marcar-leido/<int:colono_id>', methods=['POST'])
def marcar_aviso_leido(colono_id):
    if not session.get('caseta_monitor_autenticada'):
        return jsonify({"success": False, "message": "No autorizado"}), 401
    conn = conectar_db()
    cursor = conn.cursor()
    success = False
    message = ""
    try:
        ahora_str = obtener_hora_mexico().strftime('%d/%m/%Y %H:%M')
        estatus_con_fecha = f"Leído | {ahora_str}"
        cursor.execute("""
            UPDATE reservas
            SET estatus = ?
            WHERE colono_id = ? AND tipo_solicitud = 'Aviso a Caseta' AND estatus = 'Pendiente'
        """, (estatus_con_fecha, colono_id))
        conn.commit()
        
        # Disparar la notificación Web Push al residente
        cursor.execute("SELECT subscription_json FROM suscripciones_push WHERE colono_id = ?", (colono_id,))
        suscripciones = cursor.fetchall()
        for sub in suscripciones:
            try:
                sub_info = json.loads(sub['subscription_json'])
                enviar_notificacion_push(
                    subscription_info=sub_info,
                    titulo="👀 Aviso Leído",
                    mensaje="El guardia de caseta ha marcado tu aviso como leído.",
                    url="/colonos"
                )
            except Exception as e:
                print(f"Error procesando push de aviso leído: {e}")

        success = True
        message = "Aviso archivado y notificación enviada correctamente."
    except Exception as e:
        conn.rollback()
        message = str(e)
    finally:
        cursor.close()
        conn.close()
    return jsonify({"success": success, "message": message})

@app.route('/guardia/scanner')
def vista_guardia_scanner():
    if not session.get('caseta_scanner_autenticada'):
        return redirect('/login/caseta/scanner')
    return render_template('guardia_scanner.html')

@app.route('/api/validar-qr', methods=['POST'])
def validar_qr():
    datos = request.get_json() or {}
    token_leido = datos.get('token', '').strip()
    if not token_leido:
        return jsonify({'status': 'RECHAZADO', 'message': 'Código vacío.'})
    es_frecuente = "-PERM-" in token_leido
    try:
        partes_token = token_leido.split('-')
        timestamp_qr = int(partes_token[-1])
        tiempo_actual = int(time.time())
        limite_caducidad = 2592000 if es_frecuente else 86400
        if (tiempo_actual - timestamp_qr) > limite_caducidad:
            msg_expira = "❌ El pase frecuente expiró (Superó el mes de vigencia)." if es_frecuente else "❌ Código QR expirado (Venció a las 24 horas)."
            return jsonify({'status': 'RECHAZADO', 'message': msg_expira})
    except Exception:
        return jsonify({'status': 'RECHAZADO', 'message': 'Estructura de QR inválida.'})
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT reservas.*, colonos.calle, colonos.numero_casa, colonos.pago_al_corriente
        FROM reservas
        JOIN colonos ON reservas.colono_id = colonos.id
        WHERE reservas.estatus = ? LIMIT 1
    """, (token_leido,))
    pase = cursor.fetchone()
    if not pase:
        cursor.execute("SELECT * FROM reservas WHERE estatus = 'Ingresado' AND area_comun LIKE ?", (f"%{token_leido}%",))
        ya_ingresado = cursor.fetchone()
        cursor.close()
        conn.close()
        if ya_ingresado:
            return jsonify({'status': 'RECHAZADO', 'message': '❌ Este código QR de un solo uso ya fue utilizado previamente.'})
        return jsonify({'status': 'RECHAZADO', 'message': 'Código QR Inválido, deshabilitado o inexistente.'})
    if pase['pago_al_corriente'] != 1:
        cursor.close()
        conn.close()
        return jsonify({'status': 'RECHAZADO', 'message': 'Acceso Limitado: Domicilio con Adeudo.'})
    visitante = pase['area_comun'].replace("Invitado: ", "")
    domicilio = f"{pase['calle']} #{pase['numero_casa']}"
    hora_cdmx = obtener_hora_mexico().strftime('%d/%m %H:%M')
    if es_frecuente:
        nuevo_detalle = f"{pase['area_comun']} | Último Acceso: {hora_cdmx}"
        cursor.execute("UPDATE reservas SET area_comun = ? WHERE id = ?", (nuevo_detalle, pase['id']))
    else:
        nuevo_detalle = f"{pase['area_comun']} (Token: {token_leido})"
        cursor.execute("UPDATE reservas SET estatus = 'Ingresado', area_comun = ? WHERE id = ?", (nuevo_detalle, pase['id']))
    conn.commit()
    cursor.execute("SELECT subscription_json FROM suscripciones_push WHERE colono_id = ?", (pase['colono_id'],))
    suscripciones = cursor.fetchall()
    for sub in suscripciones:
        try:
            sub_info = json.loads(sub['subscription_json'])
            enviar_notificacion_push(
                subscription_info=sub_info,
                titulo="🚗 Visita Ingresando",
                mensaje=f"Se escaneó el QR de: {visitante} en caseta.",
                url="/colonos"
            )
        except Exception as e:
            print(f"Error procesando push a casa: {e}")
    cursor.close()
    conn.close()
    return jsonify({
        'status': 'AUTORIZADO',
        'visitor': visitante,
        'house': domicilio,
        'reserva_id': pase['id']
    })

@app.route('/api/registrar-placas', methods=['POST'])
def registrar_placas():
    if not session.get('caseta_scanner_autenticada'):
        return jsonify({'success': False, 'message': 'No autorizado.'}), 401
    datos = request.get_json() or {}
    reserva_id = datos.get('reserva_id')
    placas = datos.get('placas', '').strip().upper()
    if not reserva_id or not placas:
        return jsonify({'success': False, 'message': 'Datos incompletos.'}), 400
    conn = conectar_db()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT area_comun FROM reservas WHERE id = ?", (reserva_id,))
        registro = cursor.fetchone()
        if not registro:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'message': 'El registro de acceso no existe.'}), 404
        cursor.execute("""
            SELECT colonos.calle, colonos.numero_casa
            FROM reservas
            JOIN colonos ON reservas.colono_id = colonos.id
            WHERE reservas.area_comun LIKE ? AND colonos.pago_al_corriente = 0
            LIMIT 1
        """, (f"%{placas}%",))
        coincidencia = cursor.fetchone()
        advertencia_adeudo = None
        if coincidencia:
            advertencia_adeudo = f"⚠️ Atención: Las placas {placas} tienen historial en {coincidencia['calle']} #{coincidencia['numero_casa']} (Con Adeudo)."
        nuevo_detalle = f"{registro['area_comun']} | Placas: {placas}"
        cursor.execute("UPDATE reservas SET area_comun = ? WHERE id = ?", (nuevo_detalle, reserva_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({
            'success': True,
            'message': 'Placas registradas correctamente.',
            'warning': advertencia_adeudo
        })
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'message': f'Error en base de datos: {str(e)}'}), 500

@app.route('/control/exportar-excel', methods=['POST', 'GET'])
def exportar_excel():
    if not session.get('admin_logeado'): return redirect('/login')
    tipo_filtro = request.form.get('selectorFiltro', 'todo')
    colono_id = request.form.get('colono_id', '')
    fecha_inicio = request.form.get('fecha_inicio', '')
    fecha_fin = request.form.get('fecha_fin', '')
    conn = conectar_db()
    query_base = """
        SELECT reservas.id, colonos.calle, colonos.numero_casa, colonos.nombre_titular,
               reservas.tipo_solicitud, reservas.area_comun, reservas.estatus
        FROM reservas
        JOIN colonos ON reservas.colono_id = colonos.id
        WHERE reservas.tipo_solicitud IN ('PaseQR', 'PaseQR_Frecuente')
    """
    params = []
    if tipo_filtro == 'domicilio' and colono_id:
        query_base += " AND reservas.colono_id = ?"
        params.append(colono_id)
    query_base += " ORDER BY reservas.id DESC"
    df = pd.read_sql_query(query_base, conn, params=params if params else None)
    conn.close()
    fechas_reales = []
    tz_cdmx = ZoneInfo('America/Mexico_City')
    for idx, row in df.iterrows():
        ts = extraer_timestamp_qr(None, row['estatus'], row['area_comun'])
        fechas_reales.append(datetime.fromtimestamp(ts, tz=tz_cdmx) if ts else None)
    df['Fecha Creación'] = fechas_reales
    df['Estado Acceso'] = df['estatus'].apply(lambda e: '🟢 INGRESADO' if e == 'Ingresado' else ('🔴 DESHABILITADO' if e == 'Deshabilitado' else '📱 Activo (En espera)'))
    df['Detalle / Visitante'] = df['area_comun'].apply(lambda a: str(a).replace('Invitado: ', ''))
    if tipo_filtro == 'periodo' and fecha_inicio and fecha_fin:
        ts_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').replace(tzinfo=tz_cdmx)
        ts_fin = datetime.strptime(fecha_fin + ' 23:59:59', '%Y-%m-%d %H:%M:%S').replace(tzinfo=tz_cdmx)
        df = df[(df['Fecha Creación'] >= ts_inicio) & (df['Fecha Creación'] <= ts_fin)]
    df_final = df[['id', 'calle', 'numero_casa', 'nombre_titular', 'Fecha Creación', 'Detalle / Visitante', 'Estado Acceso']].copy()
    df_final.columns = ['ID Registro', 'Calle / Circuito', 'Número Casa', 'Nombre del Titular', 'Fecha y Hora', 'Visitante', 'Estado de Acceso']
    df_final['Fecha y Hora'] = pd.to_datetime(df_final['Fecha y Hora']).dt.tz_localize(None)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_final.to_excel(writer, index=False, sheet_name='Bitacora_QR')
    output.seek(0)
    nombre_archivo = f"Bitacora_Accesos_Auditoria_{int(time.time())}.xlsx"
    return send_file(output, as_attachment=True, download_name=nombre_archivo, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.route('/control/ejecutar-purga-segura', methods=['POST'])
def ejecutar_purga_segura():
    if not session.get('admin_logeado'): return redirect('/login')
    conn = conectar_db()
    limite_tiempo = int(time.time()) - 5184000
    query = """
        SELECT reservas.id, colonos.calle, colonos.numero_casa, colonos.nombre_titular,
               reservas.tipo_solicitud, reservas.area_comun, reservas.estatus
        FROM reservas
        JOIN colonos ON reservas.colono_id = colonos.id
        WHERE reservas.tipo_solicitud IN ('PaseQR', 'PaseQR_Frecuente')
    """
    df = pd.read_sql_query(query, conn)
    ids_a_borrar = []
    registros_respaldo = []
    tz_cdmx = ZoneInfo('America/Mexico_City')
    for idx, row in df.iterrows():
        ts = extraer_timestamp_qr(None, row['estatus'], row['area_comun'])
        if ts and ts < limite_tiempo:
            ids_a_borrar.append(row['id'])
            row_dict = dict(row)
            row_dict['Fecha Creación Real'] = datetime.fromtimestamp(ts, tz=tz_cdmx).strftime('%Y-%m-%d %H:%M:%S')
            registros_respaldo.append(row_dict)
    if not ids_a_borrar:
        conn.close()
        return "No se encontraron pases QR con más de 2 meses de antigüedad para purgar.", 400
    df_respaldo = pd.DataFrame(registros_respaldo)
    df_respaldo = df_respaldo[['id', 'calle', 'numero_casa', 'nombre_titular', 'Fecha Creación Real', 'area_comun', 'estatus']]
    df_respaldo.columns = ['ID Elimina', 'Calle', 'Número', 'Titular', 'Fecha y Hora', 'Detalle', 'Estatus Final']
    df_respaldo['Fecha y Hora'] = pd.to_datetime(df_respaldo['Fecha y Hora']).dt.tz_localize(None)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_respaldo.to_excel(writer, index=False, sheet_name='Historico_Purgado_60_Dias')
    output.seek(0)
    cursor = conn.cursor()
    placeholders = ','.join(['?'] * len(ids_a_borrar))
    cursor.execute(f"DELETE FROM reservas WHERE id IN ({placeholders})", ids_a_borrar)
    conn.commit()
    cursor.close()
    conn.close()
    return send_file(output, as_attachment=True, download_name=f"RESPALDO_HISTORICO_PURGA_60DIAS_{int(time.time())}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
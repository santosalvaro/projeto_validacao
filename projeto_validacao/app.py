import os
import csv
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash

# --- ADICIONE ESTE BLOCO LOGO APÓS OS IMPORTS ---
class ReverseProxyPrefixFix(object):
    def __init__(self, app, prefix=''):
        self.app = app
        self.prefix = prefix

    def __call__(self, environ, start_response):
        # Se o Apache enviar o cabeçalho informando que veio de um subcaminho, ou forçamos o prefixo
        script_name = environ.get('HTTP_X_SCRIPT_NAME', self.prefix)
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ.get('PATH_INFO', '')
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = environ.get('HTTP_X_FORWARDED_PROTO', '')
        if scheme:
            environ['wsgi.url_scheme'] = scheme
        return self.app(environ, start_response)
# ------------------------------------------------

DATABASE = 'database.db'
LIMIT_PER_USER = 383

ADMIN_USER = 'oadministrador'
ADMIN_PASS = 'oadmin'

app = Flask(__name__)
app.secret_key = 'chave_secreta_experimento_cientifico_llm'

# --- ATIVE O MIDDLEWARE AQUI ---
# Isso diz ao Flask: se houver um proxy, adicione '/whavax' antes de todas as rotas e redirecionamentos
app.wsgi_app = ReverseProxyPrefixFix(app.wsgi_app, prefix='/whavax')
# -------------------------------

@app.after_request
def skip_ngrok_warning(response):
    response.headers['ngrok-skip-browser-warning'] = 'true'
    return response

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON;')
    return conn

def init_db():
    if os.path.exists(DATABASE):
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mensagem TEXT NOT NULL,
            label_ids TEXT,
            label_names TEXT,
            source_file TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sample_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            answer TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (sample_id) REFERENCES samples (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()

    # Importação de usuários do txt
    usuarios_file = 'usuarios.txt'
    if os.path.exists(usuarios_file):
        with open(usuarios_file, 'r', encoding='utf-8') as f:
            for line in f:
                username = line.strip()
                if username:
                    p_hash = generate_password_hash(username)
                    try:
                        cursor.execute(
                            'INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)',
                            (username, p_hash, datetime.now().isoformat())
                        )
                    except sqlite3.IntegrityError:
                        pass
        conn.commit()

    # Importação de Amostras
    csv_files = ['gemma4.csv', 'gpt54.csv', 'qwen36.csv']
    for file_name in csv_files:
        if os.path.exists(file_name):
            with open(file_name, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    msg = row.get('mensagem') or row.get('Mensagem')
                    l_ids = row.get('label_ids')
                    l_names = row.get('label_names')
                    if msg:
                        cursor.execute(
                            'INSERT INTO samples (mensagem, label_ids, label_names, source_file) VALUES (?, ?, ?, ?)',
                            (msg, l_ids, l_names, file_name)
                        )
            conn.commit()
    conn.close()

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if username == ADMIN_USER and password == ADMIN_PASS:
            session['admin_logged_in'] = True
            session['username'] = ADMIN_USER
            return redirect(url_for('admin_dashboard'))

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('welcome'))
        else:
            flash('Usuário ou senha incorretos.', 'danger')

    return render_template('login.html')

@app.route('/welcome')
def welcome():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('welcome.html')

@app.route('/classify', methods=['GET', 'POST'])
def classify():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    conn = get_db_connection()

    # Progresso contabilizado corretamente
    total_anotado = conn.execute(
        'SELECT COUNT(*) as total FROM annotations WHERE user_id = ?', (user_id,)
    ).fetchone()['total']

    if total_anotado >= LIMIT_PER_USER:
        conn.close()
        return redirect(url_for('finished'))

    if request.method == 'POST':
        sample_id = request.form.get('sample_id')
        answer = request.form.get('answer')

        if sample_id and answer in ['Sim', 'Não']:
            try:
                # Travamento estrito para evitar Over-validation (Concorrência SQLite)
                conn.execute('BEGIN IMMEDIATE TRANSACTION;')
                votos_atuais = conn.execute(
                    'SELECT COUNT(*) as total FROM annotations WHERE sample_id = ?', (sample_id,)
                ).fetchone()['total']
                
                ja_votou = conn.execute(
                    'SELECT 1 FROM annotations WHERE sample_id = ? AND user_id = ?', (sample_id, user_id)
                ).fetchone()

                if votos_atuais < 3 and not ja_votou:
                    conn.execute(
                        'INSERT INTO annotations (sample_id, user_id, answer, timestamp) VALUES (?, ?, ?, ?)',
                        (sample_id, user_id, answer, datetime.now().isoformat())
                    )
                    conn.commit()
                else:
                    conn.rollback()
            except sqlite3.OperationalError:
                pass
        
        return redirect(url_for('classify'))

    # Fila Inteligente: Prioriza as que têm 2 ou 1 votos e NUNCA repete amostras já respondidas pelo usuário
    query = '''
        SELECT s.*, COUNT(a.id) as total_votos 
        FROM samples s
        LEFT JOIN annotations a ON s.id = a.sample_id
        WHERE s.id NOT IN (SELECT sample_id FROM annotations WHERE user_id = ?)
        GROUP BY s.id
        HAVING total_votos < 3
        ORDER BY total_votos DESC, s.id ASC
        LIMIT 1
    '''
    sample = conn.execute(query, (user_id,)).fetchone()
    conn.close()

    if not sample:
        return redirect(url_for('finished'))

    # Trata as labels quebrando por ';' para gerar a lista tratada
    labels = [l.strip() for l in sample['label_names'].split(',')] if sample['label_names'] else []

    return render_template('annotation.html', sample=sample, labels=labels, progresso=total_anotado)

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    total_samples = conn.execute('SELECT COUNT(*) as total FROM samples').fetchone()['total']
    
    # Amostras resolvidas são aquelas que atingiram 3 validações
    resolved_samples = conn.execute('''
        SELECT COUNT(*) as total FROM (
            SELECT sample_id FROM annotations GROUP BY sample_id HAVING COUNT(id) >= 3
        )
    ''').fetchone()['total']
    
    remaining_samples = max(0, total_samples - resolved_samples)

    users_list = conn.execute('''
        SELECT u.username, COUNT(a.id) as rotacoes
        FROM users u
        LEFT JOIN annotations a ON u.id = a.user_id
        GROUP BY u.id
        ORDER BY rotacoes DESC
    ''').fetchall()
    
    conn.close()
    return render_template('admin.html', total_samples=total_samples, resolved_samples=resolved_samples, remaining_samples=remaining_samples, users_list=users_list)

@app.route('/admin/export-now')
def admin_export_now():
    if not session.get('admin_logged_in'):
        return redirect(url_for('login'))
    
    # Roda o script de exportação integrado
    import export_results
    export_results.export_data()
    
    output_path = 'resultados_consolidados.csv'
    if os.path.exists(output_path):
        return send_file(output_path, as_attachment=True)
    else:
        return "Erro: Planilha não gerada.", 500

@app.route('/finished')
def finished():
    return render_template('finished.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)

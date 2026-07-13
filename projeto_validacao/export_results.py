import csv
import sqlite3
import os

DATABASE = 'database.db'
OUTPUT_FILE = 'resultados_consolidados.csv'

def export_data():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = '''
        SELECT 
            a.id AS id_avaliacao,
            s.id AS id_amostra,
            s.mensagem,
            s.label_ids,
            s.label_names,
            s.source_file,
            a.user_id AS id_validador,
            u.username AS username_anotador,
            a.answer AS resposta_anotador,
            a.timestamp
        FROM annotations a
        JOIN samples s ON a.sample_id = s.id
        JOIN users u ON a.user_id = u.id
        ORDER BY a.timestamp ASC
    '''
    
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        
        with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
            fieldnames = [
                'id_avaliacao', 'id_amostra', 'mensagem', 'label_ids', 
                'label_names', 'source_file', 'id_validador', 
                'username_anotador', 'nome_llm', 'resposta_anotador', 'timestamp'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for row in rows:
                data = dict(row)
                filename = data.get('source_file', '')
                # Extrai o nome da LLM limpo (Ex: "validacao1")
                data['nome_llm'] = os.path.splitext(filename)[0] if filename else 'Desconhecido'
                writer.writerow(data)
                
    except sqlite3.OperationalError as e:
        print(f"Erro: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    export_data()
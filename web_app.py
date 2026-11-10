import os
from flask import Flask, jsonify
from worker import start_background_loop, last_run, DATA_DIR

app = Flask(__name__)
start_background_loop()

@app.get('/')
def home():
    return jsonify({'service': 'robo-blitz4', 'status': 'online',
                    'note': 'Plano gratuito de teste; pode dormir após inatividade.',
                    'data_dir': str(DATA_DIR)})

@app.get('/status')
def status():
    return jsonify(last_run)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '10000')))

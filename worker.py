import os, time, json, hashlib, subprocess, sys, requests
from pathlib import Path

DATA_DIR = Path(os.environ.get('DATA_DIR', '/var/data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ENGINE = Path(__file__).with_name('engine_v15.py')
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
POLL_SECONDS = max(30, int(os.environ.get('POLL_SECONDS', '30')))

# The analysis engine remains the V15 code. This service wrapper runs it on a
# fixed cadence, captures its operational status and only notifies on a change.
def send(text):
    if not TOKEN or not CHAT_ID:
        print('Telegram ainda não configurado; mensagem não enviada.', flush=True)
        return False
    r = requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',
                      data={'chat_id': CHAT_ID, 'text': text}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(data.get('description', 'Falha Telegram'))
    return True

def main():
    print(f'Blitz4 V15 cloud worker iniciado. Consulta a cada {POLL_SECONDS}s.', flush=True)
    while True:
        try:
            env = os.environ.copy(); env['DATA_DIR'] = str(DATA_DIR)
            p = subprocess.run([sys.executable, str(ENGINE)], env=env,
                               capture_output=True, text=True, timeout=240)
            (DATA_DIR/'ultima_execucao.log').write_text(
                'STDOUT:\n'+p.stdout+'\nSTDERR:\n'+p.stderr, encoding='utf-8')
            if p.returncode != 0:
                print('Execução do motor falhou:', p.stderr[-1500:], flush=True)
            else:
                # V15 outputs are logged in full. The engine's strict signal
                # classification is intentionally not weakened by the wrapper.
                # Notification is enabled only after a qualifying V15 status is
                # detected in the generated output; no routine heartbeat alerts.
                out = p.stdout + '\n' + p.stderr
                marker = next((line.split('=', 1)[1] for line in out.splitlines() if line.startswith('BLITZ4_RESULT=')), None)
                result = json.loads(marker) if marker else {}
                status = result.get('status', 'SEM SINAL')
                if result.get('atualizado') and result.get('protecao_aprovada') and status in ('SINAL FORTE', 'SINAL MODERADO'):
                    signature = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    state = DATA_DIR/'telegram_evento_enviado.sha256'
                    previous = state.read_text().strip() if state.exists() else ''
                    if signature != previous:
                        msg = ('🤖 ROBÔ TIPMINER — BLITZ4 DOUBLE V15\n'
                               f'{status}\nHorário-alvo: {result.get("hora", "não informado")}\n'
                               f'Situação: {result.get("entrada", "não informada")}\n'
                               f'Última rodada: {result.get("ultima_rodada", "não informada")}\n'
                               f'Histórico: {result.get("historico", 0)} rodadas\n'
                               f'Motivo: {result.get("motivo", "")}\n'
                               'Análise estatística; não garante resultados futuros.')
                        if send(msg): state.write_text(signature)
                print('Execução concluída.', flush=True)
        except Exception as e:
            print('Erro no ciclo:', repr(e), flush=True)
        time.sleep(POLL_SECONDS)

if __name__ == '__main__': main()

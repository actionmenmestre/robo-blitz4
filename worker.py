import os, time, json, hashlib, subprocess, sys, threading
from pathlib import Path
import requests

DATA_DIR = Path(os.environ.get('DATA_DIR', '/tmp/blitz4-data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
APP_DIR = Path(__file__).resolve().parent
ENGINE = APP_DIR / 'engine_v15.py'
SOURCE_HISTORY = APP_DIR / 'historico_acumulado.json'
HISTORY = DATA_DIR / 'historico_acumulado.json'
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
POLL_SECONDS = max(30, int(os.environ.get('POLL_SECONDS', '30')))

# Free Web Services have ephemeral storage. Copy the bundled baseline only
# when the runtime has no working history yet. A paid persistent disk is not
# assumed here.
if not HISTORY.exists() and SOURCE_HISTORY.exists():
    HISTORY.write_bytes(SOURCE_HISTORY.read_bytes())

lock = threading.Lock()
last_run = {'state': 'starting', 'detail': 'Aguardando primeira análise'}

def send(text):
    if not TOKEN or not CHAT_ID:
        print('Telegram não configurado; alerta não enviado.', flush=True)
        return False
    r = requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',
                      data={'chat_id': CHAT_ID, 'text': text}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(data.get('description', 'Falha Telegram'))
    return True

def run_once():
    global last_run
    env = os.environ.copy()
    env['DATA_DIR'] = str(DATA_DIR)
    p = subprocess.run([sys.executable, str(ENGINE)], env=env,
                       capture_output=True, text=True, timeout=240)
    out = p.stdout + '\n' + p.stderr
    (DATA_DIR / 'ultima_execucao.log').write_text(out, encoding='utf-8')
    if p.returncode != 0:
        last_run = {'state': 'error', 'detail': p.stderr[-1200:]}
        print('Falha do motor:', p.stderr[-1200:], flush=True)
        return
    marker = next((line.split('=', 1)[1] for line in out.splitlines()
                   if line.startswith('BLITZ4_RESULT=')), None)
    if not marker:
        last_run = {'state': 'error', 'detail': 'Motor não produziu BLITZ4_RESULT'}
        print(last_run['detail'], flush=True)
        return
    result = json.loads(marker)
    last_run = {'state': 'ok', 'detail': result}
    status = result.get('status', '')
    if (result.get('atualizado') and result.get('protecao_aprovada')
            and status in ('SINAL FORTE', 'SINAL MODERADO')):
        signature_data = {k: result.get(k) for k in ('status','hora','entrada','ultima_rodada','motivo')}
        signature = hashlib.sha256(json.dumps(signature_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        state_file = DATA_DIR / 'telegram_evento_enviado.sha256'
        previous = state_file.read_text().strip() if state_file.exists() else ''
        if signature != previous:
            msg = ('🤖 ROBÔ TIPMINER — BLITZ4 DOUBLE\n'
                   f"{status}\nHorário-alvo: {result.get('hora','não informado')}\n"
                   f"Entrada: {result.get('entrada','não informada')}\n"
                   f"Última rodada: {result.get('ultima_rodada','não informada')}\n"
                   f"Histórico: {result.get('historico',0)} rodadas\n"
                   f"Motivo: {result.get('motivo','')}\n"
                   'Análise estatística experimental; não garante resultados futuros.')
            if send(msg):
                state_file.write_text(signature)
    print('Ciclo concluído:', status, 'API atualizada=', result.get('atualizado'), flush=True)

def loop():
    global last_run
    print(f'Blitz4 teste gratuito iniciado; ciclo a cada {POLL_SECONDS}s.', flush=True)
    while True:
        try:
            with lock:
                run_once()
        except Exception as exc:
            last_run = {'state': 'error', 'detail': repr(exc)}
            print('Erro no ciclo:', repr(exc), flush=True)
        time.sleep(POLL_SECONDS)


def start_background_loop():
    t = threading.Thread(target=loop, name='blitz4-poller', daemon=True)
    t.start()

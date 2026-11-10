#@title ▶️ EXECUTAR ROBÔ BLITZ4 V14 — clique em Executar
#@markdown O código fica recolhido; o resultado aparece no painel abaixo.

import os, json, shutil, tempfile, requests, math, warnings
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from collections import Counter
import numpy as np, pandas as pd
import re, hashlib, html as html_lib

FUSO=ZoneInfo("America/Sao_Paulo")
API_URL="https://api.core.public.tipminer.com/v1/double/rounds/20686b4a-e703-46b4-9f4d-d4b45a280145/history"
SIMBOLO_COR={0:"Branca", **{i:"Amarela" for i in range(1,8)}, **{i:"Preta" for i in range(8,15)}}
PEDRAS_ATENCAO={3,5,6,10,13}
MIN_TREINO=12
MIN_VALIDACAO=20
MIN_VALIDACAO_MODERADO=30
MIN_FAMILIAS_INDEPENDENTES=2
JANELA_ADAPTATIVA_BRANCAS=600  # padrões aprendidos com as 600 Brancas mais recentes
ATRASO_MAXIMO_API_MIN=2

BASE=os.environ.get('DATA_DIR','/var/data')
os.makedirs(BASE, exist_ok=True)
ARQ=os.path.join(BASE,'historico_acumulado.json')
if not os.path.isfile(ARQ): raise FileNotFoundError('Histórico ausente no disco persistente: '+ARQ)
with open(ARQ,encoding='utf-8') as f: old=json.load(f)
if isinstance(old,dict): old=next((old[k] for k in ('data','results','rounds','history','records') if isinstance(old.get(k),list)),None)
if not isinstance(old,list): raise ValueError('Formato do histórico não reconhecido; arquivo original preservado.')
api_ok=True; erro_api=''
try:
    r=requests.get(API_URL,params={'limit':200,'timezone':'America/Sao_Paulo','_cb':int(datetime.now(timezone.utc).timestamp()*1000)},headers={'User-Agent':'Mozilla/5.0','Accept':'application/json, text/plain, */*','Referer':'https://www.tipminer.com/'},timeout=30)
    r.raise_for_status(); payload=r.json()
    new=payload if isinstance(payload,list) else next((payload[k] for k in ('data','results','rounds','history','records') if isinstance(payload.get(k),list)),[])
    if not new: raise RuntimeError('API não retornou rodadas.')
except Exception as e:
    api_ok=False; erro_api=str(e); new=[]
    
backup=os.path.join(BASE,'historico_acumulado_backup_robo_principal.json')
if not os.path.exists(backup): shutil.copy2(ARQ,backup)
byid={}; noid={}
for z in old+new:
    if not isinstance(z,dict): continue
    uid=str(z.get('uuid','')).strip()
    if uid: byid[uid]=z
    else: noid[(str(z.get('instant','')),str(z.get('result','')))]=z
merged=list(byid.values())+list(noid.values()); merged.sort(key=lambda x:str(x.get('instant','')))
if api_ok:
    fd,tmp=tempfile.mkstemp(prefix='tipminer_',suffix='.json',dir=BASE); os.close(fd)
    try:
        with open(tmp,'w',encoding='utf-8') as f: json.dump(merged,f,ensure_ascii=False,indent=2)
        os.replace(tmp,ARQ)
    except Exception:
        if os.path.exists(tmp): os.remove(tmp)
        raise


lin=[]; descartadas=0
for z in merged:
    try:
        v=int(z['result']); dt=datetime.fromisoformat(str(z['instant']).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        if not 0<=v<=14: raise ValueError
        lin.append({'uuid':str(z.get('uuid','')),'data_hora':dt.astimezone(FUSO),'numero':v,'cor':SIMBOLO_COR[v]})
    except Exception: descartadas+=1
df=pd.DataFrame(lin).sort_values('data_hora').drop_duplicates(subset=['uuid'] if any(x['uuid'] for x in lin) else ['data_hora','numero'],keep='last').reset_index(drop=True)
if len(df)<100: raise ValueError('Histórico insuficiente para iniciar o robô principal.')


# ================================================================
# V14 — MOTOR ORIENTADO A EVENTOS DE BRANCA
# Cada UUID de Branca é reconhecido uma única vez. A cada execução,
# os vizinhos cronológicos são reavaliados: o posterior pode ainda
# não existir na primeira consulta e será preenchido na próxima.
# ================================================================
mem_eventos_path=os.path.join(BASE,'memoria_eventos_branca_v13.json')
try:
    with open(mem_eventos_path,encoding='utf-8') as f:
        memoria_eventos=json.load(f)
    if not isinstance(memoria_eventos,dict): memoria_eventos={}
except Exception:
    memoria_eventos={}
memoria_eventos.setdefault('eventos',{})
memoria_eventos.setdefault('ids_processados',[])

# Um evento por rodada Branca (UUID); fallback por timestamp quando não há UUID.
for idx, row in df[df.numero.eq(0)].iterrows():
    uid=str(row.get('uuid','')).strip()
    event_id=uid if uid else 'sem-uuid|'+pd.Timestamp(row.data_hora).isoformat()
    memoria_eventos['ids_processados'].append(event_id)
    memoria_eventos['eventos'].setdefault(event_id, {
        'branca_em':pd.Timestamp(row.data_hora).isoformat(),
        'uuid':uid,
        'primeira_deteccao':datetime.now(FUSO).isoformat()
    })

# Atualiza pedras adjacentes em ordem real das rodadas; não presume que
# a pedra posterior já tenha saído. O alvo usa a soma da pedra com o minuto.
for event_id, ev in list(memoria_eventos['eventos'].items()):
    try:
        dt=pd.Timestamp(ev['branca_em'])
        if dt.tzinfo is None: dt=dt.tz_localize(FUSO)
        else: dt=dt.tz_convert(FUSO)
        matches=df.index[df.data_hora.eq(dt)].tolist()
        if not matches and ev.get('uuid'):
            matches=df.index[df.uuid.astype(str).eq(str(ev['uuid']))].tolist()
        if not matches: continue
        pos=int(matches[-1]); updates={}
        for lado, viz in [('anterior',pos-1),('posterior',pos+1)]:
            if 0 <= viz < len(df):
                pedra=int(df.iloc[viz].numero)
                updates[lado+'_pedra']=pedra
                updates[lado+'_em']=pd.Timestamp(df.iloc[viz].data_hora).isoformat()
                updates[lado+'_cor']=str(df.iloc[viz].cor)
                if 1 <= pedra <= 14:
                    updates[lado+'_alvo']=(dt.floor('min')+pd.Timedelta(minutes=pedra)).isoformat()
            else:
                updates[lado+'_pedra']=None
                updates[lado+'_alvo']=None
        ev.update(updates)
    except Exception:
        continue

memoria_eventos['ids_processados']=list(dict.fromkeys(memoria_eventos['ids_processados']))[-20000:]
# Mantém os eventos mais recentes para o arquivo não crescer indefinidamente.
if len(memoria_eventos['eventos'])>3000:
    ordenados=sorted(memoria_eventos['eventos'].items(),key=lambda kv:kv[1].get('branca_em',''))
    memoria_eventos['eventos']=dict(ordenados[-3000:])
memoria_eventos['ultima_execucao']=datetime.now(FUSO).isoformat()
memoria_eventos['ultima_rodada_analisada']=pd.Timestamp(df.data_hora.iloc[-1]).isoformat()
with open(mem_eventos_path,'w',encoding='utf-8') as f:
    json.dump(memoria_eventos,f,ensure_ascii=False,indent=2)

# Painel de eventos: os últimos Brancos comandam a busca; os eventos
# sem pedra posterior ficam explicitamente aguardando, sem inventar alvo.
eventos_recentes=sorted(memoria_eventos['eventos'].values(),key=lambda e:e.get('branca_em',''),reverse=True)[:8]
linhas_eventos=[]
for ev in eventos_recentes:
    try:
        hh=pd.Timestamp(ev['branca_em']).strftime('%H:%M:%S')
        partes=[]
        for lado in ('anterior','posterior'):
            pedra=ev.get(lado+'_pedra')
            alvo=ev.get(lado+'_alvo')
            if pedra is None: partes.append(f'{lado}: aguardando rodada adjacente')
            elif alvo: partes.append(f'{lado}: pedra {int(pedra):02d} → alvo {pd.Timestamp(alvo).strftime("%H:%M")}')
            else: partes.append(f'{lado}: pedra {int(pedra):02d} (sem alvo válido)')
        linhas_eventos.append(f'Branca {hh} — '+' | '.join(partes))
    except Exception:
        continue
texto_eventos_v13='<br>'.join(linhas_eventos) if linhas_eventos else 'Nenhuma Branca identificada no histórico.'

# SOMA ENCADEADA UNIVERSAL — REGRA CORRIGIDA CONFORME A TESE DO USUÁRIO.
# A pedra usada na primeira soma é a que sai no minuto POSTERIOR à Branca.
# Ex.: Branca 11:07; pedra 09 em 11:08; alvo 11:16.
# Se o alvo falhar, cada pedra 1..14 observada nele abre um novo caminho.
df['_minuto_exato']=df.data_hora.dt.floor('min')
rodadas_por_minuto={k:g.numero.astype(int).tolist() for k,g in df.groupby('_minuto_exato',sort=True)}
minutos_com_branca={k for k,vals in rodadas_por_minuto.items() if 0 in vals}
brancas_minutos=sorted(minutos_com_branca)
cadeia_stats={'alvos_1_observados':0,'acertos_1':0,'falhas_1':0,'caminhos_2':0,'acertos_2':0,'caminhos_3':0,'acertos_3':0}
# Histórico: uma cadeia por minuto com Branca; usa somente minutos seguintes já encerrados.
ultima_rodada_dt=df.data_hora.iloc[-1]
for minuto_branca in brancas_minutos:
    minuto_seguinte=minuto_branca+pd.Timedelta(minutes=1)
    if minuto_seguinte not in rodadas_por_minuto or minuto_seguinte+pd.Timedelta(minutes=1)>ultima_rodada_dt:
        continue
    pedras_inicio=[int(p) for p in rodadas_por_minuto[minuto_seguinte] if 1<=int(p)<=14]
    for pedra_inicio in pedras_inicio:
        alvo1=minuto_branca+pd.Timedelta(minutes=pedra_inicio)
        if alvo1 not in rodadas_por_minuto or alvo1+pd.Timedelta(minutes=1)>ultima_rodada_dt:
            continue
        cadeia_stats['alvos_1_observados']+=1
        pedras1=rodadas_por_minuto[alvo1]
        if 0 in pedras1:
            cadeia_stats['acertos_1']+=1
            continue
        cadeia_stats['falhas_1']+=1
        for pedra1 in pedras1:
            if not 1<=int(pedra1)<=14: continue
            alvo2=alvo1+pd.Timedelta(minutes=int(pedra1))
            if alvo2 not in rodadas_por_minuto or alvo2+pd.Timedelta(minutes=1)>ultima_rodada_dt:
                continue
            cadeia_stats['caminhos_2']+=1
            pedras2=rodadas_por_minuto[alvo2]
            if 0 in pedras2:
                cadeia_stats['acertos_2']+=1
                continue
            for pedra2 in pedras2:
                if not 1<=int(pedra2)<=14: continue
                alvo3=alvo2+pd.Timedelta(minutes=int(pedra2))
                if alvo3 not in rodadas_por_minuto or alvo3+pd.Timedelta(minutes=1)>ultima_rodada_dt:
                    continue
                cadeia_stats['caminhos_3']+=1
                if 0 in rodadas_por_minuto[alvo3]:
                    cadeia_stats['acertos_3']+=1
pct1=100*cadeia_stats['acertos_1']/max(1,cadeia_stats['alvos_1_observados'])
pct2=100*cadeia_stats['acertos_2']/max(1,cadeia_stats['caminhos_2'])
pct3=100*cadeia_stats['acertos_3']/max(1,cadeia_stats['caminhos_3'])

# Acompanhamento ao vivo da cadeia mais recente, usando a pedra do minuto posterior à Branca.
agora_cadeia=pd.Timestamp.now(tz=FUSO)
cadeia_ativa=[]
status_cadeia='Sem cadeia ativa identificada'
origem_cadeia=None
if brancas_minutos:
    origem_cadeia=brancas_minutos[-1]
    minuto_seguinte=origem_cadeia+pd.Timedelta(minutes=1)
    if minuto_seguinte>ultima_rodada_dt:
        status_cadeia='Aguardando a pedra do minuto posterior à Branca'
    elif minuto_seguinte not in rodadas_por_minuto:
        status_cadeia='Aguardando atualização do minuto posterior à Branca'
    else:
        pedras_inicio=[int(p) for p in rodadas_por_minuto[minuto_seguinte] if 1<=int(p)<=14]
        if not pedras_inicio:
            status_cadeia='Minuto posterior sem pedra válida para iniciar a soma'
        else:
            for pedra_inicio in pedras_inicio:
                alvo1=origem_cadeia+pd.Timedelta(minutes=pedra_inicio)
                if alvo1>agora_cadeia.floor('min'):
                    cadeia_ativa.append((1,alvo1))
                    continue
                if alvo1 not in rodadas_por_minuto or alvo1+pd.Timedelta(minutes=1)>ultima_rodada_dt:
                    status_cadeia='Aguardando atualização do primeiro minuto-alvo'
                    continue
                pedras1=rodadas_por_minuto[alvo1]
                if 0 in pedras1:
                    status_cadeia='Primeira soma encontrou Branca'
                    continue
                for pedra1 in pedras1:
                    if not 1<=int(pedra1)<=14: continue
                    alvo2=alvo1+pd.Timedelta(minutes=int(pedra1))
                    if alvo2>agora_cadeia.floor('min'):
                        cadeia_ativa.append((2,alvo2))
                        continue
                    if alvo2 not in rodadas_por_minuto or alvo2+pd.Timedelta(minutes=1)>ultima_rodada_dt:
                        status_cadeia='Aguardando atualização do segundo minuto-alvo'
                        continue
                    pedras2=rodadas_por_minuto[alvo2]
                    if 0 in pedras2:
                        status_cadeia='Segunda soma encontrou Branca'
                        continue
                    for pedra2 in pedras2:
                        if not 1<=int(pedra2)<=14: continue
                        alvo3=alvo2+pd.Timedelta(minutes=int(pedra2))
                        if alvo3>agora_cadeia.floor('min'):
                            cadeia_ativa.append((3,alvo3))
            if cadeia_ativa:
                status_cadeia='Acompanhando somas seguintes'
            elif status_cadeia=='Sem cadeia ativa identificada':
                status_cadeia='Cadeia anterior sem alvo futuro'

# Memória de desempenho em modo sombra: registra métricas, sem alterar a liberação dos sinais.
memoria_sombra={
    'atualizado_em':agora_cadeia.isoformat(),
    'rodadas_analisadas':int(len(df)),
    'regra_inicio':'pedra do minuto posterior à Branca',
    'etapas':{
        '1':{'casos':int(cadeia_stats['alvos_1_observados']),'brancas':int(cadeia_stats['acertos_1']),'taxa_pct':round(pct1,3)},
        '2':{'caminhos':int(cadeia_stats['caminhos_2']),'brancas':int(cadeia_stats['acertos_2']),'taxa_pct':round(pct2,3)},
        '3':{'caminhos':int(cadeia_stats['caminhos_3']),'brancas':int(cadeia_stats['acertos_3']),'taxa_pct':round(pct3,3)}
    },
    'cadeia_mais_recente':{'branca_em':origem_cadeia.isoformat() if origem_cadeia is not None else None,
                           'status':status_cadeia,
                           'alvos': [{'etapa':int(e),'horario':t.isoformat()} for e,t in cadeia_ativa]}
}
try:
    memoria_path=os.path.join(BASE,'memoria_desempenho_encadeado.json')
    with open(memoria_path,'w',encoding='utf-8') as f: json.dump(memoria_sombra,f,ensure_ascii=False,indent=2)
except Exception as e:
    print('Aviso: não foi possível salvar a memória sombra:',e)

# PESQUISA EXPERIMENTAL V3 — SOMAS ANTERIOR/POSTERIOR + CONCORDÂNCIA DE CORES.
# Fica em modo sombra: não altera regras, pesos nem liberação dos sinais.
# A pedra de referência vem do minuto anterior OU posterior à Branca de origem.
# Quando o alvo falha, cada pedra do minuto-alvo abre um novo caminho (até etapa 3).

def cor_pedra(p):
    return SIMBOLO_COR.get(int(p), 'Desconhecida')

# Indexa as rodadas por minuto e mantém a ordem cronológica para identificar
# a pedra imediatamente anterior a cada Branca que ocorre no minuto-alvo.
idx_por_minuto={}
for ix, row in df.iterrows():
    idx_por_minuto.setdefault(row.data_hora.floor('min'), []).append(ix)

exp_stats={}
exp_eventos=[]
ultima_minuto=df.data_hora.max().floor('min')
for direcao, desloc in [('anterior',-1),('posterior',1)]:
    stats={'referencias':0,'alvos_avaliados':0,'brancas_alvo':0,
           'cores_comparaveis':0,'concordancias':0,
           'etapa2_caminhos':0,'etapa2_brancas':0,
           'etapa3_caminhos':0,'etapa3_brancas':0}
    for minuto_origem in brancas_minutos:
        minuto_ref=minuto_origem+pd.Timedelta(minutes=desloc)
        if minuto_ref not in idx_por_minuto: continue
        pedras_ref=[int(df.iloc[ix].numero) for ix in idx_por_minuto[minuto_ref] if 1<=int(df.iloc[ix].numero)<=14]
        for pedra_ref in pedras_ref:
            stats['referencias']+=1
            cor_ref=cor_pedra(pedra_ref)
            alvo=minuto_origem+pd.Timedelta(minutes=pedra_ref)
            if alvo not in idx_por_minuto or alvo>=ultima_minuto: continue
            stats['alvos_avaliados']+=1
            brancas_alvo=[ix for ix in idx_por_minuto[alvo] if int(df.iloc[ix].numero)==0]
            if brancas_alvo:
                stats['brancas_alvo']+=1
                for ixw in brancas_alvo:
                    prev_ix=ixw-1
                    if prev_ix>=0:
                        cor_prev=str(df.iloc[prev_ix].cor)
                        stats['cores_comparaveis']+=1
                        if cor_prev==cor_ref: stats['concordancias']+=1
                continue
            # Etapa 2: cada pedra não branca que saiu no primeiro alvo gera novo alvo.
            for ix1 in idx_por_minuto[alvo]:
                pedra1=int(df.iloc[ix1].numero)
                if not 1<=pedra1<=14: continue
                alvo2=alvo+pd.Timedelta(minutes=pedra1)
                if alvo2 not in idx_por_minuto or alvo2>=ultima_minuto: continue
                stats['etapa2_caminhos']+=1
                brancas2=[ix for ix in idx_por_minuto[alvo2] if int(df.iloc[ix].numero)==0]
                if brancas2:
                    stats['etapa2_brancas']+=1
                    for ixw in brancas2:
                        prev_ix=ixw-1
                        if prev_ix>=0:
                            stats['cores_comparaveis']+=1
                            if str(df.iloc[prev_ix].cor)==cor_ref: stats['concordancias']+=1
                    continue
                # Etapa 3: mesma regra universal, usando cada pedra do segundo alvo.
                for ix2 in idx_por_minuto[alvo2]:
                    pedra2=int(df.iloc[ix2].numero)
                    if not 1<=pedra2<=14: continue
                    alvo3=alvo2+pd.Timedelta(minutes=pedra2)
                    if alvo3 not in idx_por_minuto or alvo3>=ultima_minuto: continue
                    stats['etapa3_caminhos']+=1
                    brancas3=[ix for ix in idx_por_minuto[alvo3] if int(df.iloc[ix].numero)==0]
                    if brancas3:
                        stats['etapa3_brancas']+=1
                        for ixw in brancas3:
                            prev_ix=ixw-1
                            if prev_ix>=0:
                                stats['cores_comparaveis']+=1
                                if str(df.iloc[prev_ix].cor)==cor_ref: stats['concordancias']+=1
    stats['taxa_alvo_pct']=round(100*stats['brancas_alvo']/max(1,stats['alvos_avaliados']),2)
    stats['taxa_concordancia_pct']=round(100*stats['concordancias']/max(1,stats['cores_comparaveis']),2)
    stats['taxa_etapa2_pct']=round(100*stats['etapa2_brancas']/max(1,stats['etapa2_caminhos']),2)
    stats['taxa_etapa3_pct']=round(100*stats['etapa3_brancas']/max(1,stats['etapa3_caminhos']),2)
    exp_stats[direcao]=stats

memoria_experimental={
    'atualizado_em':agora_cadeia.isoformat(),
    'modo':'observacao_sombra_sem_influencia_nos_sinais',
    'regras':{
        'referencias_testadas':['pedra do minuto anterior à Branca','pedra do minuto posterior à Branca'],
        'somas_encadeadas':'cada pedra 1 a 14 dos minutos-alvo pode abrir a etapa seguinte',
        'concordancia_cor':'cor da pedra de referência comparada à pedra imediatamente anterior à Branca encontrada no alvo',
        'etapas_maximas':3
    },
    'resultados':exp_stats,
    'aviso':'Taxas descritivas históricas; caminhos podem se sobrepor e não representam probabilidade garantida.'
}
memoria_exp_path=os.path.join(BASE,'memoria_experimental_somas_cores.json')
try:
    with open(memoria_exp_path,'w',encoding='utf-8') as f:
        json.dump(memoria_experimental,f,ensure_ascii=False,indent=2)
except Exception as e:
    print('Aviso: memória experimental de somas e cores não salva:',e)

stats_ant=exp_stats['anterior']; stats_pos=exp_stats['posterior']
texto_experimento=(
    f"Pedra anterior: {stats_ant['brancas_alvo']}/{stats_ant['alvos_avaliados']} alvos com Branca; "
    f"etapa 2: {stats_ant['etapa2_brancas']}/{stats_ant['etapa2_caminhos']}; etapa 3: {stats_ant['etapa3_brancas']}/{stats_ant['etapa3_caminhos']}.<br>"
    f"Pedra posterior: {stats_pos['brancas_alvo']}/{stats_pos['alvos_avaliados']} alvos com Branca; "
    f"etapa 2: {stats_pos['etapa2_brancas']}/{stats_pos['etapa2_caminhos']}; etapa 3: {stats_pos['etapa3_brancas']}/{stats_pos['etapa3_caminhos']}.<br>"
    f"Concordância de cor observada nos alvos com Branca: anterior {stats_ant['concordancias']}/{stats_ant['cores_comparaveis']}; "
    f"posterior {stats_pos['concordancias']}/{stats_pos['cores_comparaveis']}."
)

def texto_cadeia_ativa():
    if not cadeia_ativa: return status_cadeia
    alvos=sorted(set(cadeia_ativa),key=lambda x:(x[1],x[0]))[:8]
    return status_cadeia+' — '+' | '.join(f'etapa {etapa}: {alvo.strftime("%H:%M")}' for etapa,alvo in alvos)

df['minuto']=df.data_hora.dt.minute
df['pedra_anterior']=df.numero.shift(1)
df['cor_anterior']=df.cor.shift(1)
# Cada linha de b é uma Branca. Todas as variáveis explicativas usam apenas eventos anteriores.
b=df[df.numero.eq(0)].copy().reset_index()
b=b.rename(columns={'index':'idx'})
b['pedra_indicadora']=b.idx.map(df.numero.shift(1).to_dict())
b['minuto_branca']=b.data_hora.dt.minute
b['intervalo_rodadas']=b.idx.diff()
b['intervalo_minutos']=(b.data_hora.diff().dt.total_seconds()/60)
b['minuto_branca_anterior']=b.minuto_branca.shift(1)
b['pedra_indicadora_anterior']=b.pedra_indicadora.shift(1)
b['pedra_indicadora_2']=b.pedra_indicadora.shift(2)
b['pedra_indicadora_3']=b.pedra_indicadora.shift(3)
b['soma_direta']=(b.minuto_branca_anterior+b.pedra_indicadora_anterior)%60
# Soma dos dígitos do minuto da Branca anterior + soma dos dígitos da pedra indicadora anterior
def sd(x): return sum(int(a) for a in str(abs(int(x))))
b['soma_digitos']=[(sd(m)+sd(p))%60 if pd.notna(m) and pd.notna(p) else np.nan for m,p in zip(b.minuto_branca_anterior,b.pedra_indicadora_anterior)]
b['soma_2_pedras']=(b.pedra_indicadora_anterior+b.pedra_indicadora_2)%60
# Novas representações agrupadas: investigam padrões que não aparecem nos valores exatos.
# Elas são apenas variáveis candidatas; os mesmos mínimos e a validação cronológica continuam valendo.
for nome, serie in [('soma_direta', b['soma_direta']), ('soma_digitos', b['soma_digitos']), ('soma_2_pedras', b['soma_2_pedras'])]:
    b[f'{nome}_paridade'] = serie.map(lambda x: 'par' if pd.notna(x) and int(x)%2==0 else ('ímpar' if pd.notna(x) else np.nan))
    b[f'{nome}_final'] = serie.map(lambda x: f'final {int(x)%10}' if pd.notna(x) else np.nan)
    b[f'{nome}_faixa10'] = pd.cut(serie, bins=[-1,9,19,29,39,49,59], labels=['00-09','10-19','20-29','30-39','40-49','50-59']).astype('object')
b['seq_pedras']=b.apply(lambda x: '-'.join(str(int(v)) for v in [x.pedra_indicadora_3,x.pedra_indicadora_2,x.pedra_indicadora_anterior]) if pd.notna(x.pedra_indicadora_3) and pd.notna(x.pedra_indicadora_2) and pd.notna(x.pedra_indicadora_anterior) else None,axis=1)
# Três cores imediatamente anteriores à Branca-alvo
cores=[]
for ix in b.idx:
    prev=df.iloc[max(0,int(ix)-3):int(ix)].cor.tolist()
    cores.append('-'.join(prev) if len(prev)==3 else None)
b['seq_3_cores']=cores
b['faixa_intervalo']=pd.cut(b.intervalo_rodadas,bins=[0,5,10,20,40,80,160,np.inf],labels=['1-5','6-10','11-20','21-40','41-80','81-160','161+']).astype('object')
b['faixa_minutos']=pd.cut(b.intervalo_minutos,bins=[0,5,15,30,60,120,360,np.inf],labels=['0-5m','5-15m','15-30m','30-60m','1-2h','2-6h','6h+']).astype('object')
b['pedra_especial']=b.pedra_indicadora.map(lambda x: f'Pedra {int(x)}' if pd.notna(x) and int(x) in PEDRAS_ATENCAO else 'Outras')

# Novos fatores de contexto: frequência de Brancas e distância desde a última.
# Para cada Branca histórica, usa somente as rodadas anteriores ao evento-alvo.
def faixa_freq(q):
    return '0' if q==0 else ('1' if q==1 else ('2-3' if q<=3 else '4+'))
def faixa_gap(q):
    return '0-2' if q<=2 else ('3-5' if q<=5 else ('6-10' if q<=10 else ('11-20' if q<=20 else '21+')))
for janela in (30,50,100):
    vals=[]
    for ix in b.idx.astype(int):
        ini=max(0,ix-janela)
        vals.append(faixa_freq(int(df.iloc[ini:ix].numero.eq(0).sum())))
    b[f'freq_branca_{janela}']=vals
gaps=[]
for ix in b.idx.astype(int):
    anteriores=b.idx[b.idx<ix]
    gaps.append(int(ix-anteriores.iloc[-1]) if len(anteriores) else 9999)
b['gap_branca_atual']= [faixa_gap(x) for x in gaps]

b=b.dropna(subset=['pedra_indicadora','minuto_branca']).reset_index(drop=True)
b['pedra_indicadora']=b.pedra_indicadora.astype(int)




# Janela móvel: prioriza o comportamento recente para acompanhar mudanças de padrão.
# A divisão continua cronológica: treino vem antes da validação, sem olhar o futuro.
# Histórico completo para não descartar padrões antigos; a validação é cronológica.
# A janela recente é medida separadamente e usada como camada de adaptação,
# sem substituir o histórico completo.
b_modelo=b.copy().reset_index(drop=True)
split=max(1,int(len(b_modelo)*0.70))
treino=b_modelo.iloc[:split].copy()
valid=b_modelo.iloc[split:].copy()
if len(valid)<MIN_VALIDACAO:
    raise ValueError('Histórico recente insuficiente para validar os padrões com segurança.')
# Famílias de fatores combinadas, todas com dados anteriores ao alvo.
features={
 'Pedra imediatamente anterior':'pedra_indicadora',
 'Pedras especiais (3,5,6,10,13)':'pedra_especial',
 'Intervalo em rodadas':'faixa_intervalo',
 'Intervalo em tempo':'faixa_minutos',
 'Sequência das 3 pedras indicadoras':'seq_pedras',
 'Sequência das 3 cores anteriores':'seq_3_cores',
 'Soma direta':'soma_direta',
 'Soma dos dígitos':'soma_digitos',
 'Soma de 2 pedras indicadoras':'soma_2_pedras',
 'Frequência de Brancas nas últimas 30 rodadas':'freq_branca_30',
 'Frequência de Brancas nas últimas 50 rodadas':'freq_branca_50',
 'Frequência de Brancas nas últimas 100 rodadas':'freq_branca_100',
 'Distância desde a última Branca':'gap_branca_atual',
 'Paridade da soma direta' :'soma_direta_paridade',
 'Final da soma direta':'soma_direta_final',
 'Faixa da soma direta':'soma_direta_faixa10',
 'Paridade da soma dos dígitos':'soma_digitos_paridade',
 'Final da soma dos dígitos':'soma_digitos_final',
 'Faixa da soma dos dígitos':'soma_digitos_faixa10',
 'Paridade da soma de 2 pedras':'soma_2_pedras_paridade',
 'Final da soma de 2 pedras':'soma_2_pedras_final',
 'Faixa da soma de 2 pedras':'soma_2_pedras_faixa10'
}
# Cruzamentos enxutos: combinações de 2 e 3 fatores entre os 8 fatores-base
# mais informativos. As variações de soma (paridade/final/faixa) continuam
# avaliadas individualmente em features, evitando centenas de cruzamentos lentos.
from itertools import combinations
combo_map={}  # guarda as colunas exatas para reconstruir o contexto atual
base_fatores=['pedra_especial','faixa_intervalo','faixa_minutos','seq_pedras','seq_3_cores','soma_direta','soma_digitos','soma_2_pedras']
# IMPORTANTE: apenas 8 fatores-base geram 84 cruzamentos (em vez de 833).
# As regras individuais continuam preservadas; isso reduz bastante o tempo de análise.
novas_treino={}; novas_valid={}
for tamanho in (2,3):
    for cols in combinations(base_fatores,tamanho):
        nm=' + '.join(cols)
        col='__combo_'+str(tamanho)+'_'+str(len(features))
        # Conversão e junção vetorizadas por bloco; valores ausentes ficam explícitos.
        novas_treino[col]=treino[list(cols)].astype('string').fillna('<AUSENTE>').agg(' | '.join,axis=1)
        novas_valid[col]=valid[list(cols)].astype('string').fillna('<AUSENTE>').agg(' | '.join,axis=1)
        features[nm]=col
        combo_map[nm]=cols
if novas_treino:
    treino=pd.concat([treino, pd.DataFrame(novas_treino,index=treino.index)],axis=1).copy()
    valid=pd.concat([valid, pd.DataFrame(novas_valid,index=valid.index)],axis=1).copy()

# Para cada regra, escolhe até 3 minutos-alvo mais frequentes na janela móvel recente; avalia no bloco futuro.
# O mínimo de validação é 20 casos por regra.
base_global=treino.minuto_branca.value_counts(normalize=True)
base_ex=float(base_global.nlargest(3).sum()) if len(base_global) else np.nan
base_pm=float(sum(base_global.get(m,0) for m in range(60)))
resultados=[]; regras=[]
for nome,col in features.items():
    tab=treino.dropna(subset=[col]).groupby(col,dropna=False).minuto_branca.agg(['count',lambda s:s.value_counts().head(3).index.tolist()]).reset_index()
    tab.columns=['padrao','n_treino','minutos_previstos']
    tab=tab[tab.n_treino>=MIN_TREINO]
    for _,q in tab.iterrows():
        pad=q.padrao; pred=set(int(x) for x in q.minutos_previstos)
        casos=valid[valid[col].astype(str).eq(str(pad))]
        if len(casos)<MIN_VALIDACAO: continue
        exact=casos.minuto_branca.astype(int).isin(pred).mean()*100
        near=casos.minuto_branca.astype(int).map(lambda m:any(min((m-x)%60,(x-m)%60)<=1 for x in pred)).mean()*100
        regras.append({'Família':nome,'Padrão':str(pad),'Casos treino':int(q.n_treino),'Minutos previstos':', '.join(map(str,sorted(pred))),'Casos validação':len(casos),'Exato validação (%)':round(exact,2),'±1 validação (%)':round(near,2),'Excesso vs base global (p.p.)':round(exact-base_ex*100,2)})
if regras:
    ranking=pd.DataFrame(regras).sort_values(['Exato validação (%)','Casos validação'],ascending=[False,False]).reset_index(drop=True)
else: ranking=pd.DataFrame(columns=['Família','Padrão','Casos treino','Minutos previstos','Casos validação','Exato validação (%)','±1 validação (%)','Excesso vs base global (p.p.)'])

# Sumário compacto por família: melhor regra e volume, sem despejar dezenas de tabelas.
if not ranking.empty:
    resumo_familias=(ranking.sort_values('Exato validação (%)',ascending=False).groupby('Família',as_index=False).first().sort_values('Exato validação (%)',ascending=False))
else: resumo_familias=pd.DataFrame()

# Detector simples de mudança: compara o bloco recente com o histórico anterior.
# A comparação é descritiva; não presume que uma mudança seja previsível.
recent=b_modelo.tail(min(100,len(b_modelo)))
anterior=b_modelo.iloc[:-len(recent)] if len(b_modelo)>len(recent) else b_modelo.iloc[0:0]
comp=[]
for label,sub in [('Histórico anterior',anterior),('Últimas até 100 Brancas',recent)]:
    if len(sub):
        comp.append({'Período':label,'Brancas':len(sub),
          'Pedra 3/5/6/10/13 (%)':round(sub.pedra_indicadora.isin(PEDRAS_ATENCAO).mean()*100,2),
          'Intervalo mediano (rodadas)':round(sub.intervalo_rodadas.median(),1),
          'Minuto mais frequente':int(sub.minuto_branca.mode().iloc[0])})
comparacao=pd.DataFrame(comp)
if len(anterior) and len(recent):
    dif_pedra=recent.pedra_indicadora.isin(PEDRAS_ATENCAO).mean()*100-anterior.pedra_indicadora.isin(PEDRAS_ATENCAO).mean()*100
    gap_ant=float(anterior.intervalo_rodadas.median()); gap_rec=float(recent.intervalo_rodadas.median())
    if abs(dif_pedra)>=8 or (gap_ant>0 and abs(gap_rec-gap_ant)/gap_ant>=0.25):
        regime_txt='Mudança recente detectada: o comportamento das últimas Brancas difere do histórico anterior.'
    else:
        regime_txt='Comportamento recente relativamente alinhado ao histórico anterior.'
else:
    regime_txt='Amostra ainda insuficiente para comparar comportamento recente e histórico anterior.'

# Painel prático atual: última 4 principal e última 3 comparação, somente fórmulas já combinadas.
ult4=b.tail(4).copy(); ult3=b.tail(3).copy()
def painel(t,nome):
    out=[]
    for _,z in t.iterrows():
        out.append({'Janela':nome,'Branca referência':z.data_hora.strftime('%d/%m %H:%M:%S'),'Minuto Branca':int(z.minuto_branca),'Pedra anterior':int(z.pedra_indicadora),'Soma direta/candidato':int((z.minuto_branca+z.pedra_indicadora)%60),'Soma dígitos':z.soma_digitos,'Pedras especiais?':int(z.pedra_indicadora) in PEDRAS_ATENCAO})
    return out
painel_atual=pd.DataFrame(painel(ult4,'Últimas 4')+painel(ult3,'Últimas 3'))


# PAINEL ESPECÍFICO DE SOMAS — evita que as regras de soma fiquem escondidas no TOP 20 geral.
mask_somas = ranking['Família'].astype(str).str.contains(r'soma|pedras indicadoras', case=False, regex=True) if not ranking.empty else pd.Series([], dtype=bool)
ranking_somas = ranking[mask_somas].copy() if not ranking.empty else pd.DataFrame()

# Ranking de cruzamentos de exatamente 2 ou 3 fatores que incluem ao menos uma soma.
nomes_combo_soma = [n for n, cols in combo_map.items() if any(c.startswith('soma_') for c in cols)]
ranking_combos_soma = ranking[ranking['Família'].isin(nomes_combo_soma)].copy() if not ranking.empty else pd.DataFrame()

# Sinal experimental só se houver padrão validado com amostra e excesso positivo; nunca garantido.
validos=ranking[(ranking['Casos validação']>=MIN_VALIDACAO)&(ranking['Excesso vs base global (p.p.)']>0)] if not ranking.empty else ranking

# ------------------------------------------------------------
# PAINEL INTEGRADO ATUAL — MINUTOS + CONTEXTO + COBERTURA
# ------------------------------------------------------------
# O contexto atual é comparado com regras descobertas no treino e
# avaliadas no bloco posterior. Não cria regra nova olhando o futuro.
ultima=b.iloc[-1]
contexto_atual={}
for nome,col in features.items():
    if col in b.columns:
        contexto_atual[nome]=ultima[col]
    elif nome in combo_map:
        cols=combo_map[nome]
        contexto_atual[nome]=' | '.join(str(ultima[c]) for c in cols)

# Atualiza os fatores recentes com a situação da última rodada recebida,
# em vez de reutilizar o contexto anterior à última Branca.
pos_atual=len(df)
for janela in (30,50,100):
    qtd=int(df.tail(janela).numero.eq(0).sum())
    contexto_atual[f'Frequência de Brancas nas últimas {janela} rodadas']=faixa_freq(qtd)
ultimas_brancas=df.index[df.numero.eq(0)].tolist()
gap_atual=(pos_atual-1-ultimas_brancas[-1]) if ultimas_brancas else 9999
contexto_atual['Distância desde a última Branca']=faixa_gap(int(gap_atual))

regras_ativas=[]
if not ranking.empty:
    for _,rr in ranking.iterrows():
        nome=rr['Família']
        valor=contexto_atual.get(nome,None)
        if valor is not None and str(valor)==str(rr['Padrão']):
            mins=[]
            try: mins=[int(x.strip()) for x in str(rr['Minutos previstos']).split(',')]
            except Exception: mins=[]
            if mins:
                regras_ativas.append({
                    'Família':nome,'Padrão atual':str(rr['Padrão']),
                    'Minutos candidatos':', '.join(map(str,mins)),
                    'Casos treino':int(rr['Casos treino']),
                    'Casos validação':int(rr['Casos validação']),
                    'Exato validação (%)':float(rr['Exato validação (%)']),
                    '±1 validação (%)':float(rr['±1 validação (%)']),
                    'Excesso vs base (p.p.)':float(rr['Excesso vs base global (p.p.)']),
                    'Fatores independentes':tuple({'pedra_especial':'pedra_indicadora'}.get(f,f) for f in combo_map.get(nome, ({
                        'Pedra imediatamente anterior':'pedra_indicadora',
                        'Pedras especiais (3,5,6,10,13)':'pedra_indicadora',
                        'Intervalo em rodadas':'faixa_intervalo',
                        'Intervalo em tempo':'faixa_minutos',
                        'Sequência das 3 pedras indicadoras':'seq_pedras',
                        'Sequência das 3 cores anteriores':'seq_3_cores',
                        'Soma direta':'soma_direta',
                        'Soma dos dígitos':'soma_digitos',
                        'Soma de 2 pedras indicadoras':'soma_2_pedras'
                    }.get(nome,nome),)))
                })
painel_regras=pd.DataFrame(regras_ativas)
if not painel_regras.empty:
    painel_regras=painel_regras.sort_values(
        ['Excesso vs base (p.p.)','Casos validação'],ascending=[False,False]
    ).reset_index(drop=True)

# Consolida minutos e mede concordância entre famílias realmente independentes.
# Duas regras só contam como independentes quando não compartilham fatores-base;
# independência aqui é operacional, não uma prova estatística ou causal.
agregado={}
for _,rr in painel_regras.iterrows():
    for mm in [int(x.strip()) for x in rr['Minutos candidatos'].split(',')]:
        a=agregado.setdefault(mm,{'Minuto':mm,'Regras coincidentes':0,'Melhor excesso (p.p.)':-999.0,
            'Casos validação somados (não independentes)':0,'Famílias':[],'Regras elegíveis':[]})
        a['Regras coincidentes']+=1
        a['Melhor excesso (p.p.)']=max(a['Melhor excesso (p.p.)'],rr['Excesso vs base (p.p.)'])
        a['Casos validação somados (não independentes)']+=int(rr['Casos validação'])
        a['Famílias'].append(rr['Família'])
        if int(rr['Casos validação'])>=MIN_VALIDACAO and float(rr['Excesso vs base (p.p.)'])>0:
            a['Regras elegíveis'].append(rr.to_dict())

def selecionar_independentes(regras_candidatas):
    escolhidas=[]; fatores_usados=set()
    # prioriza melhor excesso e, em empate, maior validação
    for regra in sorted(regras_candidatas,key=lambda z:(z['Excesso vs base (p.p.)'],z['Casos validação']),reverse=True):
        fatores=set(regra.get('Fatores independentes',()))
        if fatores and not (fatores & fatores_usados):
            escolhidas.append(regra); fatores_usados.update(fatores)
    return escolhidas

linhas=[]
for mm,a in agregado.items():
    independentes=selecionar_independentes(a['Regras elegíveis'])
    linhas.append({'Minuto':mm,'Regras coincidentes':a['Regras coincidentes'],
        'Famílias independentes elegíveis':len(independentes),
        'Casos validação somados (não independentes)':a['Casos validação somados (não independentes)'],
        'Melhor excesso (p.p.)':a['Melhor excesso (p.p.)'],
        'Famílias':'; '.join(dict.fromkeys(a['Famílias'])),
        'Famílias independentes usadas':'; '.join(r['Família'] for r in independentes),
        'Critérios mínimos atendidos':len(independentes)>=MIN_FAMILIAS_INDEPENDENTES,
        'Critério moderado atendido':(len(independentes)>=1 and any(int(r['Casos validação'])>=MIN_VALIDACAO_MODERADO and float(r['Excesso vs base (p.p.)'])>0 for r in independentes))})
candidatos_integrados=pd.DataFrame(linhas)

# INTEGRAÇÃO REAL DA SOMA ENCADEADA NA DECISÃO:
# cada alvo futuro ativo entra como uma família adicional, desde que a etapa
# tenha amostra histórica mínima e desempenho acima da taxa-base de minutos.
taxa_base_cadeia=100*len(minutos_com_branca)/max(1,len(rodadas_por_minuto))
metricas_etapa={
    1:(cadeia_stats['acertos_1'],cadeia_stats['alvos_1_observados']),
    2:(cadeia_stats['acertos_2'],cadeia_stats['caminhos_2']),
    3:(cadeia_stats['acertos_3'],cadeia_stats['caminhos_3'])
}
linhas_cadeia=[]
for etapa, alvo in sorted(set(cadeia_ativa),key=lambda x:(x[1],x[0])):
    acertos_etapa,amostra_etapa=metricas_etapa[etapa]
    taxa_etapa=100*acertos_etapa/max(1,amostra_etapa)
    excesso_etapa=taxa_etapa-taxa_base_cadeia
    if amostra_etapa>=MIN_VALIDACAO_MODERADO and excesso_etapa>0:
        linhas_cadeia.append({
            'Minuto':int(alvo.minute),
            'Regras coincidentes':1,
            'Famílias independentes elegíveis':1,
            'Casos validação somados (não independentes)':int(amostra_etapa),
            'Melhor excesso (p.p.)':float(excesso_etapa),
            'Famílias':'Soma encadeada — etapa '+str(etapa),
            'Famílias independentes usadas':'Soma encadeada — etapa '+str(etapa),
            'Critérios mínimos atendidos':False,
            'Critério moderado atendido':True,
            '_Horario alvo':alvo,
            '_Etapa cadeia':etapa,
            '_Taxa cadeia (%)':taxa_etapa,
            '_Base cadeia (%)':taxa_base_cadeia
        })
if linhas_cadeia:
    candidatos_integrados=pd.concat([candidatos_integrados,pd.DataFrame(linhas_cadeia)],ignore_index=True,sort=False)

# V11 — HIERARQUIA: SOMAS DIRETAS ORIGINADAS NOS BRANCOS MAIS RECENTES.
# Para cada uma das últimas 6 Brancas, examina a pedra imediatamente anterior
# e a imediatamente posterior. O alvo é minuto da Branca + valor da pedra.
# Um ramo só entra na decisão se houver validação/confluência independente
# já aprovada para aquele minuto; caso contrário fica apenas no monitor.
N_BRANCAS_PRIORITARIAS=6
ultimas_brancas_v11=b.tail(N_BRANCAS_PRIORITARIAS).copy()
linhas_somas_recentes=[]
monitor_somas_recentes=[]
ultima_minuto_v11=df['_minuto_exato'].max()
for pos,(_,zb) in enumerate(ultimas_brancas_v11.iloc[::-1].iterrows()):
    idx_branca=int(zb['idx'])
    minuto_branca=pd.Timestamp(zb['data_hora']).floor('min')
    # prioridade maior para Branca mais recente; ambos os lados têm igual peso.
    prioridade_base=N_BRANCAS_PRIORITARIAS-pos
    vizinhos=[('anterior',idx_branca-1),('posterior',idx_branca+1)]
    for lado,idx_pedra in vizinhos:
        if idx_pedra<0 or idx_pedra>=len(df):
            continue
        pedra=int(df.iloc[idx_pedra]['numero'])
        if not 1<=pedra<=14:
            continue
        alvo=minuto_branca+pd.Timedelta(minutes=pedra)
        registro={'branca_origem':minuto_branca,'lado':lado,'pedra':pedra,'alvo':alvo,'prioridade':prioridade_base}
        monitor_somas_recentes.append(registro)
        # Só considerar alvo futuro em relação à última rodada conhecida.
        if alvo<=ultima_minuto_v11:
            continue
        if candidatos_integrados.empty:
            continue
        mesmo=candidatos_integrados[candidatos_integrados['Minuto'].astype(int).eq(int(alvo.minute))].copy()
        if mesmo.empty:
            continue
        elegiveis=mesmo[mesmo['Critério moderado atendido'].fillna(False) | mesmo['Critérios mínimos atendidos'].fillna(False)]
        if elegiveis.empty:
            continue
        forte=bool(elegiveis['Critérios mínimos atendidos'].fillna(False).any())
        familias_lista=[]
        for valor in elegiveis['Famílias independentes usadas'].dropna().astype(str):
            familias_lista.extend([x.strip() for x in valor.split(';') if x.strip()])
        familias_lista=list(dict.fromkeys(familias_lista))
        n_fam=max(1,max([int(x) for x in elegiveis['Famílias independentes elegíveis'].fillna(1).tolist()]))
        melhor=float(elegiveis['Melhor excesso (p.p.)'].max())
        casos=int(elegiveis['Casos validação somados (não independentes)'].max())
        linhas_somas_recentes.append({
            'Minuto':int(alvo.minute),'Regras coincidentes':len(elegiveis),
            'Famílias independentes elegíveis':n_fam,
            'Casos validação somados (não independentes)':casos,
            'Melhor excesso (p.p.)':melhor,
            'Famílias':'; '.join(familias_lista),
            'Famílias independentes usadas':'; '.join(familias_lista),
            'Critérios mínimos atendidos':forte,
            'Critério moderado atendido':True,
            '_Horario alvo':alvo,
            '_Prioridade soma recente':prioridade_base,
            '_Origem soma recente':f'Branca {minuto_branca.strftime("%H:%M")} + pedra {lado} {pedra} = {alvo.strftime("%H:%M")}',
            '_Pedra soma recente':pedra,
            '_Lado soma recente':lado
        })
if linhas_somas_recentes:
    candidatos_integrados=pd.concat([candidatos_integrados,pd.DataFrame(linhas_somas_recentes)],ignore_index=True,sort=False)
if not candidatos_integrados.empty:
    if '_Prioridade soma recente' not in candidatos_integrados:
        candidatos_integrados['_Prioridade soma recente']=0
    candidatos_integrados['_Prioridade soma recente']=candidatos_integrados['_Prioridade soma recente'].fillna(0)
    candidatos_integrados=candidatos_integrados.sort_values(
        ['_Prioridade soma recente','Critérios mínimos atendidos','Critério moderado atendido','Famílias independentes elegíveis','Melhor excesso (p.p.)'],
        ascending=[False,False,False,False,False]
    ).reset_index(drop=True)


# Cobertura de cores: distribuição observada recente, apresentada como
# contexto descritivo (não como previsão nem como garantia de proteção).
ultimas_200=df.tail(200)
freq_cores=(ultimas_200.cor.value_counts(normalize=True).reindex(
    ['Branca','Amarela','Preta'],fill_value=0
)*100).round(2)
cobertura_cores=pd.DataFrame([
    {'Cor':cor,'Ocorrências últimas 200 rodadas':int((ultimas_200.cor==cor).sum()),
     'Frequência observada (%)':float(freq_cores[cor])}
    for cor in ['Branca','Amarela','Preta']
])

agora_painel=datetime.now(FUSO)
idade_dados_min=(agora_painel-df.iloc[-1].data_hora).total_seconds()/60
dados_atualizados=bool(api_ok and idade_dados_min<=ATRASO_MAXIMO_API_MIN)

# PREVISÃO DINÂMICA: estima o tempo restante até a próxima Branca a partir
# dos intervalos reais entre Brancas recentes, condicionado ao tempo já decorrido.
# Não escolhe mais um dos três minutos fixos mais frequentes do relógio.
brancas_df=df[df.numero.eq(0)]
ultima_branca_dt=brancas_df.iloc[-1].data_hora if not brancas_df.empty else None
minutos_desde_branca=((agora_painel-ultima_branca_dt).total_seconds()/60) if ultima_branca_dt is not None else None
intervalos_recentes=pd.to_numeric(b.intervalo_minutos,errors='coerce').dropna().tail(80)
intervalos_recentes=intervalos_recentes[intervalos_recentes>0]
eta_branca_min=None
amostra_eta=0
metodo_eta='indisponível'
if minutos_desde_branca is not None and len(intervalos_recentes)>=15:
    # Distribuição residual: entre intervalos históricos tão longos quanto o
    # intervalo já observado, calcula quanto ainda faltava até a Branca.
    residuais=intervalos_recentes[intervalos_recentes>=minutos_desde_branca]-minutos_desde_branca
    if len(residuais)>=8:
        eta_branca_min=float(residuais.median())
        amostra_eta=int(len(residuais))
        metodo_eta='intervalos recentes condicionados ao tempo decorrido'
    else:
        # Fallback robusto: mediana móvel das últimas 30 ocorrências,
        # descontando o tempo que já passou desde a última Branca.
        base_intervalos=intervalos_recentes.tail(30)
        eta_branca_min=max(1.0,float(base_intervalos.median())-float(minutos_desde_branca))
        amostra_eta=int(len(base_intervalos))
        metodo_eta='mediana móvel dos últimos intervalos (amostra condicional curta)'
if eta_branca_min is not None:
    horario_dinamico=agora_painel+pd.to_timedelta(max(1.0,eta_branca_min),unit='m')
else:
    horario_dinamico=None

# ================================================================
# V7 — DETECTOR EXPERIMENTAL DE MUDANÇA DE REGIME (MODO OBSERVAÇÃO)
# Não tenta ler o indicador REC da plataforma. Detecta apenas mudanças
# estatísticas no histórico recebido, respeitando a ordem das rodadas.
# Não altera os critérios de liberação dos sinais nesta versão.
# ================================================================
regime_estado='INDEFINIDO — amostra insuficiente'
regime_alertas=[]
regime_metricas=[]
regime_confianca='BAIXA'

if len(df) >= 700:
    janela_atual=df.tail(100).copy()
    janela_base=df.iloc[-600:-100].copy()

    # 1) Mudança na distribuição de cores (100 rodadas recentes vs 500 anteriores).
    cores=['Branca','Amarela','Preta']
    p_atual=janela_atual.cor.value_counts(normalize=True).reindex(cores,fill_value=0)
    p_base=janela_base.cor.value_counts(normalize=True).reindex(cores,fill_value=0)
    delta_cor=(p_atual-p_base).abs()*100
    maior_delta_cor=float(delta_cor.max())
    cor_mais_mudou=str(delta_cor.idxmax())
    if maior_delta_cor >= 8:
        regime_alertas.append('Distribuição de cores mudou: '+cor_mais_mudou+' variou '+f'{maior_delta_cor:.1f} p.p.')
    regime_metricas.append(f'Maior variação de cor: {maior_delta_cor:.1f} p.p. (100 recentes x 500 anteriores)')

    # 2) Mudança na distribuição das pedras 1..14, sem confundir com cor.
    pedras= list(range(1,15))
    n_atual=janela_atual.numero[ janela_atual.numero.between(1,14) ].value_counts(normalize=True).reindex(pedras,fill_value=0)
    n_base=janela_base.numero[ janela_base.numero.between(1,14) ].value_counts(normalize=True).reindex(pedras,fill_value=0)
    delta_pedra=(n_atual-n_base).abs()*100
    maior_delta_pedra=float(delta_pedra.max())
    pedra_mais_mudou=int(delta_pedra.idxmax())
    if maior_delta_pedra >= 5:
        regime_alertas.append(f'Frequência da pedra {pedra_mais_mudou:02d} mudou {maior_delta_pedra:.1f} p.p.')
    regime_metricas.append(f'Maior variação de pedra: {maior_delta_pedra:.1f} p.p.')

    # 3) Mudança dos intervalos entre Brancas, comparando blocos cronológicos.
    ints=pd.to_numeric(b.intervalo_minutos,errors='coerce').dropna()
    ints=ints[ints>0]
    if len(ints)>=40:
        ints_recentes=ints.tail(20)
        ints_base=ints.iloc[-80:-20].tail(60)
        med_rec=float(ints_recentes.median())
        med_base=float(ints_base.median()) if len(ints_base) else float('nan')
        razao_intervalo=(med_rec/med_base) if med_base and med_base>0 else float('nan')
        if np.isfinite(razao_intervalo) and (razao_intervalo>=1.5 or razao_intervalo<=0.67):
            regime_alertas.append(f'Mediana dos intervalos mudou: {med_rec:.1f} min vs {med_base:.1f} min')
        regime_metricas.append(f'Intervalo mediano: recentes {med_rec:.1f} min; base {med_base:.1f} min')
    else:
        razao_intervalo=float('nan')
        regime_metricas.append('Intervalos entre Brancas: amostra insuficiente para comparação')

    # 4) Intervalo atual prolongado em relação ao percentil 90 histórico.
    if minutos_desde_branca is not None and len(ints)>=30:
        q90=float(ints.tail(80).quantile(0.90))
        if minutos_desde_branca > q90:
            regime_alertas.append(f'Tempo sem Branca acima do percentil 90: {minutos_desde_branca:.1f} min vs limite {q90:.1f} min')
            regime_estado='INTERVALO PROLONGADO — comportamento fora da faixa habitual'
        regime_metricas.append(f'Tempo desde a última Branca: {minutos_desde_branca:.1f} min; percentil 90 histórico: {q90:.1f} min')

    # Classificação conservadora: mudança em dois ou mais eixos = transição.
    if regime_estado.startswith('INTERVALO PROLONGADO'):
        if len(regime_alertas)>=3:
            regime_confianca='MODERADA'
    elif len(regime_alertas)>=2:
        regime_estado='TRANSIÇÃO — mudanças em mais de um indicador'
        regime_confianca='MODERADA'
    elif len(regime_alertas)==1:
        regime_estado='ATENÇÃO — mudança isolada, ainda não confirmada'
        regime_confianca='BAIXA'
    else:
        regime_estado='COMPATÍVEL COM A REFERÊNCIA — sem mudança forte detectada'
        regime_confianca='MODERADA' if len(ints)>=40 else 'BAIXA'
else:
    regime_metricas.append(f'Histórico disponível: {len(df)} rodadas; detector requer pelo menos 700 para comparar 100 recentes com 500 anteriores.')

if regime_alertas:
    regime_diagnostico='; '.join(regime_alertas)
else:
    regime_diagnostico='Nenhuma mudança forte detectada nas métricas disponíveis.'
detector_regime_txt=(f'{regime_estado}. Confiança do diagnóstico: {regime_confianca}. '
            + ' | '.join(regime_metricas)
            + ' | '.join([regime_diagnostico, 'Detector estatístico experimental; não identifica diretamente o indicador REC da plataforma. Modo observação: não altera os sinais.']))

# Define o status e a lista de sinais antes de montar o painel visual.
if not dados_atualizados:
    status_painel='SEM SINAL'
    motivo=('API indisponível: '+str(erro_api)) if not api_ok else f'Dados desatualizados: última rodada há {idade_dados_min:.1f} minutos (limite {ATRASO_MAXIMO_API_MIN} min).'
elif horario_dinamico is None:
    status_painel='SEM SINAL'
    motivo='Não há amostra recente suficiente de intervalos entre Brancas para estimar o próximo horário.'
elif candidatos_integrados.empty:
    status_painel='SEM SINAL'
    motivo='Nenhuma regra validada coincidiu com o contexto atual.'
elif bool(candidatos_integrados['Critérios mínimos atendidos'].any()):
    status_painel='SINAL FORTE'
    motivo=f'Critérios fortes: mínimo de {MIN_VALIDACAO} casos por regra e {MIN_FAMILIAS_INDEPENDENTES} famílias independentes.'
elif bool(candidatos_integrados['Critério moderado atendido'].any()):
    status_painel='SINAL MODERADO'
    motivo=f'Critério moderado: pelo menos 1 família independente, {MIN_VALIDACAO_MODERADO} casos de validação e excesso histórico positivo.'
else:
    status_painel='SEM SINAL'
    motivo='Há indícios, mas nenhum minuto atingiu os critérios mínimos forte ou moderado.'

if not candidatos_integrados.empty:
    fortes=candidatos_integrados[candidatos_integrados['Critérios mínimos atendidos']].copy()
    moderados=candidatos_integrados[candidatos_integrados['Critério moderado atendido']].copy()
    liberados=(fortes if not fortes.empty else moderados).head(5)
else:
    liberados=pd.DataFrame()

# PAINEL ÚNICO — somente o resultado operacional na tela
from datetime import timedelta

sinal=None
origem_horario_txt='Sem horário liberado'
validacao_contexto_txt='Nenhuma família validou o contexto'
contribuicoes_txt='Sem contribuições adicionais identificadas'
if status_painel in ('SINAL FORTE','SINAL MODERADO') and not liberados.empty:
    # V14: prioridade operacional das somas recentes. O ETA residual não é
    # pré-requisito quando o candidato já tem horário explícito e validação.
    op=liberados.copy()
    if '_Prioridade soma recente' in op.columns and op['_Prioridade soma recente'].fillna(0).gt(0).any():
        op=op[op['_Prioridade soma recente'].fillna(0).gt(0)].copy()
        # Ordena por recência da Branca de origem, depois força de validação.
        op=op.sort_values(['_Prioridade soma recente','Critérios mínimos atendidos','Famílias independentes elegíveis','Melhor excesso (p.p.)'],ascending=[False,False,False,False])
    else:
        op=op.sort_values(['Melhor excesso (p.p.)','Famílias independentes elegíveis'],ascending=[False,False])
    if not op.empty:
        sinal=op.iloc[0]

if sinal is not None and dados_atualizados:
    # V8: o minuto da cobertura vem do HORÁRIO efetivamente exibido, nunca da
    # coluna 'Minuto' da regra que validou o contexto. Isso corrige o desvio
    # observado (ex.: alvo 16:31 consultando minuto 33 ou 00).
    horario_sinal=sinal.get('_Horario alvo',pd.NaT)
    if pd.isna(horario_sinal):
        horario_sinal=horario_dinamico
    horario_sinal=pd.Timestamp(horario_sinal)
    if horario_sinal.tzinfo is None:
        horario_sinal=horario_sinal.tz_localize(FUSO)
    else:
        horario_sinal=horario_sinal.tz_convert(FUSO)
    mm=int(horario_sinal.minute)

    # Índice de ordem dentro de cada minuto: 1ª e 2ª rodada.
    df['_rodada_no_minuto']=df.groupby('_minuto_exato').cumcount()+1
    # Amostra histórica e recente usam exatamente o mesmo minuto do relógio.
    amostra=df[df['minuto'].eq(mm)].copy()
    cont=amostra.cor.value_counts()
    n_hist=int(len(amostra))
    n_amarela=int(cont.get('Amarela',0)); n_preta=int(cont.get('Preta',0)); n_branca=int(cont.get('Branca',0))
    hist_nao_branca=n_amarela+n_preta
    hist_pct_a=100*n_amarela/hist_nao_branca if hist_nao_branca else float('nan')
    hist_pct_p=100*n_preta/hist_nao_branca if hist_nao_branca else float('nan')

    # Wilson inferior (95%) para evitar chamar pequena maioria de proteção confiável.
    def wilson_inferior(sucessos,total,z=1.96):
        if total<=0: return 0.0
        p=sucessos/total; den=1+z*z/total
        centro=p+z*z/(2*total)
        margem=z*math.sqrt((p*(1-p)+z*z/(4*total))/total)
        return 100*(centro-margem)/den

    # Janela recente maior que 500 para permitir amostra útil por minuto do relógio.
    # Mantém a mesma janela e o mesmo alvo em todas as estatísticas da cobertura.
    REC_COBERTURA=1500
    recente=df.tail(REC_COBERTURA).copy()
    recente=recente[recente['minuto'].eq(mm)]
    rec_cont=recente.cor.value_counts()
    rec_a=int(rec_cont.get('Amarela',0)); rec_p=int(rec_cont.get('Preta',0))
    rec_nb=rec_a+rec_p
    rec_pct_a=100*rec_a/rec_nb if rec_nb else float('nan')
    rec_pct_p=100*rec_p/rec_nb if rec_nb else float('nan')

    # Só recomenda uma cor se a mesma vencer no histórico e na janela recente,
    # ambas com amostra mínima e limite inferior de Wilson acima de 50%.
    MIN_N_HIST=40
    MIN_N_REC=20
    protecao='Indefinida'
    motivo_protecao=[]
    if hist_nao_branca<MIN_N_HIST:
        motivo_protecao.append(f'histórico com {hist_nao_branca} não-brancas (mínimo {MIN_N_HIST})')
    if rec_nb<MIN_N_REC:
        motivo_protecao.append(f'janela recente com {rec_nb} não-brancas (mínimo {MIN_N_REC})')
    if hist_nao_branca>=MIN_N_HIST and rec_nb>=MIN_N_REC:
        hist_vencedora='Amarela' if n_amarela>n_preta else ('Preta' if n_preta>n_amarela else 'Empate')
        rec_vencedora='Amarela' if rec_a>rec_p else ('Preta' if rec_p>rec_a else 'Empate')
        if hist_vencedora in ('Amarela','Preta') and hist_vencedora==rec_vencedora:
            q_hist=n_amarela if hist_vencedora=='Amarela' else n_preta
            q_rec=rec_a if hist_vencedora=='Amarela' else rec_p
            li_hist=wilson_inferior(q_hist,hist_nao_branca)
            li_rec=wilson_inferior(q_rec,rec_nb)
            if li_hist>50 and li_rec>50:
                protecao=hist_vencedora
            else:
                motivo_protecao.append(f'limite inferior 95% não supera 50% (histórico {li_hist:.1f}%; recente {li_rec:.1f}%)')
        else:
            motivo_protecao.append('histórico e janela recente não concordam na cor dominante')

    if protecao in ('Amarela','Preta'):
        qtd_protecao=n_amarela if protecao=='Amarela' else n_preta
        pct_protecao=100*qtd_protecao/hist_nao_branca if hist_nao_branca else 0
        texto_protecao=f'{protecao} — {qtd_protecao} de {hist_nao_branca} não-brancas ({pct_protecao:.1f}%), estável nas duas janelas'
    else:
        texto_protecao='INDEFINIDA — ' + ('; '.join(motivo_protecao) if motivo_protecao else 'evidência insuficiente')

    # V12 — sempre identifica a melhor candidata observada, mas só a chama
    # de proteção recomendada quando os critérios conservadores originais passam.
    # A escolha da candidata é descritiva; não representa previsão garantida.
    candidatos_cor = []
    for nome, qh, qr in (('Amarela', n_amarela, rec_a), ('Preta', n_preta, rec_p)):
        ph = 100*qh/hist_nao_branca if hist_nao_branca else 0.0
        pr = 100*qr/rec_nb if rec_nb else 0.0
        li_h_c = wilson_inferior(qh, hist_nao_branca) if hist_nao_branca else 0.0
        li_r_c = wilson_inferior(qr, rec_nb) if rec_nb else 0.0
        # Nota conservadora: pior percentual entre histórico e recente.
        nota = min(ph, pr) if hist_nao_branca and rec_nb else 0.0
        candidatos_cor.append((nota, nome, ph, pr, li_h_c, li_r_c))
    candidatos_cor.sort(reverse=True)
    _, melhor_cor_observada, melhor_hist_pct, melhor_rec_pct, melhor_li_hist, melhor_li_rec = candidatos_cor[0]
    cobertura_aprovada = protecao in ('Amarela','Preta')
    if cobertura_aprovada:
        texto_candidata_v12 = (
            f'{protecao} — PROTEÇÃO APROVADA. Participação entre não-brancas: '
            f'histórico {melhor_hist_pct:.1f}%; recente {melhor_rec_pct:.1f}%.'
        )
    else:
        texto_candidata_v12 = (
            f'{melhor_cor_observada} é a melhor candidata observada, mas NÃO está aprovada como proteção. '
            f'Participação entre não-brancas: histórico {melhor_hist_pct:.1f}%; recente {melhor_rec_pct:.1f}%. '
            f'Limites inferiores 95%: histórico {melhor_li_hist:.1f}%; recente {melhor_li_rec:.1f}%.'
        )

    # Auditoria das duas rodadas dentro do minuto, preservando ordem cronológica.
    slots=[]
    for slot in (1,2):
        sh=amostra[amostra['_rodada_no_minuto'].eq(slot)].cor.value_counts()
        slots.append(f"rodada {slot}: Amarela {int(sh.get('Amarela',0))}, Preta {int(sh.get('Preta',0))}")
    texto_cobertura_diagnostico=(
        f'Alvo consultado: minuto {mm:02d} (horário {horario_sinal.strftime("%H:%M")}). '
        f'Histórico: Amarela {n_amarela}, Preta {n_preta}; {hist_nao_branca} não-brancas. '
        f'Últimas {REC_COBERTURA} rodadas no mesmo minuto: Amarela {rec_a}, Preta {rec_p}; {rec_nb} não-brancas. '
        + ' | '.join(slots) + '. '
        + (f'Proteção selecionada: {protecao}.' if protecao in ('Amarela','Preta') else 'Proteção não recomendada: '+ '; '.join(motivo_protecao)+'.')
    )

    # V10: estatística completa das duas proteções, mesmo quando nenhuma
    # atinge o critério para recomendação. Não escolhe uma cor por força.
    total_hist_min=len(amostra)
    brancas_hist_min=int((amostra.cor=='Branca').sum())
    total_rec_min=len(recente)
    brancas_rec_min=int((recente.cor=='Branca').sum())

    def _linha_cor_v10(nome, base, total, brancas, rec_base, rec_total, rec_brancas):
        q=int((base.cor==nome).sum()) if total else 0
        qr=int((rec_base.cor==nome).sum()) if rec_total else 0
        pct_nb=100*q/(total-brancas) if total>brancas else 0.0
        pct_total=100*q/total if total else 0.0
        pct_rec_nb=100*qr/(rec_total-rec_brancas) if rec_total>rec_brancas else 0.0
        pct_rec_total=100*qr/rec_total if rec_total else 0.0
        conjunta=100*(q+brancas)/total if total else 0.0
        conjunta_rec=100*(qr+rec_brancas)/rec_total if rec_total else 0.0
        li_h=wilson_inferior(q,total-brancas) if total>brancas else 0.0
        li_r=wilson_inferior(qr,rec_total-rec_brancas) if rec_total>rec_brancas else 0.0
        return (
            f'<b>{nome}</b>: histórico {q}/{total} ({pct_total:.1f}% de todos; '
            f'{pct_nb:.1f}% das não-brancas; limite inferior 95% {li_h:.1f}%). '
            f'Recente {qr}/{rec_total} ({pct_rec_total:.1f}% de todos; '
            f'{pct_rec_nb:.1f}% das não-brancas; limite inferior 95% {li_r:.1f}%). '
            f'Branca + {nome}: histórico {conjunta:.1f}%; recente {conjunta_rec:.1f}%.'
        )

    # Cor com maior frequência conjunta observada; não é recomendação de entrada.
    conj_hist_a=(n_branca+n_amarela)/n_hist if n_hist else 0.0
    conj_hist_p=(n_branca+n_preta)/n_hist if n_hist else 0.0
    conj_rec_a=(int((recente.cor=='Branca').sum())+rec_a)/len(recente) if len(recente) else 0.0
    conj_rec_p=(int((recente.cor=='Branca').sum())+rec_p)/len(recente) if len(recente) else 0.0
    if conj_hist_a>conj_hist_p and conj_rec_a>conj_rec_p:
        tendencia_cobertura='AMARELA tem maior cobertura conjunta observada nas duas janelas.'
    elif conj_hist_p>conj_hist_a and conj_rec_p>conj_rec_a:
        tendencia_cobertura='PRETA tem maior cobertura conjunta observada nas duas janelas.'
    else:
        tendencia_cobertura='SEM COR DOMINANTE CONSISTENTE: histórico e janela recente divergem ou empatam.'
    texto_cobertura_resumo_v11=(
        f'<b>COBERTURA OBSERVADA — {tendencia_cobertura}</b><br>'
        f'Amarela: Branca + Amarela = histórico {100*conj_hist_a:.1f}% | recente {100*conj_rec_a:.1f}%. '
        f'Preta: Branca + Preta = histórico {100*conj_hist_p:.1f}% | recente {100*conj_rec_p:.1f}%. '
        f'<br><b>Decisão:</b> {texto_protecao}. A tendência observada não substitui os critérios de confiança.'
    )

    texto_estatistica_cobertura_v10 = (
        f'<b>Estatística completa do minuto {mm:02d} — sem escolher automaticamente uma cor</b><br>'
        f'Histórico: {total_hist_min} rodadas, {brancas_hist_min} Brancas '
        f'({100*brancas_hist_min/total_hist_min if total_hist_min else 0:.1f}%). '
        f'Janela recente: {total_rec_min} rodadas, {brancas_rec_min} Brancas '
        f'({100*brancas_rec_min/total_rec_min if total_rec_min else 0:.1f}%).<br>'
        + _linha_cor_v10('Amarela',amostra,total_hist_min,brancas_hist_min,recente,total_rec_min,brancas_rec_min)
        + '<br>'
        + _linha_cor_v10('Preta',amostra,total_hist_min,brancas_hist_min,recente,total_rec_min,brancas_rec_min)
        + '<br><b>Por rodada:</b> '
        + ' | '.join(
            f'Rodada {slot}: ' +
            ', '.join(
                f'{cor} {int((amostra[amostra["_rodada_no_minuto"].eq(slot)].cor==cor).sum())}'
                for cor in ('Branca','Amarela','Preta')
            )
            for slot in (1,2)
        )
        + '<br>As porcentagens conjuntas representam Branca OU a cor indicada no minuto-alvo. '
          'São estatísticas descritivas; proteção indefinida significa que os critérios de recomendação não foram atingidos.'
    )

    hora=horario_sinal.strftime('%d/%m/%Y %H:%M')
    familias=str(sinal['Famílias independentes usadas'])
    # V5: separar a origem que calcula o horário das famílias que validam o contexto.
    if pd.notna(sinal.get('_Prioridade soma recente',0)) and float(sinal.get('_Prioridade soma recente',0) or 0)>0:
        origem_horario_txt=str(sinal.get('_Origem soma recente','Soma direta originada em Branca recente.'))
        validacao_contexto_txt=f'Confluências históricas independentes: {familias}.'
    elif pd.notna(sinal.get('_Horario alvo',pd.NaT)) and pd.notna(sinal.get('_Etapa cadeia',np.nan)):
        origem_horario_txt=f'Soma encadeada — etapa {int(sinal["_Etapa cadeia"])}; alvo calculado pela cadeia.'
        validacao_contexto_txt='A própria cadeia atingiu os critérios históricos de entrada.'
    else:
        origem_horario_txt=f'Mediana residual dos intervalos entre Brancas — {amostra_eta} intervalos considerados; método: {metodo_eta}.'
        validacao_contexto_txt=f'Família(s) que validaram o contexto: {familias}.'
    # Contribuições auxiliares: regras que coincidem com o contexto, mas não foram selecionadas como independentes.
    auxiliares=[]
    if not candidatos_integrados.empty:
        for _,cand in candidatos_integrados.iterrows():
            nome_cand=str(cand.get('Famílias',''))
            if nome_cand and nome_cand not in familias and int(cand.get('Minuto',-999))==int(sinal['Minuto']):
                auxiliares.append(nome_cand)
    contribuicoes_txt='; '.join(dict.fromkeys(auxiliares)) if auxiliares else 'Nenhuma outra família independente elegível coincidiu com o minuto selecionado.'
    excesso=float(sinal['Melhor excesso (p.p.)'])
    detalhe=f"{int(sinal['Famílias independentes elegíveis'])} família(s) independente(s) | melhor excesso histórico +{excesso:.2f} p.p. | validação: {int(sinal['Casos validação somados (não independentes)'])} casos (soma não independente)"
    status_html='SINAL FORTE' if bool(sinal['Critérios mínimos atendidos']) else 'SINAL MODERADO'
    cor_status='#15803d' if bool(sinal['Critérios mínimos atendidos']) else '#b45309'
    entrada_html='BRANCA'
    # V12: um sinal só fica operacional se houver proteção de cor aprovada.
    if not cobertura_aprovada:
        status_html='CANDIDATO BLOQUEADO — SEM PROTEÇÃO VALIDADA'
        cor_status='#b45309'
        entrada_html='NÃO ENTRAR'
    if float(sinal.get('_Prioridade soma recente',0) or 0)>0:
        explicacao=(f'Candidato prioritário originado em Branca recente: {sinal.get("_Origem soma recente")}. '
                    f'Liberado porque coincidiu com {int(sinal["Famílias independentes elegíveis"])} família(s) de validação.')
    elif pd.notna(sinal.get('_Etapa cadeia',np.nan)):
        explicacao=(f'Sinal acrescentado pela Soma Encadeada — etapa {int(sinal["_Etapa cadeia"])}. '
                    f'Taxa histórica da etapa: {float(sinal["_Taxa cadeia (%)"]):.1f}% '
                    f'contra base de {float(sinal["_Base cadeia (%)"]):.1f}%, '
                    f'em {int(sinal["Casos validação somados (não independentes)"])} caminhos.')
    else:
        explicacao=f'Horário estimado pela mediana residual dos intervalos recentes entre Brancas ({amostra_eta} intervalos considerados); padrão atual validado por: {metodo_eta}.'
else:
    status_html='SEM SINAL'
    cor_status='#b45309'
    hora='—'; entrada_html='NÃO ENTRAR'; texto_protecao='—'; detalhe=''
    texto_cobertura_diagnostico='Sem sinal: cobertura não calculada.'
    texto_cobertura_resumo_v11='Sem cobertura calculada.'
    texto_estatistica_cobertura_v10='Sem estatística de cobertura: não há sinal.'
    texto_protecao='Proteção não calculada.'
    familias=''
    origem_horario_txt='Nenhum horário liberado.'
    validacao_contexto_txt='Nenhuma família validou o contexto.'
    contribuicoes_txt='Sem contribuições adicionais.'
    explicacao=motivo if 'motivo' in globals() else 'Não há evidência suficiente para liberar uma entrada.'
    n_hist=0; n_branca=0; n_amarela=0; n_preta=0
    cobertura_aprovada=False
    texto_candidata_v12='Sem candidato: não há sinal para avaliar a cobertura.'

# MEMÓRIA V6 — AVALIAÇÃO RESPEITANDO AS DUAS RODADAS DE CADA MINUTO.
# A ordem é cronológica dentro do minuto: rodada 1 e rodada 2 (~30 segundos).
# Branca: separa 1ª/2ª rodada do minuto-alvo e dos minutos +1/+2.
# Cobertura: separa 1ª/2ª rodada e também registra se acertou em pelo menos uma.
memoria_v4_path=os.path.join(BASE,'memoria_sinais_desempenho_v4.json')
try:
    with open(memoria_v4_path,encoding='utf-8') as f:
        memoria_v4=json.load(f)
    if not isinstance(memoria_v4,dict): memoria_v4={}
except Exception:
    memoria_v4={}
memoria_v4.setdefault('pendentes',[])
memoria_v4.setdefault('avaliados',[])
memoria_v4.setdefault('chaves_registradas',[])
memoria_v4['versao_avaliacao']='V6_duas_rodadas_por_minuto'
memoria_v4['regra_rodadas']='Duas rodadas cronológicas por minuto; cada uma aproximadamente 30 segundos.'

# Caso manual já relatado: às 11:55 saíram 05 e 03 (ambas Amarelas);
# Branca ocorreu às 11:56. Cobertura Preta falhou nas duas rodadas do minuto-alvo.
# Como o usuário não informou a ordem exata entre 05 e 03, não atribuímos pedra a um slot específico.
caso_manual_id='EXEMPLO_MANUAL_2026-11-09_11-55'
if not any(x.get('id')==caso_manual_id for x in memoria_v4['avaliados']):
    memoria_v4['avaliados'].append({
        'id':caso_manual_id,'alvo':'2026-11-09T11:55:00-03:00',
        'origem':'relato_manual_usuario','status_sinal':'SINAL MODERADO',
        'cobertura_prevista':'Preta','familias':'não informado',
        'resultado_branca':'ATRASO_1_MIN','atraso_minutos':1,
        'resultado_branca_minuto_alvo':'SEM_BRANCA_NAS_DUAS_RODADAS',
        'pedras_no_alvo':[5,3],'cores_no_alvo':['Amarela','Amarela'],
        'resultado_cobertura':'ERRO_NAS_DUAS_RODADAS',
        'cobertura_rodada_1':'NAO_CONFIRMADA_ORDEM_NAO_INFORMADA',
        'cobertura_rodada_2':'NAO_CONFIRMADA_ORDEM_NAO_INFORMADA',
        'observacao':'Relato manual: duas pedras Amarelas no minuto 11:55; Branca no minuto 11:56. Ordem das pedras dentro do minuto não informada.'
    })


# Atualiza o exemplo manual se ele já existia na memória V5, sem duplicá-lo.
for item in memoria_v4['avaliados']:
    if item.get('id')==caso_manual_id:
        item.update({
            'resultado_branca_minuto_alvo':'SEM_BRANCA_NAS_DUAS_RODADAS',
            'cobertura_rodada_1':'NAO_CONFIRMADA_ORDEM_NAO_INFORMADA',
            'cobertura_rodada_2':'NAO_CONFIRMADA_ORDEM_NAO_INFORMADA',
            'resultado_cobertura':'ERRO_NAS_DUAS_RODADAS',
            'observacao':'Relato manual: duas pedras Amarelas no minuto 11:55; Branca no minuto 11:56. Ordem das pedras dentro do minuto não informada.'
        })

# Usa a lista cronológica já ordenada no DataFrame para preservar a ordem dos dois resultados.
def duas_rodadas(minuto):
    vals=rodadas_por_minuto.get(minuto,[])
    return [int(x) for x in vals[:2]]

pendentes_novos=[]
for reg in memoria_v4['pendentes']:
    try:
        alvo=pd.Timestamp(reg['alvo']).floor('min')
        if alvo.tzinfo is None: alvo=alvo.tz_localize(FUSO)
        else: alvo=alvo.tz_convert(FUSO)
        # Só avalia quando os três minutos (alvo, +1, +2) tiverem duas rodadas cada.
        slots={k:duas_rodadas(alvo+pd.Timedelta(minutes=k)) for k in (0,1,2)}
        if any(len(v)<2 for v in slots.values()):
            pendentes_novos.append(reg); continue

        # Branca: prioridade cronológica, distinguindo a rodada dentro de cada minuto.
        branca_slot=None
        for k in (0,1,2):
            for pos,pedra in enumerate(slots[k],start=1):
                if pedra==0:
                    branca_slot=(k,pos); break
            if branca_slot is not None: break
        if branca_slot is None:
            status_branca='SEM_BRANCA_ATE_2_MIN'; atraso=None; status_branca_slot='SEM_BRANCA_NA_JANELA'
        else:
            atraso,pos=branca_slot
            if atraso==0:
                status_branca='EXATO'
                status_branca_slot='EXATO_RODADA_1' if pos==1 else 'EXATO_RODADA_2'
            elif atraso==1:
                status_branca='ATRASO_1_MIN'
                status_branca_slot='ATRASO_1_MIN_RODADA_1' if pos==1 else 'ATRASO_1_MIN_RODADA_2'
            else:
                status_branca='ATRASO_2_MIN'
                status_branca_slot='ATRASO_2_MIN_RODADA_1' if pos==1 else 'ATRASO_2_MIN_RODADA_2'

        # Cobertura é avaliada somente nas duas rodadas do minuto originalmente indicado.
        pedras_alvo=[p for p in slots[0] if 1<=p<=14]
        cores_alvo=[cor_pedra(p) for p in pedras_alvo]
        cobertura_prevista=reg.get('cobertura_prevista')
        if len(pedras_alvo)<2:
            cov_r1=cov_r2='SEM_DADO'
            status_cobertura='SEM_DADO_NO_ALVO'
        else:
            cov_r1='ACERTO' if cor_pedra(pedras_alvo[0])==cobertura_prevista else 'ERRO'
            cov_r2='ACERTO' if cor_pedra(pedras_alvo[1])==cobertura_prevista else 'ERRO'
            if cov_r1=='ACERTO' and cov_r2=='ACERTO': status_cobertura='ACERTO_NAS_DUAS_RODADAS'
            elif cov_r1=='ACERTO' or cov_r2=='ACERTO': status_cobertura='ACERTO_EM_UMA_RODADA'
            else: status_cobertura='ERRO_NAS_DUAS_RODADAS'
        memoria_v4['avaliados'].append({
            **reg,'avaliado_em':agora_cadeia.isoformat(),
            'resultado_branca':status_branca,'resultado_branca_detalhe':status_branca_slot,
            'atraso_minutos':atraso,
            'rodadas_minuto_alvo':slots[0],
            'pedras_no_alvo':pedras_alvo,'cores_no_alvo':cores_alvo,
            'cobertura_rodada_1':cov_r1,'cobertura_rodada_2':cov_r2,
            'resultado_cobertura':status_cobertura
        })
    except Exception:
        pendentes_novos.append(reg)
memoria_v4['pendentes']=pendentes_novos

# Registra cada novo sinal uma única vez.
if sinal is not None and dados_atualizados:
    dt_reg=horario_sinal if pd.notna(horario_sinal) else horario_dinamico
    if dt_reg is not None and not pd.isna(dt_reg):
        dt_reg=pd.Timestamp(dt_reg)
        if dt_reg.tzinfo is None: dt_reg=dt_reg.tz_localize(FUSO)
        else: dt_reg=dt_reg.tz_convert(FUSO)
        chave=dt_reg.floor('min').isoformat()+'|'+str(protecao)+'|'+str(familias)
        if chave not in memoria_v4['chaves_registradas']:
            memoria_v4['pendentes'].append({
                'alvo':dt_reg.floor('min').isoformat(),
                'registrado_em':agora_cadeia.isoformat(),
                'status_sinal':status_html,
                'cobertura_prevista':protecao,
                'familias':familias,
                'origem_horario':origem_horario_txt,
                'validacao_contexto':validacao_contexto_txt,
                'cobertura_historica':texto_protecao,
                'minuto_alvo':int(dt_reg.minute)
            })
            memoria_v4['chaves_registradas'].append(chave)


# ================================================================
# V9 — CONTINUIDADE APÓS FALHA DO ALVO PRINCIPAL (MODO OBSERVAÇÃO)
# Se não houver Branca no minuto-alvo, cada pedra 1..14 desse minuto
# abre um ramo: novo_alvo = minuto_alvo + valor_da_pedra.
# Cada ramo é avaliado separadamente; se falhar, as pedras do novo alvo
# podem abrir a etapa seguinte. Não altera a liberação do sinal principal.
# ================================================================
mem_v9_path=os.path.join(BASE,'memoria_continuacao_sinais_v9.json')
try:
    with open(mem_v9_path,encoding='utf-8') as f:
        mem_v9=json.load(f)
    if not isinstance(mem_v9,dict): mem_v9={}
except Exception:
    mem_v9={}
mem_v9.setdefault('fontes_processadas',[])
mem_v9.setdefault('ramos',[])
mem_v9.setdefault('observacoes_manuais',[])

def _minuto_iso_v9(valor):
    t=pd.Timestamp(valor)
    if t.tzinfo is None: t=t.tz_localize(FUSO)
    else: t=t.tz_convert(FUSO)
    return t.floor('min')

def _add_ramo_v9(fonte_id, etapa, origem_minuto, pedra, observacao=''):
    origem=_minuto_iso_v9(origem_minuto)
    pedra=int(pedra)
    if not 1<=pedra<=14: return
    alvo=origem+pd.Timedelta(minutes=pedra)
    rid=f'{fonte_id}|E{etapa}|{origem.isoformat()}|P{pedra:02d}|{alvo.isoformat()}'
    if any(x.get('id')==rid for x in mem_v9['ramos']): return
    mem_v9['ramos'].append({
        'id':rid,'fonte_id':fonte_id,'etapa':int(etapa),
        'minuto_origem':origem.isoformat(),'pedra_origem':pedra,
        'minuto_alvo':alvo.isoformat(),'status':'AGUARDANDO',
        'observacao':observacao
    })

# Caso observado e informado pelo usuário: sinal principal 16:31,
# sem Branca no alvo; pedras 03 e 08 abriram dois ramos independentes.
# A imagem/relato confirma Branca às 16:34, portanto o ramo da pedra 03
# é registrado como observação manual, sem inferir a ordem das duas pedras.
caso_v9='OBS_USUARIO_2026-11-09_16-31'
if caso_v9 not in mem_v9['fontes_processadas']:
    mem_v9['observacoes_manuais'].append({
        'id':caso_v9,'alvo':'2026-11-09T16:31:00-03:00',
        'pedras':[3,8],'branca_no_alvo':False,
        'branca_observada_em':'2026-11-09T16:34:00-03:00',
        'nota':'Relato do usuário: pedra 03 gerou alvo 16:34, onde ocorreu Branca. Pedra 08 também gera ramo 16:39. Ordem das pedras no minuto não inferida.'
    })
    for p in [3,8]:
        _add_ramo_v9(caso_v9,2,'2026-11-09T16:31:00-03:00',p,'observação manual relatada pelo usuário')
    mem_v9['fontes_processadas'].append(caso_v9)

# Converte sinais principais já avaliados em ramos secundários.
# Só abre caminhos quando não houve Branca nas duas rodadas do minuto-alvo.
for reg in memoria_v4.get('avaliados',[]):
    if reg.get('resultado_branca_minuto_alvo')!='SEM_BRANCA_NAS_DUAS_RODADAS':
        continue
    alvo_txt=reg.get('alvo')
    pedras=reg.get('pedras_no_alvo',[])
    if not alvo_txt or not isinstance(pedras,list): continue
    fonte_id='SINAL_'+str(reg.get('id') or (str(alvo_txt)+'|'+str(reg.get('cobertura_prevista'))))
    if fonte_id in mem_v9['fontes_processadas']: continue
    for p in pedras:
        try: _add_ramo_v9(fonte_id,2,alvo_txt,int(p),'pedra observada nas duas rodadas do alvo principal')
        except Exception: pass
    mem_v9['fontes_processadas'].append(fonte_id)

# Avalia ramos já vencidos; exige as duas rodadas completas do minuto.
# Se não houver Branca, abre novos ramos até a etapa 3.
for ramo in list(mem_v9['ramos']):
    if ramo.get('status')!='AGUARDANDO': continue
    try:
        alvo=_minuto_iso_v9(ramo['minuto_alvo'])
        vals=duas_rodadas(alvo)
        if len(vals)<2: continue
        if 0 in vals:
            ramo['status']='BRANCA_NO_ALVO'
            ramo['pedras_no_alvo']=vals
            ramo['rodada_branca']=int(vals.index(0)+1)
            ramo['avaliado_em']=agora_cadeia.isoformat()
            continue
        ramo['status']='SEM_BRANCA_NO_ALVO'
        ramo['pedras_no_alvo']=vals
        ramo['avaliado_em']=agora_cadeia.isoformat()
        if int(ramo.get('etapa',2))<3:
            for p in vals:
                if 1<=int(p)<=14:
                    _add_ramo_v9(ramo['fonte_id'],int(ramo['etapa'])+1,alvo,int(p),
                                 'continuação após falha do alvo da etapa anterior')
    except Exception as e:
        ramo['erro_avaliacao']=str(e)

# Resumo de desempenho dos ramos, mantendo os caminhos separados.
ramos_df_v9=pd.DataFrame(mem_v9['ramos'])
if not ramos_df_v9.empty:
    ramos_concluidos_v9=ramos_df_v9[ramos_df_v9.status.isin(['BRANCA_NO_ALVO','SEM_BRANCA_NO_ALVO'])]
    ramos_acertos_v9=int((ramos_concluidos_v9.status=='BRANCA_NO_ALVO').sum())
    ramos_falhas_v9=int((ramos_concluidos_v9.status=='SEM_BRANCA_NO_ALVO').sum())
    ramos_pendentes_v9=int((ramos_df_v9.status=='AGUARDANDO').sum())
    por_etapa_v9=[]
    for etapa_v9 in (2,3):
        sub_v9=ramos_concluidos_v9[ramos_concluidos_v9.etapa==etapa_v9]
        por_etapa_v9.append(f'Etapa {etapa_v9}: {int((sub_v9.status=="BRANCA_NO_ALVO").sum())}/{len(sub_v9)} Brancas')
else:
    ramos_acertos_v9=ramos_falhas_v9=ramos_pendentes_v9=0
    por_etapa_v9=['Etapa 2: 0/0 Brancas','Etapa 3: 0/0 Brancas']

# Destaque dos ramos ativos e dos resultados observados mais recentes.
ativos_v9=[x for x in mem_v9['ramos'] if x.get('status')=='AGUARDANDO']
recentes_v9=sorted(mem_v9['ramos'],key=lambda x:x.get('minuto_alvo',''),reverse=True)
linhas_v9=[]
for x in (ativos_v9[:8] if ativos_v9 else recentes_v9[:5]):
    t=pd.Timestamp(x['minuto_alvo']).strftime('%H:%M')
    linhas_v9.append(f'Etapa {x["etapa"]}: {t} — pedra {int(x["pedra_origem"]):02d} — {x["status"]}')
texto_continuacao_v9=(
    ('<br>'.join(linhas_v9) if linhas_v9 else 'Nenhum ramo secundário registrado.')
    + '<br>' + ' • '.join(por_etapa_v9)
    + f'<br>Brancas nos alvos secundários: {ramos_acertos_v9} • Falhas: {ramos_falhas_v9} • Aguardando: {ramos_pendentes_v9}'
    + '<br>Modo observação: os ramos não alteram nem substituem o sinal principal.'
)
mem_v9['atualizado_em']=agora_cadeia.isoformat()
mem_v9['resumo']={'ramos_acertos':ramos_acertos_v9,'ramos_falhas':ramos_falhas_v9,
                  'ramos_pendentes':ramos_pendentes_v9,'total_ramos':len(mem_v9['ramos'])}
try:
    with open(mem_v9_path,'w',encoding='utf-8') as f:
        json.dump(mem_v9,f,ensure_ascii=False,indent=2)
except Exception as e:
    print('Aviso: memória V9 de continuidade não salva:',e)


# Placares separados e detalhados por rodada.
avaliados_df=pd.DataFrame(memoria_v4['avaliados'])
if not avaliados_df.empty:
    branca_counts=avaliados_df['resultado_branca'].value_counts().to_dict()
    detalhe_branca_counts=avaliados_df.get('resultado_branca_detalhe',pd.Series(dtype=str)).value_counts().to_dict()
    cobertura_counts=avaliados_df['resultado_cobertura'].value_counts().to_dict()
    cov_r1_counts=avaliados_df.get('cobertura_rodada_1',pd.Series(dtype=str)).value_counts().to_dict()
    cov_r2_counts=avaliados_df.get('cobertura_rodada_2',pd.Series(dtype=str)).value_counts().to_dict()
else:
    branca_counts={}; detalhe_branca_counts={}; cobertura_counts={}; cov_r1_counts={}; cov_r2_counts={}
q_exato=int(branca_counts.get('EXATO',0))
q_exato_r1=int(detalhe_branca_counts.get('EXATO_RODADA_1',0))
q_exato_r2=int(detalhe_branca_counts.get('EXATO_RODADA_2',0))
q_atraso1=int(branca_counts.get('ATRASO_1_MIN',0))
q_atraso2=int(branca_counts.get('ATRASO_2_MIN',0))
q_sem=int(branca_counts.get('SEM_BRANCA_ATE_2_MIN',0))
q_cov_r1=int(cov_r1_counts.get('ACERTO',0))
q_cov_r2=int(cov_r2_counts.get('ACERTO',0))
q_cov_uma=int(cobertura_counts.get('ACERTO_EM_UMA_RODADA',0))
q_cov_duas=int(cobertura_counts.get('ACERTO_NAS_DUAS_RODADAS',0))
q_cov_erro=int(cobertura_counts.get('ERRO_NAS_DUAS_RODADAS',0))
q_cov_sem=int(cobertura_counts.get('SEM_DADO_NO_ALVO',0))
q_cov_legado=sum(1 for x in memoria_v4['avaliados'] if x.get('resultado_cobertura') in ('ACERTO','ERRO'))
try:
    memoria_v4['atualizado_em']=agora_cadeia.isoformat()
    memoria_v4['resumo']={
        'branca_exata':q_exato,'branca_exata_rodada_1':q_exato_r1,'branca_exata_rodada_2':q_exato_r2,
        'atraso_1_min':q_atraso1,'atraso_2_min':q_atraso2,'sem_branca_ate_2_min':q_sem,
        'cobertura_acerto_rodada_1':q_cov_r1,'cobertura_acerto_rodada_2':q_cov_r2,
        'cobertura_acerto_em_uma_rodada':q_cov_uma,'cobertura_acerto_nas_duas_rodadas':q_cov_duas,
        'cobertura_erros_nas_duas_rodadas':q_cov_erro,'cobertura_sem_dado':q_cov_sem
    }
    with open(memoria_v4_path,'w',encoding='utf-8') as f:
        json.dump(memoria_v4,f,ensure_ascii=False,indent=2)
except Exception as e:
    print('Aviso: memória de avaliação V6 não salva:',e)

texto_memoria_v4=(
    f"Branca no minuto-alvo: {q_exato} • 1ª rodada {q_exato_r1} • 2ª rodada {q_exato_r2}.<br>"
    f"Branca com atraso: +1 min {q_atraso1} • +2 min {q_atraso2} • sem Branca até +2 min {q_sem}.<br>"
    f"Cobertura: 1ª rodada {q_cov_r1} acertos • 2ª rodada {q_cov_r2} acertos • pelo menos uma rodada {q_cov_uma} • ambas {q_cov_duas} • erro nas duas {q_cov_erro} • sem dado {q_cov_sem}.<br>"
    f"Registros antigos sem divisão por rodada: {q_cov_legado}. • Sinais aguardando avaliação: {len(memoria_v4['pendentes'])}."
)

ultima_linha=df.iloc[-1]
ultima_txt=ultima_linha.data_hora.strftime('%d/%m/%Y %H:%M:%S')
api_txt='ATUALIZADA' if dados_atualizados else 'INDISPONÍVEL / DESATUALIZADA'
texto_cadeia=texto_cadeia_ativa()
if monitor_somas_recentes:
    linhas_monitor=[]
    for z in sorted(monitor_somas_recentes,key=lambda x:(x['branca_origem'],x['prioridade']),reverse=True)[:12]:
        linhas_monitor.append(f"Branca {z['branca_origem'].strftime('%H:%M')} + pedra {z['lado']} {z['pedra']} → {z['alvo'].strftime('%H:%M')}")
    texto_monitor_somas_v11='<br>'.join(linhas_monitor)
else:
    texto_monitor_somas_v11='Ainda não há pedras adjacentes válidas para calcular somas diretas.'
monitor_somas_html=(f'<div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#fbbf24">V14 — SOMAS DOS BRANCOS MAIS RECENTES (PRIORIDADE 1)</div><div style="margin-top:7px;font-size:12px;color:#e5e7eb;line-height:1.7">{texto_monitor_somas_v11}</div><div style="margin-top:6px;font-size:11px;color:#9ca3af">Candidatos futuros só assumem prioridade operacional quando passam pela validação independente e pela proteção de cor. Se nenhum passar, o painel mantém o melhor candidato no monitor e informa o bloqueio; não transforma soma não validada em sinal.</div></div>')
cadeia_html=(f'<div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px">'
             f'<div style="font-size:12px;color:#9ca3af">SOMA ENCADEADA — ACOMPANHAMENTO (PEDRA POSTERIOR À BRANCA)</div>'
             f'<div style="margin-top:6px">{texto_cadeia}</div>'
             f'<div style="margin-top:7px;font-size:12px;color:#cbd5e1">{regime_txt}</div>'
             f'<div style="margin-top:7px;font-size:12px;color:#cbd5e1">'
             f'Etapa 1: {cadeia_stats["acertos_1"]}/{cadeia_stats["alvos_1_observados"]} ({pct1:.1f}%) • '
             f'Etapa 2: {cadeia_stats["acertos_2"]}/{cadeia_stats["caminhos_2"]} caminhos ({pct2:.1f}%) • '
             f'Etapa 3: {cadeia_stats["acertos_3"]}/{cadeia_stats["caminhos_3"]} caminhos ({pct3:.1f}%) • Base: {taxa_base_cadeia:.1f}%'
             f'</div></div>')

html=f"""<div style="max-width:760px;margin:12px auto;padding:24px;border-radius:18px;background:#111827;color:#f9fafb;font-family:Arial,sans-serif;box-shadow:0 8px 28px #0002">
<div style="font-size:13px;letter-spacing:2px;color:#9ca3af;text-align:center">TIPMINER • BLITZ4 DOUBLE — V14</div>
<div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#fbbf24">V14 — ANÁLISE DISPARADA POR CADA BRANCA (SEM DUPLICAR EVENTOS)</div><div style="margin-top:7px;font-size:12px;color:#e5e7eb;line-height:1.7">{texto_eventos_v13}</div><div style="margin-top:6px;font-size:11px;color:#9ca3af">Cada Branca é registrada uma vez pelo UUID. A pedra posterior só entra no cálculo depois de aparecer na sequência real. Reexecute após novas Brancas para atualizar os eventos.</div></div>
<div style="text-align:center;margin:16px 0 22px;font-size:30px;font-weight:800;color:{cor_status}">{status_html}</div>
<div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#fbbf24">V8 — DETECTOR DE MUDANÇA DE REGIME (MODO OBSERVAÇÃO)</div><div style="margin-top:7px;font-size:13px;line-height:1.6">{detector_regime_txt}</div><div style="margin-top:6px;font-size:11px;color:#9ca3af">Compara as últimas 100 rodadas com as 500 anteriores, intervalos entre Brancas e tempo desde a última Branca. Não lê diretamente o indicador REC e não altera os sinais nesta versão.</div></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
<div style="background:#1f2937;padding:16px;border-radius:12px"><div style="font-size:12px;color:#9ca3af">HORÁRIO-ALVO</div><div style="font-size:25px;font-weight:700;margin-top:7px">{hora}</div></div>
<div style="background:#1f2937;padding:16px;border-radius:12px"><div style="font-size:12px;color:#9ca3af">ENTRADA</div><div style="font-size:25px;font-weight:700;margin-top:7px">{entrada_html}</div></div>
<div style="background:#1f2937;padding:16px;border-radius:12px;grid-column:1 / -1"><div style="font-size:12px;color:#9ca3af">PROTEÇÃO HISTÓRICA</div><div style="font-size:20px;font-weight:700;margin-top:7px">{texto_protecao}</div></div>
<div style="background:#1f2937;padding:16px;border-radius:12px;grid-column:1 / -1;border:1px solid #64748b"><div style="font-size:12px;color:#fbbf24">V12 — PEDRA DE COBERTURA</div><div style="font-size:17px;font-weight:700;margin-top:7px">{texto_candidata_v12}</div><div style="font-size:12px;margin-top:6px;color:#cbd5e1">O sinal principal é bloqueado quando Amarela ou Preta não passa pelos critérios de proteção. A candidata observada não deve ser tratada como entrada aprovada.</div></div>
</div>
 <div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#9ca3af">V12 — ORIGEM E JUSTIFICATIVA DO SINAL</div><div style="margin-top:7px;font-size:13px;line-height:1.6"><b>Quem calculou o horário:</b> {origem_horario_txt}<br><b>Quem validou o contexto:</b> {validacao_contexto_txt}<br><b>Outras contribuições:</b> {contribuicoes_txt}</div></div>
 <div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#9ca3af">V12 — AUDITORIA DA COBERTURA</div><div style="margin-top:7px;font-size:12px;color:#e5e7eb;line-height:1.6">{texto_cobertura_diagnostico}</div><div style="margin-top:6px;font-size:11px;color:#9ca3af">A frequência histórica e a janela recente são descritivas; nenhuma delas garante a próxima cor.</div></div>
{monitor_somas_html}
{cadeia_html}
 <div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#fbbf24">V12 — ESTATÍSTICA DAS DUAS COBERTURAS</div><div style="margin-top:7px;font-size:12px;color:#e5e7eb;line-height:1.7">{texto_estatistica_cobertura_v10}</div></div>
 <div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#fbbf24">V12 — COR DE COBERTURA OBSERVADA</div><div style="margin-top:7px;font-size:13px;color:#e5e7eb;line-height:1.7">{texto_cobertura_resumo_v11}</div></div>
 <div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#fbbf24">V12 — CONTINUIDADE APÓS FALHA DO ALVO PRINCIPAL</div><div style="margin-top:7px;font-size:12px;color:#e5e7eb;line-height:1.6">{texto_continuacao_v9}</div><div style="margin-top:6px;font-size:11px;color:#9ca3af">Cada pedra do minuto que falhou abre um ramo independente: minuto-alvo + valor da pedra. Se o novo alvo também falhar, as pedras dele podem abrir a etapa 3. As duas rodadas são avaliadas juntas para Branca, preservando o número da rodada em caso de acerto.</div></div>\n <div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#9ca3af">PESQUISA EXPERIMENTAL — SOMAS ANTERIOR/POSTERIOR + CORES (MODO OBSERVAÇÃO)</div><div style="margin-top:7px;font-size:12px;color:#e5e7eb;line-height:1.6">{texto_experimento}</div><div style="margin-top:6px;font-size:11px;color:#9ca3af">Não altera os sinais atuais. Resultados históricos descritivos; caminhos podem se sobrepor.</div></div>
 <div style="margin-top:14px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#9ca3af">MEMÓRIA V11 — DESEMPENHO POR RODADA (DUAS RODADAS POR MINUTO)</div><div style="margin-top:7px;font-size:12px;color:#e5e7eb;line-height:1.6">{texto_memoria_v4}</div><div style="margin-top:6px;font-size:11px;color:#9ca3af">Cada minuto tem duas rodadas em ordem cronológica. A cobertura é avaliada separadamente em cada rodada e também se acertou em pelo menos uma. Atrasos contam somente a partir do minuto seguinte.</div></div>
 <div style="margin-top:16px;padding:14px;background:#1f2937;border-radius:12px"><div style="font-size:12px;color:#9ca3af">VALIDAÇÃO</div><div style="margin-top:6px">{detalhe if detalhe else explicacao}</div>{f'<div style="margin-top:7px;font-size:13px;color:#cbd5e1">Famílias: {familias}</div>' if familias else ''}</div>
<div style="margin-top:14px;font-size:13px;color:#cbd5e1;line-height:1.6">{explicacao}<br>Última rodada: {ultima_txt} • API: {api_txt}<br>Histórico analisado: {len(df):,} rodadas</div>
<div style="margin-top:12px;font-size:11px;color:#9ca3af;text-align:center">Estatística histórica experimental; não é garantia de resultado.</div>
</div>"""
print("Painel HTML calculado; execução em modo serviço.")
print("BLITZ4_RESULT=" + json.dumps({"status": str(status_html), "status_motor": str(status_painel), "hora": str(hora), "entrada": str(entrada_html), "protecao_aprovada": bool(cobertura_aprovada), "motivo": str(motivo), "atualizado": bool(dados_atualizados), "ultima_rodada": str(ultima_txt), "historico": int(len(df))}, ensure_ascii=False))



# Notificações são controladas pelo serviço worker.py.

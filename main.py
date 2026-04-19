import os
import json
import re
import pandas as pd
import gspread
from jinja2 import Template
from datetime import date, timedelta
from collections import Counter, defaultdict

# 🔑 CARGAR CREDENCIALES DESDE ENTORNO (GitHub Secrets)
SHEET_ID = os.environ["SHEET_ID"]
APPS_SCRIPT_URL = os.environ["APPS_SCRIPT_URL"]
CREDENTIALS_JSON = json.loads(os.environ["GOOGLE_CREDENTIALS"])

def get_semaforo(dias):
    if dias < 150: return "green", "🟢 Sin riesgo", "Viajes dentro del límite seguro."
    elif dias < 183: return "orange", "🟡 Posible riesgo", f"Acumulados {dias} días. Evalúa reducir estancias."
    else: return "red", "🔴 En riesgo", f"{dias} días. PODRÍAS perder domicilio fiscal."

def eventos_a_viajes(df):
    viajes, anomalias = [], []
    sal_act, pais_act, f_sal = None, None, None
    for _, row in df.iterrows():
        tipo, fecha, pais = row["TIPO"], row["FECHA"], str(row["PAIS"]).strip()
        if tipo == "SALIDA":
            if sal_act is not None: anomalias.append(f"⚠️ Salida sin entrada previa ({f_sal.strftime('%d/%m/%Y')})")
            sal_act, pais_act, f_sal = True, pais, fecha
        elif tipo == "ENTRADA" and sal_act is not None:
            dias = (fecha - f_sal).days - 1
            viajes.append({"salida":f_sal, "entrada":fecha, "pais":pais_act, "dias":max(0,dias), "en_curso":False})
            sal_act, pais_act, f_sal = None, None, None
        elif tipo == "ENTRADA" and sal_act is None:
            anomalias.append(f"⚠️ Entrada sin salida previa ({fecha.strftime('%d/%m/%Y')})")
    if sal_act is not None:
        hoy = date.today()
        dias = (hoy - f_sal).days - 1
        viajes.append({"salida":f_sal, "entrada":hoy, "pais":pais_act, "dias":max(0,dias), "en_curso":True})
        anomalias.append("ℹ️ Viaje en curso (sin retorno)")
    return pd.DataFrame(viajes), anomalias

PAIS_ISO = {"españa":"es","perú":"pe","usa":"us","estados unidos":"us","mexico":"mx","méxico":"mx",
    "colombia":"co","argentina":"ar","chile":"cl","ecuador":"ec","bolivia":"bo","brasil":"br",
    "italia":"it","francia":"fr","alemania":"de","canada":"ca","japon":"jp","china":"cn",
    "portugal":"pt","rusia":"ru","turquia":"tr","panama":"pa","costa rica":"cr","inglaterra":"gb",
    "reino unido":"gb","irlanda":"ie","austria":"at","suiza":"ch","holanda":"nl","paises bajos":"nl",
    "belgica":"be","noruega":"no","suecia":"se","dinamarca":"dk","finlandia":"fi","polonia":"pl",
    "grecia":"gr","islandia":"is","israel":"il","egipto":"eg","marruecos":"ma","sudafrica":"za",
    "australia":"au","nueva zelanda":"nz","corea del sur":"kr","singapur":"sg","malasia":"my",
    "indonesia":"id","filipinas":"ph","vietnam":"vn","india":"in","sri lanka":"lk","emiratos arabes":"ae","dubai":"ae"}

# 🔹 CONEXIÓN Y LECTURA
gc = gspread.service_account_from_dict(CREDENTIALS_JSON)
sheet = gc.open_by_key(SHEET_ID).sheet1
df = pd.DataFrame(sheet.get_all_records())

df.columns = [c.strip().upper().replace(" ", "_").replace("/", "_") for c in df.columns]
df = df.rename(columns={"TIPO_DE_MOVIMIENTO":"TIPO", "FECHA_DE_MOVIMIENTO":"FECHA", "PROCEDENCIA_DESTINO":"PAIS"})

def parsear_fecha_segura(val):
    if pd.isna(val): return None
    try: return pd.to_datetime(str(val).strip(), dayfirst=True, errors='coerce').date()
    except: return None

df["FECHA"] = df["FECHA"].apply(parsear_fecha_segura)
df = df.dropna(subset=["FECHA", "TIPO"]).sort_values("FECHA").reset_index(drop=True)
df["TIPO"] = df["TIPO"].astype(str).str.strip().str.upper()

hoy = date.today()
viajes_df, anomalias = eventos_a_viajes(df)

def contar_dias_ventana(vdf, fecha_ref, dias=365):
    if vdf.empty: return 0
    end = fecha_ref
    start = end - timedelta(days=dias-1)
    count = 0
    for _, v in vdf.iterrows():
        eff_s = v["salida"] + timedelta(days=1)
        eff_e = v["entrada"] - timedelta(days=1)
        if eff_s > eff_e: continue
        ov_s, ov_e = max(eff_s, start), min(eff_e, end)
        if ov_s <= ov_e: count += (ov_e - ov_s).days + 1
    return count

dias_12m = contar_dias_ventana(viajes_df, hoy, 365)
anio_act = hoy.year
dias_anio = contar_dias_ventana(viajes_df, date(anio_act, 12, 31), 365)
c12, e12, m12 = get_semaforo(dias_12m)
ca, ea, ma = get_semaforo(dias_anio)

# 🔹 GRÁFICA HISTÓRICA
chart_labels, chart_values = [], []
if not viajes_df.empty:
    dias_por_mes = defaultdict(int)
    for _, v in viajes_df.iterrows():
        eff_s = v["salida"] + timedelta(days=1)
        eff_e = v["entrada"] - timedelta(days=1)
        if eff_s > eff_e: continue
        cur = eff_s
        while cur <= eff_e:
            if cur.year >= 2000:
                key = f"{cur.year}-{cur.month:02d}"
                dias_por_mes[key] += 1
            cur += timedelta(days=1)
    meses_es = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
    for mes_key in sorted(dias_por_mes.keys()):
        year, month = mes_key.split("-")
        chart_labels.append(f"{meses_es[int(month)-1]} {year}")
        chart_values.append(dias_por_mes[mes_key])

# 🔹 PREPARAR JSON PARA JS
viajes_json = []
if not viajes_df.empty:
    for _, row in viajes_df.iterrows():
        viajes_json.append({
            "pais": row["pais"],
            "salida_str": row["salida"].strftime("%Y-%m-%d"),
            "entrada_str": row["entrada"].strftime("%Y-%m-%d")
        })

ranking = Counter()
if not viajes_df.empty: ranking = Counter(viajes_df[~viajes_df["en_curso"]]["pais"]).most_common(5)
ranking_data = [{"pais": p, "dias": d, "iso": PAIS_ISO.get(p.lower().strip(), "xx")} for p, d in ranking]

config = {
    "app_url": APPS_SCRIPT_URL, "dias_12m": dias_12m, "dias_anio": dias_anio, "anio_act": anio_act,
    "c12": c12, "e12": e12, "m12": m12, "ca": ca, "ea": ea, "ma": ma,
    "anomalias": anomalias, "ranking": ranking_data, "viajes": viajes_json,
    "chart_labels": chart_labels, "chart_values": chart_values
}
config_json = json.dumps(config)

# 🔹 PLANTILLA HTML (Exactamente la versión funcional)
html_template = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Control SUNAT - Miguel</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
<style>
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:12px;background:#f8f9fa;color:#111;line-height:1.4}
  .card{background:#fff;border-radius:12px;padding:16px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
  .status-green{border-left:5px solid #198754;padding-left:12px}
  .status-orange{border-left:5px solid #fd7e14;padding-left:12px}
  .status-red{border-left:5px solid #dc3545;padding-left:12px}
  h1,h2,h3{margin:0 0 8px;font-weight:600} h1{font-size:1.25rem} h2{font-size:1.1rem} h3{font-size:1rem}
  .metric{font-size:1.8rem;font-weight:700;margin:4px 0 8px}
  .badge{display:inline-block;padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:500;margin-right:6px}
  .badge-green{background:#d1e7dd;color:#0f5132} .badge-orange{background:#fff3cd;color:#664d03} .badge-red{background:#f8d7da;color:#842029}
  .alert{background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:10px;margin:8px 0;font-size:0.85rem;color:#664d03}
  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  @media(max-width:480px){.grid-2{grid-template-columns:1fr}}
  .chart-wrapper{position:relative;width:100%;height:240px;margin-top:8px;min-height:240px;background:#fafafa;border-radius:8px}
  canvas{display:block;width:100%!important;height:100%!important}
  .btn{display:inline-flex;align-items:center;gap:6px;background:#0d6efd;color:#fff;border:none;border-radius:8px;padding:10px 14px;font-size:0.9rem;cursor:pointer;margin:4px 2px}
  .btn:hover{opacity:0.95}.btn:active{transform:scale(0.98)}.btn-outline{background:transparent;border:1px solid #0d6efd;color:#0d6efd}
  .form-group{margin:10px 0} .form-group label{display:block;font-size:0.85rem;margin-bottom:4px;color:#495057}
  .form-group input,.form-group select{width:100%;padding:8px;border:1px solid #ced4da;border-radius:6px;font-size:0.9rem}
  .result-box{background:#f8f9fa;border-radius:8px;padding:10px;margin-top:10px;font-size:0.9rem}
  .hidden{display:none} .loading{opacity:0.6;pointer-events:none}
  .table-responsive{overflow-x:auto;margin-top:8px}
  table{width:100%;border-collapse:collapse;font-size:0.9rem}
  th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #eee}
  th{background:#f1f3f4;font-weight:600}
  tr.total-row{background:#e8f4fd;font-weight:700}
  .ranking-item{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #eee}
  .ranking-item:last-child{border-bottom:none}
  .ranking-flag{width:24px;height:16px;border-radius:3px;object-fit:cover;margin-right:10px;border:1px solid #eee}
  .ranking-pais{flex:1;font-weight:500}
  .ranking-cant{background:#0d6efd;color:#fff;border-radius:12px;padding:3px 10px;font-size:0.8rem;font-weight:600}
  .footer{text-align:center;font-size:0.7rem;color:#6c757d;margin-top:16px}
  .range-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  @media(max-width:380px){.range-grid{grid-template-columns:1fr}}
</style>
</head>
<body>

<div class="card status-{{c12}}">
  <h1>🇵🇪 Estado Residencia Fiscal</h1>
  <div class="metric">{{e12}}</div>
  <p style="margin:4px 0 0">{{m12}}</p>
  <p style="margin:8px 0 0"><strong>Máx. últimos 12m:</strong> {{dias_12m}} / 183 días</p>
  {% if anomalias %}<div class="alert">⚠️ Revisa: {{', '.join(anomalias)}}</div>{% endif %}
</div>

<div class="grid-2">
  <div class="card status-{{c12}}"><h3>📅 Últimos 12 meses</h3><div class="metric" style="font-size:1.6rem">{{dias_12m}} días</div><span class="badge badge-{{'green' if dias_12m<150 else 'orange' if dias_12m<183 else 'red'}}">{{e12}}</span></div>
  <div class="card status-{{ca}}"><h3>🗓️ Año {{anio_act}}</h3><div class="metric" style="font-size:1.6rem">{{dias_anio}} días</div><span class="badge badge-{{'green' if dias_anio<150 else 'orange' if dias_anio<183 else 'red'}}">{{ea}}</span></div>
</div>

<div class="card"><h2>📈 Días fuera por mes (histórico)</h2><div class="chart-wrapper"><canvas id="chart"></canvas></div></div>

<div class="card"><h2>🌍 Top 5 países visitados</h2>
  {% if ranking %}
    {% for r in ranking %}
    <div class="ranking-item"><img src="https://flagcdn.com/w40/{{r.iso}}.png" class="ranking-flag" alt="{{r.pais}}" onerror="this.style.display='none'"><span class="ranking-pais">{{r.pais|title}}</span><span class="ranking-cant">{{r.dias}} días</span></div>
    {% endfor %}
  {% else %}<p style="color:#6c757d;font-size:0.9rem">Sin datos de viajes completados aún.</p>{% endif %}
</div>

<div class="card">
  <button id="btn-add" class="btn">➕ Agregar itinerario</button>
  <div id="form-add" class="hidden" style="margin-top:12px">
    <div class="form-group"><label>Tipo</label><select id="add-tipo"><option value="SALIDA">Salida</option><option value="ENTRADA">Entrada</option></select></div>
    <div class="form-group"><label>Fecha (DD/MM/YYYY)</label><input type="text" id="add-fecha" placeholder="15/06/2025"></div>
    <div class="form-group"><label>País</label><input type="text" id="add-pais" placeholder="España"></div>
    <button id="btn-save" class="btn">💾 Guardar en Sheets</button>
    <div id="res-add" class="result-box hidden"></div>
  </div>
</div>

<div class="card">
  <button id="btn-proj" class="btn btn-outline">✈️ Proyectar viaje</button>
  <div id="form-proj" class="hidden" style="margin-top:12px">
    <div class="form-group"><label>Salida (DD/MM/YYYY)</label><input type="text" id="proj-s"></div>
    <div class="form-group"><label>Retorno (DD/MM/YYYY)</label><input type="text" id="proj-r"></div>
    <button id="btn-calc-proj" class="btn">📊 Calcular impacto</button>
    <div id="res-proj" class="result-box hidden"></div>
  </div>
</div>

<div class="card">
  <button id="btn-rango" class="btn" style="background:#6c757d">🔍 Analizar Rango de Fechas</button>
  <div id="form-rango" class="hidden" style="margin-top:12px">
    <div class="range-grid">
      <div class="form-group"><label>Inicio (DD/MM/YYYY)</label><input type="text" id="rng-ini" placeholder="01/01/2024"></div>
      <div class="form-group"><label>Fin (DD/MM/YYYY)</label><input type="text" id="rng-fin" placeholder="31/12/2024"></div>
    </div>
    <button id="btn-calc-rango" class="btn">📅 Calcular días acumulados</button>
    <div id="res-rango" class="result-box hidden"></div>
    <small style="color:#6c757d;display:block;margin-top:6px">⚠️ Máximo 12 meses (365 días) entre fechas.</small>
  </div>
</div>

<div class="footer">Cálculo según Art. 7° LIR. No sustituye asesoría tributaria.</div>

<script id="app-config" type="application/json">{{ config_json | safe }}</script>
<script>
document.addEventListener('DOMContentLoaded', function() {
  try {
    const cfg = JSON.parse(document.getElementById('app-config').textContent);
    console.log('✅ Config OK | Viajes:', cfg.viajes.length, '| Chart:', cfg.chart_labels.length);
    const parseFecha = s => { const p=s.trim().split('/'); return new Date(+p[2], +p[1]-1, +p[0]); };
    const diasEntre = (f1,f2) => Math.max(0, Math.floor((parseFecha(f2)-parseFecha(f1)-864e5)/864e5));
    const formatoMes = ym => { const [y,m]=ym.split('-'); return ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][+m-1]+' '+y; };
    const toggle = id => document.getElementById(id).classList.toggle('hidden');
    const showRes = (id, html) => { const el=document.getElementById(id); el.innerHTML=html; el.classList.remove('hidden'); };
    const regex = /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/;
    document.getElementById('btn-add').onclick = () => toggle('form-add');
    document.getElementById('btn-proj').onclick = () => toggle('form-proj');
    document.getElementById('btn-rango').onclick = () => toggle('form-rango');
    document.getElementById('btn-save').onclick = async function() {
      const t = document.getElementById('add-tipo').value, f = document.getElementById('add-fecha').value, p = document.getElementById('add-pais').value;
      if (!regex.test(f)) return showRes('res-add', '❌ Usa formato DD/MM/YYYY');
      this.classList.add('loading'); this.textContent = '⏳...';
      try {
        const res = await fetch(cfg.app_url, { method: 'POST', mode: 'cors', headers: {'Content-Type':'text/plain'}, body: JSON.stringify({tipo:t, fecha:f, pais:p}) });
        const d = await res.json();
        showRes('res-add', d.status==='success' ? '✅ Guardado. Refresca reporte en Colab.' : '❌ '+(d.message||'Error'));
      } catch(e) { showRes('res-add', '❌ Error de red o URL inválida'); }
      finally { this.classList.remove('loading'); this.textContent = '💾 Guardar en Sheets'; }
    };
    document.getElementById('btn-calc-proj').onclick = function() {
      const s = document.getElementById('proj-s').value, r_ = document.getElementById('proj-r').value;
      if (!regex.test(s)||!regex.test(r_)) return showRes('res-proj', '❌ Usa DD/MM/YYYY');
      if (parseFecha(r_) < parseFecha(s)) return showRes('res-proj', '❌ Retorno debe ser posterior');
      const dn = diasEntre(s,r_), tot = cfg.dias_12m + dn;
      let c,e,m;
      if(tot<150){c='green';e='🟢 Sin riesgo';m='NO afecta residencia'}
      else if(tot<183){c='orange';e='🟡 Posible riesgo';m='Acumularías '+tot+' días'}
      else{c='red';e='🔴 En riesgo';m='⚠️ Alcanzarías '+tot+' días. Riesgo fiscal'}
      showRes('res-proj', '<strong>Proyección:</strong><br>• Días viaje: '+dn+'<br>• Total (12m): '+tot+'/183<br><span class="badge badge-'+c+'">'+e+'</span> '+m);
    };
    document.getElementById('btn-calc-rango').onclick = function() {
      const i = document.getElementById('rng-ini').value, f = document.getElementById('rng-fin').value;
      if (!regex.test(i)||!regex.test(f)) return showRes('res-rango', '❌ Usa DD/MM/YYYY');
      const rStart=parseFecha(i), rEnd=parseFecha(f);
      if(rEnd<rStart) return showRes('res-rango', '❌ Fin debe ser posterior a Inicio');
      const diffDias=Math.ceil((rEnd-rStart)/864e5);
      if(diffDias>365) return showRes('res-rango', '❌ Máximo 12 meses (365 días)');
      const mensual={}; let total=0;
      cfg.viajes.forEach(v=>{
        const vS=new Date(v.salida_str), vE=new Date(v.entrada_str);
        const effS=new Date(vS.getTime()+864e5), effE=new Date(vE.getTime()-864e5);
        if(effS>effE) return;
        const ovS=new Date(Math.max(effS,rStart)), ovE=new Date(Math.min(effE,rEnd));
        if(ovS<=ovE){ let cur=new Date(ovS); while(cur<=ovE){ const key=cur.getFullYear()+'-'+String(cur.getMonth()+1).padStart(2,'0'); mensual[key]=(mensual[key]||0)+1; total++; cur.setDate(cur.getDate()+1); } }
      });
      let tabla='<div class="table-responsive"><table><thead><tr><th>Mes</th><th style="text-align:right">Días</th></tr></thead><tbody>';
      Object.entries(mensual).sort().forEach(([m,d])=>tabla+='<tr><td>'+formatoMes(m)+'</td><td style="text-align:right">'+d+'</td></tr>');
      tabla+='<tr class="total-row"><td>TOTAL</td><td style="text-align:right">'+total+'</td></tr></tbody></table></div>';
      let c,e,m;
      if(total<150){c='green';e='🟢 Sin riesgo';m='Acumulado seguro.'}
      else if(total<183){c='orange';e='🟡 Posible riesgo';m='Acumulaste '+total+' días.'}
      else{c='red';e='🔴 En riesgo';m='⚠️ Superaste '+total+' días.'}
      showRes('res-rango', '<strong>📊 Rango: '+i+' → '+f+'</strong><br>• Días periodo: '+diffDias+'<br><br>'+tabla+'<br><span class="badge badge-'+c+'">'+e+'</span> '+m);
    };
    const ctx = document.getElementById('chart');
    if (!cfg.chart_labels || cfg.chart_labels.length === 0) {
      ctx.parentElement.innerHTML = '<p style="text-align:center;color:#6c757d;padding:40px">Sin datos históricos para graficar.</p>';
    } else {
      new Chart(ctx, { type: 'bar', data: { labels: cfg.chart_labels, datasets: [{ label: 'Días fuera', data: cfg.chart_values, backgroundColor: 'rgba(13,110,253,0.6)', borderColor: '#0d6efd', borderWidth: 1, borderRadius: 4, hoverBackgroundColor: 'rgba(13,110,253,0.9)' }] }, options: { responsive: true, maintainAspectRatio: false, animation: { duration: 0 }, plugins: { legend: { display: false }, tooltip: { enabled: true, backgroundColor: 'rgba(0,0,0,0.85)', titleFont: { size: 11 }, bodyFont: { size: 10 }, padding: 6 } }, scales: { x: { ticks: { maxRotation: 45, minRotation: 45, autoSkip: true, maxTicksLimit: 12, font: { size: 9 } }, grid: { display: false } }, y: { beginAtZero: true, ticks: { stepSize: 10, font: { size: 9 } }, grid: { color: 'rgba(0,0,0,0.05)' } } } } });
      console.log('📈 Gráfica renderizada | Valores:', cfg.chart_values.slice(0,5));
    }
  } catch(err) { console.error('❌ Error JS:', err); }
});
</script>
</body>
</html>"""

# 🔹 GENERAR ARCHIVO FINAL
html_content = Template(html_template).render(
    dias_12m=dias_12m, c12=c12, e12=e12, m12=m12,
    dias_anio=dias_anio, ca=ca, ea=ea, ma=ma, anio_act=anio_act,
    anomalias=anomalias, ranking=ranking_data,
    config_json=config_json
)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
print("✅ index.html generado exitosamente para GitHub Pages.")
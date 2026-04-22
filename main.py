# =============================================================================
# CONTROL SUNAT - VERSIÓN FINAL ESTABLE (Login + Vértices + Lógica SUNAT)
# =============================================================================
import os, json, pandas as pd, gspread
from jinja2 import Template
from datetime import date, timedelta
from collections import Counter, defaultdict

# 🔑 CONFIGURACIÓN
SHEET_ID = os.environ["SHEET_ID"]
APPS_SCRIPT_URL = os.environ["APPS_SCRIPT_URL"]
CREDENTIALS_JSON = json.loads(os.environ["GOOGLE_CREDENTIALS"])
LIMITE_SUNAT = 183

PAIS_ISO = {"españa":"es","perú":"pe","usa":"us","estados unidos":"us","mexico":"mx","méxico":"mx",
    "colombia":"co","argentina":"ar","chile":"cl","ecuador":"ec","bolivia":"bo","brasil":"br",
    "italia":"it","francia":"fr","alemania":"de","canada":"ca","japon":"jp","china":"cn",
    "portugal":"pt","rusia":"ru","turquia":"tr","panama":"pa","costa rica":"cr","inglaterra":"gb",
    "reino unido":"gb","irlanda":"ie","austria":"at","suiza":"ch","holanda":"nl","paises bajos":"nl",
    "belgica":"be","noruega":"no","suecia":"se","dinamarca":"dk","finlandia":"fi","polonia":"pl",
    "grecia":"gr","islandia":"is","israel":"il","egipto":"eg","marruecos":"ma","sudafrica":"za",
    "australia":"au","nueva zelanda":"nz","corea del sur":"kr","singapur":"sg","malasia":"my",
    "indonesia":"id","filipinas":"ph","vietnam":"vn","india":"in","sri lanka":"lk","emiratos arabes":"ae","dubai":"ae"}

def parse_fecha(val):
    if pd.isna(val): return None
    try: return pd.to_datetime(str(val).strip(), dayfirst=True, errors='coerce').date()
    except: return None

def eventos_a_viajes(df):
    """Empareja SALIDA->ENTRADA por orden cronológico (FIFO) ignorando país de retorno."""
    viajes, buffer, anomalias = [], {"M":[], "R":[], "P":[]}, []
    for _, r in df.iterrows():
        t, f, p, e = r["TIPO"], r["FECHA"], str(r["PAIS"]).strip(), r["ESTADO"]
        if e not in buffer: continue
        if t == "SALIDA":
            buffer[e].append({"s": f, "e": e, "p": p})
        elif t == "ENTRADA" and buffer[e]:
            ini = buffer[e].pop(0)
            d = max(0, (f - ini["s"]).days - 1)  # Regla SUNAT estricta
            viajes.append({"salida":ini["s"], "entrada":f, "pais":ini["p"], "dias":d, "estado":ini["e"], "en_curso":False})
        elif t == "ENTRADA":
            anomalias.append(f"⚠️ Entrada sin salida {p}")
    hoy = date.today()
    for est, list in buffer.items():
        for ini in list:
            d = max(0, (hoy - ini["s"]).days - 1)
            viajes.append({"salida":ini["s"], "entrada":hoy, "pais":ini["p"], "dias":d, "estado":est, "en_curso":True})
            anomalias.append(f"ℹ️ En curso {ini['p']}")
    return pd.DataFrame(viajes), anomalias

def resumen_ventana(vdf, ref, dias=365):
    if vdf.empty: return {"M":0,"R":0,"P":0}, 0
    end, start = ref, ref - timedelta(days=dias-1)
    tot = {"M":0,"R":0,"P":0}
    for _, v in vdf.iterrows():
        s, e = max(v["salida"]+timedelta(days=1), start), min(v["entrada"]-timedelta(days=1), end)
        if s <= e: tot[v["estado"]] += (e - s).days + 1
    return tot, sum(tot.values())

def grafica_mensual(vdf):
    datos = defaultdict(lambda: {"M":0,"R":0,"P":0})
    if vdf.empty: return datos
    for _, v in vdf.iterrows():
        s, e = v["salida"]+timedelta(days=1), v["entrada"]-timedelta(days=1)
        if s > e: continue
        c = s
        while c <= e:
            if c.year >= 2000: datos[f"{c.year}-{c.month:02d}"][v["estado"]] += 1
            c += timedelta(days=1)
    return dict(sorted(datos.items()))

# 🔹 PROCESAMIENTO DE DATOS
gc = gspread.service_account_from_dict(CREDENTIALS_JSON)
sheet = gc.open_by_key(SHEET_ID).sheet1
df = pd.DataFrame(sheet.get_all_records())
df.columns = [c.strip().upper().replace(" ","_").replace("/","_") for c in df.columns]
df = df.rename(columns={"TIPO_DE_MOVIMIENTO":"TIPO","FECHA_DE_MOVIMIENTO":"FECHA","PROCEDENCIA_DESTINO":"PAIS","ESTADO":"ESTADO"})
df["FECHA"] = df["FECHA"].apply(parse_fecha)
df = df.dropna(subset=["FECHA","TIPO","ESTADO"]).sort_values("FECHA").reset_index(drop=True)
df["TIPO"] = df["TIPO"].astype(str).str.strip().str.upper()
df["ESTADO"] = df["ESTADO"].astype(str).str.strip().str.upper()
df = df[df["ESTADO"].isin(["M","R","P"])]

hoy = date.today()
viajes_df, anomalias = eventos_a_viajes(df)
d12m, t12m = resumen_ventana(viajes_df, hoy)
danio, tanio = resumen_ventana(viajes_df, date(hoy.year, 12, 31))

if t12m < 150: c12,e12,m12 = "green","🟢 Sin riesgo","Límite seguro"
elif t12m < 183: c12,e12,m12 = "orange","🟡 Posible riesgo",f"Acumulados {t12m} días"
else: c12,e12,m12 = "red","🔴 En riesgo",f"{t12m} días. Riesgo fiscal"
ca,ea,ma = ("green","🟢 Sin riesgo","") if tanio<150 else ("orange","🟡 Posible riesgo","") if tanio<183 else ("red","🔴 En riesgo","")

grafica = grafica_mensual(viajes_df)
meses_es = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
lbl, vM, vR, vP = [], [], [], []
for k in sorted(grafica):
    y, m = k.split("-")
    lbl.append(f"{meses_es[int(m)-1]} {y}")
    vM.append(grafica[k]["M"]); vR.append(grafica[k]["R"]); vP.append(grafica[k]["P"])

ranking = {}
for est in ["M","R","P"]:
    fil = viajes_df[(viajes_df["estado"]==est) & (~viajes_df["en_curso"])]
    dias_p = defaultdict(int)
    for _, r in fil.iterrows(): dias_p[r["pais"]] += int(r["dias"])
    ranking[est] = [{"pais":p,"dias":d,"iso":PAIS_ISO.get(p.lower().strip(),"xx")} for p,d in sorted(dias_p.items(), key=lambda x: x[1], reverse=True)[:5]]

# 🔑 SERIALIZACIÓN SEGURA (Evita TypeError: date not JSON serializable)
viajes_js = []
if not viajes_df.empty:
    for _, r in viajes_df.iterrows():
        viajes_js.append({
            "pais": str(r["pais"]),
            "salida_str": r["salida"].strftime("%Y-%m-%d"),
            "entrada_str": r["entrada"].strftime("%Y-%m-%d"),
            "estado": str(r["estado"]),
            "en_curso": bool(r["en_curso"]),
            "dias": int(r["dias"])
        })

config_str = json.dumps({
    "app_url": APPS_SCRIPT_URL, "dias_12m": int(t12m), "dias_por_estado_12m": d12m,
    "dias_anio": int(tanio), "dias_por_estado_anio": danio, "anomalias": anomalias,
    "ranking": ranking, "viajes": viajes_js, "lbl": lbl, "vM": vM, "vR": vR, "vP": vP, "limite": LIMITE_SUNAT
})

# =============================================================================
# PLANTILLA HTML (LOGIN + VÉRTICES + DASHBOARD FUNCIONAL)
# =============================================================================
html_template = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Control SUNAT</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
  body{font-family:system-ui,-apple-system,sans-serif;overflow-x:hidden;background:#f8f9fa}
  #bg{position:fixed;top:0;left:0;width:100%;height:100vh;z-index:1}
  #login{position:fixed;top:0;left:0;width:100%;height:100vh;background:linear-gradient(135deg,#0f172a,#1e293b);z-index:100;display:flex;align-items:center;justify-content:center}
  #box{position:relative;z-index:101;background:rgba(255,255,255,0.95);backdrop-filter:blur(10px);padding:30px;border-radius:16px;width:90%;max-width:340px;text-align:center;box-shadow:0 25px 50px rgba(0,0,0,0.5)}
  #box input{width:100%;padding:14px;margin:8px 0 16px;border:2px solid #e2e8f0;border-radius:8px;font-size:1rem}
  #box button{width:100%;padding:14px;background:#3b82f6;color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer}
  #app{display:none;padding:12px;position:relative;z-index:50;min-height:100vh;background:#f8f9fa}
  .card{background:#fff;border-radius:12px;padding:16px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
  .status-green{border-left:5px solid #198754}.status-orange{border-left:5px solid #fd7e14}.status-red{border-left:5px solid #dc3545}
  h1,h2,h3{margin:0 0 8px;font-weight:600}h1{font-size:1.25rem}h2{font-size:1.1rem}h3{font-size:1rem}
  .metric{font-size:1.8rem;font-weight:700;margin:4px 0 8px}
  .badge{display:inline-block;padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:500;margin-right:6px}
  .badge-green{background:#d1e7dd;color:#0f5132}.badge-orange{background:#fff3cd;color:#664d03}.badge-red{background:#f8d7da;color:#842029}
  .badge-M{background:#3b82f6;color:#fff}.badge-R{background:#22c55e;color:#fff}.badge-P{background:#f59e0b;color:#000}
  .alert{background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:10px;margin:8px 0;font-size:0.85rem;color:#664d03}
  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:480px){.grid-2{grid-template-columns:1fr}}
  .chart-wrap{position:relative;width:100%;height:240px;margin-top:8px;background:#fafafa;border-radius:8px}
  canvas{display:block;width:100%!important;height:100%!important}
  .btn{background:#0d6efd;color:#fff;border:none;border-radius:8px;padding:10px 14px;cursor:pointer;margin:4px 2px}
  .btn-outline{background:transparent;border:1px solid #0d6efd;color:#0d6efd}
  .hidden{display:none}
  table{width:100%;border-collapse:collapse;font-size:0.9rem;margin-top:10px}
  th,td{padding:8px;border-bottom:1px solid #eee;text-align:left}
  .ranking-item{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #eee}
  .ranking-flag{width:24px;height:16px;border-radius:3px;object-fit:cover;margin-right:10px;border:1px solid #eee}
  .footer{text-align:center;font-size:0.7rem;color:#6c757d;margin-top:16px}
</style>
</head>
<body>

<canvas id="bg"></canvas>

<div id="login">
  <div id="box">
    <h2>🔐 Acceso Seguro</h2>
    <input id="u" placeholder="Usuario" value="admin">
    <input id="p" type="password" placeholder="Contraseña">
    <button onclick="entrar()">INGRESAR</button>
    <p id="err" style="color:red;display:none;margin-top:10px">Usuario o contraseña incorrectos</p>
  </div>
</div>

<div id="app">
  <div class="card status-{{c12}}">
    <h1>🇵🇪 Estado Residencia Fiscal</h1>
    <div class="metric">{{e12}}</div>
    <p style="margin:4px 0 0">{{m12}}</p>
    <p style="margin:8px 0 0"><strong>Total 12m:</strong> {{dias_12m}} / {{limite}}d</p>
    <div style="font-size:0.85rem;color:#6c757d">🔵M:{{dias_por_estado_12m.M}} | 🟢R:{{dias_por_estado_12m.R}} | 🟡P:{{dias_por_estado_12m.P}}</div>
    {% if anomalias %}<div class="alert">⚠️ {{', '.join(anomalias[:3])}}</div>{% endif %}
  </div>
  <div class="grid-2">
    <div class="card status-{{c12}}"><h3>📅 12 meses</h3><div class="metric" style="font-size:1.6rem">{{dias_12m}}d</div><span class="badge badge-{{'green' if dias_12m<150 else 'orange' if dias_12m<183 else 'red'}}">{{e12}}</span></div>
    <div class="card status-{{ca}}"><h3>🗓️ Año {{anio_act}}</h3><div class="metric" style="font-size:1.6rem">{{dias_anio}}d</div><span class="badge badge-{{'green' if dias_anio<150 else 'orange' if dias_anio<183 else 'red'}}">{{ea}}</span></div>
  </div>
  <div class="card"><h2>📈 Evolución (M/R/P)</h2><div class="chart-wrap"><canvas id="chart"></canvas></div></div>
  <div class="card"><h2>🌍 Top Países</h2><div id="rank"></div></div>
  <div class="card"><h2>📋 Historial</h2>
    <div style="margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-outline" onclick="window.filtro('todos')">Todos</button>
      <button class="btn" style="background:#3b82f6" onclick="window.filtro('M')">M</button>
      <button class="btn" style="background:#22c55e" onclick="window.filtro('R')">R</button>
      <button class="btn" style="background:#f59e0b;color:#000" onclick="window.filtro('P')">P</button>
    </div>
    <div id="hist"></div>
  </div>
  <div class="footer">Cálculo Art. 7° LIR. No sustituye asesoría.</div>
</div>

<script id="app-config" type="application/json">{{ config_str | safe }}</script>
<script>
// ==========================================
// 1. ANIMACIÓN VÉRTICES (Ejecuta inmediatamente)
// ==========================================
(function(){
  const cv=document.getElementById('bg'),x=cv.getContext('2d');let pts=[];
  function rsz(){cv.width=innerWidth;cv.height=innerHeight}
  addEventListener('resize',rsz);rsz();
  for(let i=0;i<60;i++)pts.push({x:Math.random()*cv.width,y:Math.random()*cv.height,vx:Math.random()*2-1,vy:Math.random()*2-1});
  function loop(){x.clearRect(0,0,cv.width,cv.height);x.fillStyle='rgba(147,197,253,0.7)';
    pts.forEach(p=>{p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>cv.width)p.vx*=-1;if(p.y<0||p.y>cv.height)p.vy*=-1;
    x.beginPath();x.arc(p.x,p.y,2,0,Math.PI*2);x.fill()});
    for(let i=0;i<pts.length;i++)for(let j=i;j<pts.length;j++){
      const dx=pts[i].x-pts[j].x,dy=pts[i].y-pts[j].y,d=Math.sqrt(dx*dx+dy*dy);
      if(d<150){x.strokeStyle=`rgba(147,197,253,${0.4-d/375})`;x.lineWidth=1;x.beginPath();x.moveTo(pts[i].x,pts[i].y);x.lineTo(pts[j].x,pts[j].y);x.stroke();}
    }
  requestAnimationFrame(loop);}
  loop();
})();

// ==========================================
// 2. LOGIN
// ==========================================
function entrar(){
  if(document.getElementById('u').value==='admin'&&document.getElementById('p').value==='admin'){
    document.getElementById('login').style.display='none';
    document.getElementById('app').style.display='block';
    renderDashboard();
  } else { document.getElementById('err').style.display='block'; }
}
document.getElementById('p').addEventListener('keypress', e => { if(e.key==='Enter') entrar(); });

// ==========================================
// 3. DASHBOARD (Solo tras login exitoso)
// ==========================================
function renderDashboard(){
  try {
    const raw = document.getElementById('app-config')?.textContent;
    if(!raw) throw new Error("Configuración no encontrada");
    const C = JSON.parse(raw);
    console.log('✅ Dashboard cargado | Meses:', C.lbl.length);

    // Tabla
    window.filtro = f => {
      const t = document.getElementById('hist'); if(!t) return;
      const d = f==='todos' ? C.viajes : C.viajes.filter(v=>v.estado===f);
      let h = '<table><tr><th>Salida</th><th>Retorno</th><th>País</th><th>Días</th><th>Estado</th></tr>';
      if(!d.length) h += '<tr><td colspan="5" style="text-align:center;color:#6c757d;padding:15px">Sin datos</td></tr>';
      else d.slice().reverse().forEach(v => h += `<tr><td>${v.salida_str.split('-').reverse().join('/')}</td><td>${v.entrada_str.split('-').reverse().join('/')}</td><td>${v.pais}</td><td style="text-align:right">${v.dias}</td><td><span class="badge badge-${v.estado}">${v.estado}</span></td></tr>`);
      t.innerHTML = h + '</table>';
    };
    window.filtro('todos');

    // Ranking
    let rhtml = '<div style="display:flex;gap:10px;overflow-x:auto;padding-bottom:5px">';
    for(let est of ["M","R","P"]){
      rhtml += `<div style="background:#f8f9fa;padding:10px;min-width:120px;border-radius:8px"><strong class="badge badge-${est}">${est}</strong>`;
      (C.ranking[est]||[]).forEach(r => rhtml += `<div class="ranking-item"><img src="https://flagcdn.com/w40/${r.iso}.png" class="ranking-flag" onerror="this.style.display='none'"><span style="font-size:0.8rem">${r.pais}</span><span style="font-size:0.75rem;background:#e9ecef;padding:2px 6px;border-radius:4px">${r.dias}d</span></div>`);
      rhtml += '</div>';
    }
    document.getElementById('rank').innerHTML = rhtml + '</div>';

    // Gráfico
    setTimeout(() => {
      const ctx = document.getElementById('chart');
      if(ctx && C.lbl.length > 0){
        new Chart(ctx, {
          type: 'line',
           {
            labels: C.lbl,
            datasets: [
              {label:'Migraciones', data:C.vM, borderColor:'#3b82f6', backgroundColor:'rgba(59,130,246,0.1)', fill:true, tension:0.3},
              {label:'Registro', data:C.vR, borderColor:'#22c55e', backgroundColor:'rgba(34,197,94,0.1)', fill:true, tension:0.3},
              {label:'Proyectado', data:C.vP, borderColor:'#f59e0b', backgroundColor:'rgba(245,158,11,0.1)', fill:true, tension:0.3}
            ]
          },
          options: {responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{ticks:{maxRotation:45,autoSkip:true,maxTicksLimit:10,font:{size:9}},grid:{display:false}},y:{beginAtZero:true,ticks:{stepSize:5,font:{size:9}},grid:{color:'rgba(0,0,0,0.05)'}}}}
        });
      }
    }, 150);

  } catch(err) {
    console.error('❌ Error Dashboard:', err);
    alert('Error al cargar el panel. Revisa F12 > Console.');
  }
}
</script>
</body>
</html>"""

# =============================================================================
# GENERACIÓN FINAL
# =============================================================================
html_final = Template(html_template).render(
    c12=c12, e12=e12, m12=m12, limite=LIMITE_SUNAT,
    dias_12m=t12m, dias_por_estado_12m=d12m, anomalias=anomalias,
    ca=ca, ea=ea, ma=ma, anio_act=hoy.year, dias_anio=tanio,
    ranking=ranking, config_str=config_str
)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_final)
print("✅ index.html generado correctamente | Login: admin / admin")


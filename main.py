# =============================================================================
# CONTROL SUNAT - VERSIÓN ESTABLE (Login + Animación + Dashboard)
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
    viajes, buffer, anomalias = [], {"M":[], "R":[], "P":[]}, []
    for _, r in df.iterrows():
        t, f, p, e = r["TIPO"], r["FECHA"], str(r["PAIS"]).strip(), r["ESTADO"]
        if e not in buffer: continue
        if t == "SALIDA": buffer[e].append({"s": f, "e": e, "p": p})
        elif t == "ENTRADA" and buffer[e]:
            ini = buffer[e].pop(0)
            d = max(0, (f - ini["s"]).days - 1)
            viajes.append({"salida":ini["s"], "entrada":f, "pais":ini["p"], "dias":d, "estado":ini["e"], "en_curso":False})
        elif t == "ENTRADA": anomalias.append(f"⚠️ Entrada sin salida {p}")
    hoy = date.today()
    for est, lst in buffer.items():
        for ini in lst:
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

# 🔹 PROCESAMIENTO
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

# 🔑 SERIALIZACIÓN SEGURA
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

config_data = {
    "app_url": APPS_SCRIPT_URL, "dias_12m": int(t12m), "dias_por_estado_12m": d12m,
    "dias_anio": int(tanio), "dias_por_estado_anio": danio, "anomalias": anomalias,
    "ranking": ranking, "viajes": viajes_js, "lbl": lbl, "vM": vM, "vR": vR, "vP": vP, "limite": LIMITE_SUNAT
}
config_str = json.dumps(config_data, ensure_ascii=False)

# =============================================================================
# PLANTILLA HTML (LOGIN + ANIMACIÓN + DASHBOARD - VALIDADA)
# =============================================================================
html_template = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Control SUNAT</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,-apple-system,sans-serif;overflow-x:hidden;background:#0f172a}
  #bg-canvas{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0}
  #login-screen{position:fixed;top:0;left:0;width:100%;height:100%;z-index:10;display:flex;align-items:center;justify-content:center}
  #login-box{position:relative;z-index:11;background:rgba(255,255,255,0.95);backdrop-filter:blur(10px);padding:30px;border-radius:12px;width:90%;max-width:340px;text-align:center;box-shadow:0 20px 40px rgba(0,0,0,0.4)}
  #login-box h2{margin:0 0 20px;color:#0f172a;font-size:1.5rem}
  #login-box input{width:100%;padding:12px;margin:8px 0;border:2px solid #e2e8f0;border-radius:8px;font-size:1rem}
  #login-box input:focus{border-color:#2563eb;outline:none}
  #login-box button{width:100%;padding:12px;background:#2563eb;color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;margin-top:10px}
  #login-err{color:#ef4444;font-size:0.85rem;margin-top:10px;display:none}
  #main-app{display:none;position:relative;z-index:20;min-height:100vh;padding:12px;background:#f8f9fa}
  
  .card{background:#fff;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 2px 6px rgba(0,0,0,0.06)}
  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:480px){.grid-2{grid-template-columns:1fr}}
  .chart-wrap{position:relative;height:240px;margin-top:8px;background:#fafafa;border-radius:8px}
  canvas{display:block;width:100%!important;height:100%!important}
  .btn{background:#2563eb;color:#fff;border:none;border-radius:6px;padding:8px 12px;cursor:pointer;margin:4px 2px;font-size:0.9rem}
  .btn-outline{background:transparent;border:1px solid #2563eb;color:#2563eb}
  table{width:100%;border-collapse:collapse;font-size:0.85rem;margin-top:8px}
  th,td{padding:8px;border-bottom:1px solid #eee;text-align:left}
  .badge-M{background:#3b82f6;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.75rem}
  .badge-R{background:#22c55e;color:#fff;padding:2px 6px;border-radius:4px;font-size:0.75rem}
  .badge-P{background:#f59e0b;color:#000;padding:2px 6px;border-radius:4px;font-size:0.75rem}
  .rank-box{background:#f8fafc;padding:10px;border-radius:6px;min-width:130px;margin:5px}
  .rank-item{display:flex;justify-content:space-between;align-items:center;font-size:0.8rem;margin:5px 0}
  .flag{width:20px;height:14px;border-radius:2px;object-fit:cover;margin-right:6px}
  .footer{text-align:center;font-size:0.7rem;color:#6c757d;margin-top:16px}
</style>
</head>
<body>

<canvas id="bg-canvas"></canvas>

<div id="login-screen">
  <div id="login-box">
    <h2>🔐 Acceso Seguro</h2>
    <input id="user" type="text" placeholder="Usuario" value="admin" autocomplete="off">
    <input id="pass" type="password" placeholder="Contraseña">
    <button onclick="checkLogin()">INGRESAR</button>
    <p id="login-err">Credenciales incorrectas</p>
  </div>
</div>

<div id="main-app">
  <div class="card"><h1>🇵🇪 Estado Residencia Fiscal</h1><p id="sum" style="margin-top:6px;font-size:0.95rem"></p></div>
  <div class="grid-2">
    <div class="card"><h3>📅 Últimos 12m</h3><p id="d12" style="font-size:1.4rem;font-weight:600"></p></div>
    <div class="card"><h3>🗓️ Año Actual</h3><p id="da" style="font-size:1.4rem;font-weight:600"></p></div>
  </div>
  <div class="card"><h2>📈 Evolución Mensual</h2><div class="chart-wrap"><canvas id="chart"></canvas></div></div>
  <div class="card"><h2>🌍 Top Países</h2><div id="rank" style="display:flex;gap:10px;overflow-x:auto;padding:5px 0"></div></div>
  <div class="card"><h2>📋 Historial</h2>
    <div style="display:flex;gap:6px;margin-bottom:8px">
      <button class="btn btn-outline" onclick="filt('todos')">Todos</button>
      <button class="btn" style="background:#3b82f6" onclick="filt('M')">M</button>
      <button class="btn" style="background:#22c55e" onclick="filt('R')">R</button>
      <button class="btn" style="background:#f59e0b;color:#000" onclick="filt('P')">P</button>
    </div>
    <div id="hist"></div>
  </div>
  <div class="footer">Cálculo según Art. 7° LIR. No sustituye asesoría tributaria.</div>
</div>

<script id="cfg" type="application/json">{{ config_str | safe }}</script>
<script>
// 1. ANIMACIÓN VÉRTICES (IIFE - Ejecución inmediata)
(function(){
  var c=document.getElementById('bg-canvas'), x=c.getContext('2d');
  var pts=[], w, h;
  function rsz(){w=c.width=window.innerWidth;h=c.height=window.innerHeight;}
  window.addEventListener('resize', rsz); rsz();
  for(var i=0;i<60;i++) pts.push({x:Math.random()*w, y:Math.random()*h, vx:(Math.random()-0.5)*1.5, vy:(Math.random()-0.5)*1.5});
  function draw(){
    x.clearRect(0,0,w,h); x.fillStyle='rgba(147,197,253,0.8)';
    for(var i=0;i<pts.length;i++){
      var p=pts[i]; p.x+=p.vx; p.y+=p.vy;
      if(p.x<0||p.x>w)p.vx*=-1; if(p.y<0||p.y>h)p.vy*=-1;
      x.beginPath(); x.arc(p.x,p.y,2.5,0,Math.PI*2); x.fill();
    }
    for(var i=0;i<pts.length;i++){
      for(var j=i+1;j<pts.length;j++){
        var dx=pts[i].x-pts[j].x, dy=pts[i].y-pts[j].y, d=Math.sqrt(dx*dx+dy*dy);
        if(d<150){x.strokeStyle='rgba(147,197,253,'+(0.4-d/375)+')'; x.lineWidth=1; x.beginPath(); x.moveTo(pts[i].x,pts[i].y); x.lineTo(pts[j].x,pts[j].y); x.stroke();}
      }
    }
    requestAnimationFrame(draw);
  }
  draw();
})();

// 2. LOGIN
function checkLogin(){
  if(document.getElementById('user').value==='admin' && document.getElementById('pass').value==='admin'){
    document.getElementById('login-screen').style.display='none';
    document.getElementById('main-app').style.display='block';
    loadDashboard();
  } else {
    var e=document.getElementById('login-err'); e.style.display='block'; setTimeout(function(){e.style.display='none';},2000);
  }
}
document.getElementById('pass').addEventListener('keypress', function(e){if(e.key==='Enter')checkLogin();});

// 3. DASHBOARD
function loadDashboard(){
  try {
    var raw = document.getElementById('cfg')?.textContent;
    if(!raw) throw new Error('Config no encontrada');
    window.C = JSON.parse(raw);
    
    document.getElementById('sum').textContent = 'Total: '+window.C.dias_12m+' / '+window.C.limite+' días';
    document.getElementById('d12').textContent = window.C.dias_12m+' días';
    document.getElementById('da').textContent = window.C.dias_anio+' días';

    var rh='';
    for(var est of ["M","R","P"]){
      rh+='<div class="rank-box"><strong>'+est+'</strong>';
      (window.C.ranking[est]||[]).forEach(function(r){rh+='<div class="rank-item"><img src="https://flagcdn.com/w40/'+r.iso+'.png" class="flag"><span>'+r.pais+'</span><span style="background:#e2e8f0;padding:2px 6px;border-radius:4px">'+r.dias+'d</span></div>';});
      rh+='</div>';
    }
    document.getElementById('rank').innerHTML=rh;

    window.filt=function(f){
      var d=f==='todos'?window.C.viajes:window.C.viajes.filter(function(v){return v.estado===f;});
      var h='<table><tr><th>Salida</th><th>Retorno</th><th>País</th><th>Días</th><th>Estado</th></tr>';
      if(!d.length) h+='<tr><td colspan="5" style="text-align:center;padding:15px;color:#64748b">Sin registros</td></tr>';
      else d.slice().reverse().forEach(function(v){h+='<tr><td>'+v.salida_str.split('-').reverse().join('/')+'</td><td>'+v.entrada_str.split('-').reverse().join('/')+'</td><td>'+v.pais+'</td><td style="text-align:right">'+v.dias+'</td><td><span class="badge-'+v.estado+'">'+v.estado+'</span></td></tr>';});
      document.getElementById('hist').innerHTML=h+'</table>';
    };
    window.filt('todos');

    setTimeout(function(){
      var ctx=document.getElementById('chart');
      if(ctx && window.C.lbl.length>0){
        new Chart(ctx, {
          type: 'line',
          data: {
            labels: window.C.lbl,
            datasets: [
              { label: 'Migraciones', data: window.C.vM, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', fill: true, tension: 0.3 },
              { label: 'Registro', data: window.C.vR, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', fill: true, tension: 0.3 },
              { label: 'Proyectado', data: window.C.vP, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', fill: true, tension: 0.3 }
            ]
          },
          options: {responsive: true, maintainAspectRatio: false, plugins: {legend: {display: false}}, scales: {x: {ticks: {maxRotation: 45, autoSkip: true, maxTicksLimit: 10}, grid: {display: false}}, y: {beginAtZero: true, ticks: {stepSize: 5}, grid: {color: '#e2e8f0'}}}}
        });
      }
    }, 100);
  } catch(e){ console.error(e); alert('Error al cargar: '+e.message); }
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
print("✅ index.html generado | Login + Animación + Dashboard base | Credenciales: admin/admin")

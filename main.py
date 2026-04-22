# =============================================================================
# CONTROL SUNAT - VERSIÓN COMPLETA (M/R/P + GRÁFICO LÍNEAS + BOTONES FUNCIONALES)
# =============================================================================
import os, json, re
import pandas as pd
import gspread
from jinja2 import Template
from datetime import date, timedelta
from collections import Counter, defaultdict

# 🔑 CONFIGURACIÓN
SHEET_ID = os.environ["SHEET_ID"]
APPS_SCRIPT_URL = os.environ["APPS_SCRIPT_URL"]
CREDENTIALS_JSON = json.loads(os.environ["GOOGLE_CREDENTIALS"])
LIMITE_SUNAT = 183

PAIS_ISO = {
    "españa":"es","perú":"pe","usa":"us","estados unidos":"us","mexico":"mx","méxico":"mx",
    "colombia":"co","argentina":"ar","chile":"cl","ecuador":"ec","bolivia":"bo","brasil":"br",
    "italia":"it","francia":"fr","alemania":"de","canada":"ca","japon":"jp","china":"cn",
    "portugal":"pt","rusia":"ru","turquia":"tr","panama":"pa","costa rica":"cr","inglaterra":"gb",
    "reino unido":"gb","irlanda":"ie","austria":"at","suiza":"ch","holanda":"nl","paises bajos":"nl",
    "belgica":"be","noruega":"no","suecia":"se","dinamarca":"dk","finlandia":"fi","polonia":"pl",
    "grecia":"gr","islandia":"is","israel":"il","egipto":"eg","marruecos":"ma","sudafrica":"za",
    "australia":"au","nueva zelanda":"nz","corea del sur":"kr","singapur":"sg","malasia":"my",
    "indonesia":"id","filipinas":"ph","vietnam":"vn","india":"in","sri lanka":"lk","emiratos arabes":"ae","dubai":"ae"
}
def normalizar_pais(nombre):
    """Unifica variaciones de nombres de países a una forma estándar"""
    if pd.isna(nombre): return "Desconocido"
    n = str(nombre).strip().lower()
    # Mapeo de variaciones comunes a nombre estándar
    equivalencias = {
        "eeuu": "Estados Unidos", "usa": "Estados Unidos", "us": "Estados Unidos",
        "united states": "Estados Unidos", "estados unidos": "Estados Unidos",
        "usa (estados unidos)": "Estados Unidos", "ee. uu.": "Estados Unidos",
        "méxico": "México", "mexico": "México", "méjico": "México",
        "reino unido": "Reino Unido", "inglaterra": "Reino Unido", "uk": "Reino Unido",
        "gran bretaña": "Reino Unido", "british": "Reino Unido",
        "colombia": "Colombia", "colômbia": "Colombia",
        "españa": "España", "spain": "España",
        # Agrega más según necesites
    }
    return equivalencias.get(n, str(nombre).strip().title())
# 🔹 FUNCIONES AUXILIARES
def parse_fecha(val):
    if pd.isna(val): return None
    try: return pd.to_datetime(str(val).strip(), dayfirst=True, errors='coerce').date()
    except: return None

def eventos_a_viajes(df):
    """Empareja SALIDA -> ENTRADA por orden cronológico (FIFO) ignorando país de retorno."""
    viajes, buffer, anomalias = [], {"M":[], "R":[], "P":[]}, []
    
    for _, r in df.iterrows():
        tipo, f, p, e = r["TIPO"], r["FECHA"], str(r["PAIS"]).strip(), r["ESTADO"]
        if e not in buffer: continue
        
        if tipo == "SALIDA":
            buffer[e].append({"salida": f, "estado": e, "pais": p})
        elif tipo == "ENTRADA":
            if not buffer[e]:
                anomalias.append(f"⚠️ {e}: Entrada sin salida ({f.strftime('%d/%m/%Y')})")
            else:
                ini = buffer[e].pop(0) # ✅ FIFO: cierra la salida más antigua pendiente
                dias = max(0, (f - ini["salida"]).days - 1) # Regla SUNAT
                viajes.append({
                    "salida": ini["salida"], "entrada": f,
                    "pais": ini["pais"],  # ✅ País del viaje = País de SALIDA
                    "dias": dias, "estado": ini["estado"], "en_curso": False
                })
                
    # Cierra viajes abiertos (en_curso)
    hoy = date.today()
    for e, pendientes in buffer.items():
        for ini in pendientes:
            dias = max(0, (hoy - ini["salida"]).days - 1)
            viajes.append({"salida":ini["salida"], "entrada":hoy, "pais":ini["pais"], "dias":dias, "estado":e, "en_curso":True})
            anomalias.append(f"ℹ️ {e}: En curso {ini['pais']}")
            
    return pd.DataFrame(viajes), anomalias

def resumen_ventana(vdf, ref, dias=365):
    """Calcula días acumulados por estado en ventana móvil"""
    if vdf.empty: return {"M":0,"R":0,"P":0}, 0
    end, start = ref, ref - timedelta(days=dias-1)
    tot = {"M":0,"R":0,"P":0}
    for _, v in vdf.iterrows():
        s = max(v["salida"] + timedelta(days=1), start)
        e = min(v["entrada"] - timedelta(days=1), end)
        if s <= e: tot[v["estado"]] += (e - s).days + 1
    return tot, sum(tot.values())

def grafica_mensual(vdf):
    """Agregación mensual exacta (misma regla SUNAT)"""
    datos = defaultdict(lambda: {"M":0,"R":0,"P":0})
    if vdf.empty: return datos
    for _, v in vdf.iterrows():
        s, e = v["salida"]+timedelta(days=1), v["entrada"]-timedelta(days=1)
        if s > e: continue
        c = s
        while c <= e:
            if c.year >= 2000:
                datos[f"{c.year}-{c.month:02d}"][v["estado"]] += 1
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

if t12m < 150: c12,e12,m12 = "green","🟢 Sin riesgo","Viajes dentro del límite."
elif t12m < 183: c12,e12,m12 = "orange","🟡 Posible riesgo",f"Acumulados {t12m} días. Evalúa reducir."
else: c12,e12,m12 = "red","🔴 En riesgo",f"{t12m} días. Riesgo de pérdida fiscal."

ca,ea,ma = ("green","🟢 Sin riesgo","") if tanio<150 else ("orange","🟡 Posible riesgo","") if tanio<183 else ("red","🔴 En riesgo","")

grafica = grafica_mensual(viajes_df)
meses_es = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']
lbl, vM, vR, vP = [], [], [], []
for k in sorted(grafica):
    y, m = k.split("-")
    lbl.append(f"{meses_es[int(m)-1]} {y}")
    vM.append(grafica[k]["M"]); vR.append(grafica[k]["R"]); vP.append(grafica[k]["P"])

ranking = {}
for est in ["M", "R", "P"]:
    # Filtrar viajes finalizados del estado actual
    fil = viajes_df[(viajes_df["estado"] == est) & (~viajes_df["en_curso"])].copy()
    
    # Normalizar nombres de países ANTES de agrupar
    fil["pais_norm"] = fil["pais"].apply(normalizar_pais)
    
    # Sumar días exactos por país normalizado
    dias_por_pais = defaultdict(int)
    for _, r in fil.iterrows():
        if r["dias"] > 0:  # Solo incluir si tiene días reales
            dias_por_pais[r["pais_norm"]] += int(r["dias"])
    
    # Debug: mostrar qué se está procesando
    total_paises = len(dias_por_pais)
    total_dias = sum(dias_por_pais.values())
    print(f"📊 Ranking {est} | Países con días>0: {total_paises} | Total días: {total_dias}")
    
    # Top 5 ordenado descendente
    top_5 = sorted(dias_por_pais.items(), key=lambda x: x[1], reverse=True)[:5]
    
    ranking[est] = [
        {"pais": p, "dias": d, "iso": PAIS_ISO.get(p.lower().strip(), "xx")} 
        for p, d in top_5
    ]
    
    # Mostrar detalles del top 5
    for p, d in top_5:
        print(f"   • {p}: {d} días")
    
    # 🔍 Alerta si EEUU no aparece pero debería
    if "Estados Unidos" in dias_por_pais and "Estados Unidos" not in [x[0] for x in top_5]:
        print(f"   ⚠️ ALERTA: Estados Unidos tiene {dias_por_pais['Estados Unidos']} días pero no está en top 5. Revisa si hay más de 5 países con más días.")
        
viajes_js = []
if not viajes_df.empty:
    for _, r in viajes_df.iterrows():
        viajes_js.append({
            "pais": r["pais"],
            "salida_str": r["salida"].strftime("%Y-%m-%d"),
            "entrada_str": r["entrada"].strftime("%Y-%m-%d"),
            "estado": r["estado"],
            "en_curso": r["en_curso"],
            "dias": r["dias"]
        })

# =============================================================================
# PLANTILLA HTML (EXPANDIDA, LEGIBLE Y 100% FUNCIONAL)
# =============================================================================
html_template = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<title>Control SUNAT - Acceso Seguro</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1"></script>
<style>
  /* ESTILOS GENERALES */
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:0;background:#f8f9fa;color:#111;line-height:1.4}
  
  /* ESTILOS DE PANTALLA DE LOGIN */
  #login-screen{position:fixed;top:0;left:0;width:100%;height:100%;background:#0f172a;z-index:9999;display:flex;align-items:center;justify-content:center;flex-direction:column;overflow:hidden}
  #bg-canvas{position:absolute;top:0;left:0;width:100%;height:100%}
  .login-box{position:relative;background:rgba(255,255,255,0.95);backdrop-filter:blur(10px);padding:35px 30px;border-radius:16px;width:90%;max-width:320px;text-align:center;box-shadow:0 20px 50px rgba(0,0,0,0.6);z-index:10000;border:1px solid rgba(255,255,255,0.2)}
  .login-box h2{margin:0 0 20px;color:#0f172a;font-size:1.5rem;font-weight:700}
  .login-box input{width:100%;padding:12px 15px;margin:8px 0 16px;border:2px solid #e2e8f0;border-radius:8px;font-size:1rem;transition:0.3s}
  .login-box input:focus{border-color:#0d6efd;outline:none}
  .login-box button{width:100%;padding:12px;background:#0d6efd;color:#fff;border:none;border-radius:8px;font-size:1rem;font-weight:600;cursor:pointer;transition:0.3s}
  .login-box button:hover{background:#0b5ed7;transform:translateY(-1px)}
  .error-msg{color:#dc3545;font-size:0.85rem;margin-top:10px;display:none}

  /* ESTILOS DE LA APP (Ocultos inicialmente) */
  #main-app{opacity:0;pointer-events:none;transition:opacity 0.6s ease-in-out;padding:12px}
  #main-app.visible{opacity:1;pointer-events:auto}
  
  /* ESTILOS EXISTENTES DEL DASHBOARD */
  .card{background:#fff;border-radius:12px;padding:16px;margin-bottom:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08)}
  .status-green{border-left:5px solid #198754;padding-left:12px}.status-orange{border-left:5px solid #fd7e14;padding-left:12px}.status-red{border-left:5px solid #dc3545;padding-left:12px}
  h1,h2,h3{margin:0 0 8px;font-weight:600}h1{font-size:1.25rem}h2{font-size:1.1rem}h3{font-size:1rem}
  .metric{font-size:1.8rem;font-weight:700;margin:4px 0 8px}
  .badge{display:inline-block;padding:4px 10px;border-radius:20px;font-size:0.8rem;font-weight:500;margin-right:6px}
  .badge-green{background:#d1e7dd;color:#0f5132}.badge-orange{background:#fff3cd;color:#664d03}.badge-red{background:#f8d7da;color:#842029}
  .badge-M{background:#3b82f6;color:#fff}.badge-R{background:#22c55e;color:#fff}.badge-P{background:#f59e0b;color:#000}
  .alert{background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:10px;margin:8px 0;font-size:0.85rem;color:#664d03}
  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:480px){.grid-2{grid-template-columns:1fr}}
  .chart-wrap{position:relative;width:100%;height:240px;margin-top:8px;min-height:240px;background:#fafafa;border-radius:8px}
  canvas{display:block;width:100%!important;height:100%!important}
  .btn{display:inline-flex;align-items:center;gap:6px;background:#0d6efd;color:#fff;border:none;border-radius:8px;padding:10px 14px;font-size:0.9rem;cursor:pointer;margin:4px 2px}
  .btn:hover{opacity:0.95}.btn:active{transform:scale(0.98)}.btn-outline{background:transparent;border:1px solid #0d6efd;color:#0d6efd}
  .form-group{margin:10px 0}.form-group label{display:block;font-size:0.85rem;margin-bottom:4px;color:#495057}
  .form-group input,.form-group select{width:100%;padding:8px;border:1px solid #ced4da;border-radius:6px;font-size:0.9rem}
  .result-box{background:#f8f9fa;border-radius:8px;padding:10px;margin-top:10px;font-size:0.9rem}
  .hidden{display:none}.loading{opacity:0.6;pointer-events:none}
  .table-responsive{overflow-x:auto;margin-top:8px}table{width:100%;border-collapse:collapse;font-size:0.9rem}
  th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #eee}th{background:#f1f3f4;font-weight:600}
  .ranking-item{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #eee}
  .ranking-flag{width:24px;height:16px;border-radius:3px;object-fit:cover;margin-right:10px;border:1px solid #eee}
  .footer{text-align:center;font-size:0.7rem;color:#6c757d;margin-top:16px}
  .legend{display:flex;gap:12px;justify-content:center;margin:8px 0;font-size:0.8rem}.legend-item{display:flex;align-items:center;gap:4px}
  .dot{width:12px;height:12px;border-radius:50%}
</style>
</head>
<body>

<!-- 🔐 PANTALLA DE LOGIN -->
<div id="login-screen">
  <canvas id="bg-canvas"></canvas>
  <div class="login-box">
    <h2>🔐 Acceso Seguro</h2>
    <input type="text" id="user" placeholder="Usuario" value="admin">
    <input type="password" id="pass" placeholder="Contraseña">
    <button onclick="intentarLogin()">INGRESAR</button>
    <p id="error-msg" class="error-msg">❌ Usuario o contraseña incorrectos</p>
  </div>
</div>

<!-- 📱 APLICACIÓN PRINCIPAL (Se muestra tras login exitoso) -->
<div id="main-app">
  <div class="card status-{{c12}}">
    <h1>🇵🇪 Estado Residencia Fiscal</h1>
    <div class="metric">{{e12}}</div>
    <p style="margin:4px 0 0">{{m12}}</p>
    <p style="margin:8px 0 0"><strong>Total últimos 12m:</strong> {{dias_12m}} / {{limite}} días</p>
    <div style="font-size:0.85rem;color:#6c757d">🔵M:{{dias_por_estado_12m.M}}d | 🟢R:{{dias_por_estado_12m.R}}d | 🟡P:{{dias_por_estado_12m.P}}d</div>
    {% if anomalias %}<div class="alert">⚠️ {{', '.join(anomalias[:3])}}{{'...' if anomalias|length>3 else ''}}</div>{% endif %}
    {% if dias_12m>=limite %}<div class="alert" style="background:#f8d7da;border-color:#dc3545;color:#842029">🔴 <strong>ALERTA:</strong> Superaste los {{limite}} días.</div>{% elif dias_12m>=150 %}<div class="alert">⚠️ <strong>Atención:</strong> {{dias_12m}}/{{limite}} días. Planifica.</div>{% endif %}
  </div>

  <div class="grid-2">
    <div class="card status-{{c12}}"><h3>📅 Últimos 12 meses</h3><div class="metric" style="font-size:1.6rem">{{dias_12m}} días</div><span class="badge badge-{{'green' if dias_12m<150 else 'orange' if dias_12m<183 else 'red'}}">{{e12}}</span></div>
    <div class="card status-{{ca}}"><h3>🗓️ Año {{anio_act}}</h3><div class="metric" style="font-size:1.6rem">{{dias_anio}} días</div><span class="badge badge-{{'green' if dias_anio<150 else 'orange' if dias_anio<183 else 'red'}}">{{ea}}</span></div>
  </div>

  <div class="card">
    <h2>📈 Evolución mensual (M/R/P)</h2>
    <div class="legend"><div class="legend-item"><span class="dot" style="background:#3b82f6"></span>M</div><div class="legend-item"><span class="dot" style="background:#22c55e"></span>R</div><div class="legend-item"><span class="dot" style="background:#f59e0b"></span>P</div></div>
    <div class="chart-wrap"><canvas id="chart"></canvas></div>
  </div>

  <div class="card"><h2>🌍 Top países</h2><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px">
    {% for est in ["M","R","P"] %}
    <div style="background:#f8f9fa;border-radius:8px;padding:8px"><strong class="badge badge-{{est}}">{{est}}</strong>
      {% if ranking[est] %}{% for r in ranking[est] %}
        <div class="ranking-item"><img src="https://flagcdn.com/w40/{{r.iso}}.png" class="ranking-flag" onerror="this.style.display='none'"><span style="font-size:0.8rem">{{r.pais|title}}</span><span style="font-size:0.75rem;background:#e9ecef;padding:2px 6px;border-radius:4px">{{r.dias}}d</span></div>
      {% endfor %}{% else %}<p style="font-size:0.75rem;color:#6c757d;margin:4px 0">-</p>{% endif %}
    </div>{% endfor %}
  </div></div>

  <div class="card"><button id="btn-p" class="btn" style="background:#f59e0b;color:#000">✈️ Proyecciones (P)</button>
    <div id="form-p" class="hidden" style="margin-top:12px"><p style="font-size:0.8rem;color:#6c757d;margin-bottom:8px">Máx 3 itinerarios. Solo estado P.</p>
      <div id="cont-p"></div><button id="btn-add-p" class="btn btn-outline" style="margin:8px 0">+ Itinerario</button><button id="btn-save-p" class="btn" style="background:#22c55e">💾 Guardar</button><div id="res-p" class="result-box hidden"></div>
    </div>
  </div>

  <div class="card"><h2>📋 Historial</h2>
    <div style="margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-outline" onclick="window.filtro('todos')" style="padding:6px 12px;font-size:0.8rem">Todos</button>
      <button class="btn" style="padding:6px 12px;font-size:0.8rem;background:#3b82f6" onclick="window.filtro('M')">M</button>
      <button class="btn" style="padding:6px 12px;font-size:0.8rem;background:#22c55e" onclick="window.filtro('R')">R</button>
      <button class="btn" style="padding:6px 12px;font-size:0.8rem;background:#f59e0b;color:#000" onclick="window.filtro('P')">P</button>
    </div>
    <div class="table-responsive"><table><thead><tr><th>Salida</th><th>Retorno</th><th>País</th><th>Días</th><th>Estado</th></tr></thead><tbody id="tb"></tbody></table></div>
  </div>

  <div class="footer">Cálculo según Art. 7° LIR. No sustituye asesoría.</div>
</div>

<script>
// ==========================================
// 🎬 ANIMACIÓN DE FONDO (VÉRTICES DINÁMICOS)
// ==========================================
const canvas = document.getElementById('bg-canvas');
const ctx = canvas.getContext('2d');
let particles = [];
const particleCount = 60; // Cantidad de puntos

function resize() { canvas.width = window.innerWidth; canvas.height = window.innerHeight; }
window.addEventListener('resize', resize);
resize();

class Particle {
  constructor() {
    this.x = Math.random() * canvas.width;
    this.y = Math.random() * canvas.height;
    this.vx = (Math.random() - 0.5) * 1.2;
    this.vy = (Math.random() - 0.5) * 1.2;
  }
  update() {
    this.x += this.vx; this.y += this.vy;
    if(this.x < 0 || this.x > canvas.width) this.vx *= -1;
    if(this.y < 0 || this.y > canvas.height) this.vy *= -1;
  }
  draw() {
    ctx.fillStyle = 'rgba(59,130,246,0.6)'; // Color de los puntos
    ctx.beginPath(); ctx.arc(this.x, this.y, 2, 0, Math.PI*2); ctx.fill();
  }
}

for(let i=0; i<particleCount; i++) particles.push(new Particle());

function animateBg() {
  ctx.clearRect(0,0,canvas.width,canvas.height);
  for(let i=0; i<particles.length; i++) {
    particles[i].update(); particles[i].draw();
    for(let j=i; j<particles.length; j++) {
      const dx = particles[i].x - particles[j].x;
      const dy = particles[i].y - particles[j].y;
      const dist = Math.sqrt(dx*dx + dy*dy);
      if(dist < 130) {
        ctx.strokeStyle = `rgba(147,197,253, ${0.5 - dist/260})`; // Líneas conectoras
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(particles[i].x, particles[i].y); ctx.lineTo(particles[j].x, particles[j].y); ctx.stroke();
      }
    }
  }
  requestAnimationFrame(animateBg);
}
animateBg();

// ==========================================
// 🔐 LÓGICA DE LOGIN
// ==========================================
function intentarLogin() {
  const u = document.getElementById('user').value;
  const p = document.getElementById('pass').value;
  
  // 🔑 VALIDACIÓN DE CREDENCIALES
  if (u === 'admin' && p === 'admin') {
    // Login exitoso
    document.getElementById('login-screen').style.opacity = '0';
    setTimeout(() => { document.getElementById('login-screen').style.display = 'none'; }, 500);
    document.getElementById('main-app').classList.add('visible');
    
    // Inicializar componentes que necesitan DOM visible
    iniciarApp(); 
  } else {
    // Login fallido
    const err = document.getElementById('error-msg');
    err.style.display = 'block';
    document.getElementById('pass').value = '';
    setTimeout(() => err.style.display = 'none', 3000);
  }
}
// Permitir Enter para entrar
document.getElementById('pass').addEventListener('keypress', function (e) { if (e.key === 'Enter') intentarLogin(); });

// ==========================================
// ⚙️ INICIALIZACIÓN DE LA APP (TRAS LOGIN)
// ==========================================
function iniciarApp() {
  try {
    const C = {{ config_json | safe }};
    console.log('✅ App Iniciada | Viajes:', C.viajes.length);

    // Helpers
    const parseFecha = s => { const p=s?.trim().split('/')||[]; return new Date(+p[2],+p[1]-1,+p[0]); };
    const regex = /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/;
    const showRes = (id, html) => { const e=document.getElementById(id); if(e){e.innerHTML=html; e.classList.remove('hidden');} };

    // 1. TABLA
    window.filtro = f => {
      const t = document.getElementById('tb'); if(!t) return;
      t.innerHTML = '';
      const d = f==='todos' ? C.viajes : C.viajes.filter(v=>v.estado===f);
      if(!d.length){ t.innerHTML='<tr><td colspan="5" style="text-align:center;padding:20px;color:#6c757d">Sin datos</td></tr>'; return; }
      d.slice().reverse().forEach(v => {
        t.innerHTML += `<tr><td>${v.salida_str.split('-').reverse().join('/')}</td><td>${v.entrada_str.split('-').reverse().join('/')}</td><td>${v.pais}</td><td style="text-align:right">${v.dias}</td><td><span class="badge badge-${v.estado}">${v.estado}</span></td></tr>`;
      });
    };
    window.filtro('todos');

    // 2. GRÁFICO DE LÍNEAS
    const ctxChart = document.getElementById('chart');
    if (ctxChart && C.lbl && C.lbl.length > 0) {
      new Chart(ctxChart, {
        type: 'line',
         {
          labels: C.lbl,
          datasets: [
            { label: 'Migraciones',  C.vM, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.1)', tension: 0.3, fill: true },
            { label: 'Registro',  C.vR, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.1)', tension: 0.3, fill: true },
            { label: 'Proyectado', data: C.vP, borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', tension: 0.3, fill: true }
          ]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(0,0,0,0.85)', titleFont: { size: 11 }, bodyFont: { size: 10 }, padding: 8 } }, scales: { x: { ticks: { maxRotation: 45, autoSkip: true, maxTicksLimit: 10, font: { size: 9 } }, grid: { display: false } }, y: { beginAtZero: true, ticks: { stepSize: 5, font: { size: 9 } }, grid: { color: 'rgba(0,0,0,0.05)' } } } }
      });
    }

    // 3. PROYECCIONES
    let pc = 1;
    window.renderProy = function() {
      const c = document.getElementById('cont-p'); if(!c) return;
      c.innerHTML = '';
      for(let i=1; i<=Math.min(pc,3); i++) {
        c.innerHTML += `<div style="border:1px solid #dee2e6;border-radius:8px;padding:10px;margin-bottom:8px;background:#fff"><div style="font-weight:600;margin-bottom:6px">Itinerario #${i}</div><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px"><div class="form-group"><label>Salida</label><input id="ps${i}" placeholder="DD/MM/YYYY"></div><div class="form-group"><label>Retorno</label><input id="pr${i}" placeholder="DD/MM/YYYY"></div></div><div class="form-group"><label>País</label><input id="pp${i}" placeholder="Ej: España"></div><button class="btn btn-outline" onclick="if(pc>1){pc--;window.renderProy()}" style="padding:6px 10px;font-size:0.8rem">🗑️ Quitar</button></div>`;
      }
    };
    document.getElementById('btn-p').onclick = () => { const f=document.getElementById('form-p'); if(f.classList.toggle('hidden')===false) window.renderProy(); };
    document.getElementById('btn-add-p').onclick = () => { if(pc<3){pc++;window.renderProy();} };
    document.getElementById('btn-save-p').onclick = async function() {
      const b=this, r='res-p'; b.classList.add('loading'); b.textContent='⏳...'; let ok=0;
      for(let i=1;i<=pc;i++){
        const s=document.getElementById(`ps${i}`)?.value, e=document.getElementById(`pr${i}`)?.value, p=document.getElementById(`pp${i}`)?.value;
        if(!s||!e||!p) continue;
        if(!regex.test(s)||!regex.test(e)){ showRes(r,'❌ Formato DD/MM/YYYY'); b.classList.remove('loading'); b.textContent='💾 Guardar'; return; }
        if(parseFecha(e)<parseFecha(s)){ showRes(r,'❌ Retorno > Salida'); b.classList.remove('loading'); b.textContent='💾 Guardar'; return; }
        try {
          await fetch(C.app_url,{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({tipo:'SALIDA',fecha:s,pais:p,estado:'P'})});
          await fetch(C.app_url,{method:'POST',headers:{'Content-Type':'text/plain'},body:JSON.stringify({tipo:'ENTRADA',fecha:e,pais:p,estado:'P'})});
          ok++;
        } catch(x){}
      }
      showRes(r, ok>0?`✅ ${ok} guardada(s). Refresca.`:'❌ Error.');
      b.classList.remove('loading'); b.textContent='💾 Guardar';
      if(ok>0){pc=1; window.renderProy();}
    };
    // Cargar proyecciones iniciales
    window.renderProy();

  } catch(err) { console.error('❌ Error JS:', err); }
}
</script>
</body>
</html>"""

# =============================================================================
# RENDERIZADO FINAL (CORREGIDO: Pasa variables explícitas a Jinja2)
# =============================================================================

# 1. JSON solo para JavaScript (frontend interactivo)
config_json = json.dumps({
    "app_url": APPS_SCRIPT_URL,
    "dias_12m": t12m, "dias_por_estado_12m": d12m,
    "dias_anio": tanio, "dias_por_estado_anio": danio,
    "anomalias": anomalias, "ranking": ranking, "viajes": viajes_js,
    "lbl": lbl, "vM": vM, "vR": vR, "vP": vP, "limite": LIMITE_SUNAT
})

# 2. Renderizado HTML: PASAMOS CADA VARIABLE que usa el template estático
html_content = Template(html_template).render(
    c12=c12, e12=e12, m12=m12,
    dias_12m=t12m, dias_por_estado_12m=d12m, limite=LIMITE_SUNAT,
    anomalias=anomalias,
    ca=ca, ea=ea, ma=ma,
    anio_act=hoy.year, dias_anio=tanio, dias_por_estado_anio=danio,
    ranking=ranking,
    config_json=config_json
)

# 3. Guardar archivo
with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
print(f"✅ index.html generado correctamente | 12m: {t12m}d | Años: {tanio}d | Viajes: {len(viajes_df)}")

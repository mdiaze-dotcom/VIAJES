# =============================================================================
# CONTROL SUNAT - VERSIÓN M/R/P + PROYECCIONES MÚLTIPLES + GRÁFICO MULTICOLOR
# =============================================================================
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

# 🔹 CONSTANTES DE NEGOCIO
LIMITE_SUNAT = 183
UMBRALES = {"verde": 150, "amarillo": 182, "rojo": 183}
ESTADOS = {"M": {"label": "Migraciones", "color": "#3b82f6"},  # Azul
           "R": {"label": "Registro", "color": "#22c55e"},      # Verde
           "P": {"label": "Proyectado", "color": "#f59e0b"}}    # Ámbar

def get_semaforo(dias):
    if dias < UMBRALES["verde"]: return "green", "🟢 Sin riesgo", "Viajes dentro del límite seguro."
    elif dias < UMBRALES["amarillo"]: return "orange", "🟡 Posible riesgo", f"Acumulados {dias} días. Evalúa reducir estancias."
    else: return "red", "🔴 En riesgo", f"{dias} días. PODRÍAS perder domicilio fiscal."

def eventos_a_viajes(df):
    """Convierte eventos ENTRADA/SALIDA en viajes, preservando ESTADO."""
    viajes, anomalias = [], []
    buffer = {}  # {pais: {salida: fecha, estado: str}}
    
    for _, row in df.iterrows():
        tipo, fecha, pais, estado = row["TIPO"], row["FECHA"], str(row["PAIS"]).strip(), row["ESTADO"]
        key = f"{pais}_{estado}"
        
        if tipo == "SALIDA":
            if key in buffer:
                anomalias.append(f"⚠️ {estado}: Salida duplicada para {pais} ({buffer[key]['salida'].strftime('%d/%m/%Y')})")
            buffer[key] = {"salida": fecha, "estado": estado, "pais": pais}
            
        elif tipo == "ENTRADA" and key in buffer:
            inicio = buffer.pop(key)
            dias = (fecha - inicio["salida"]).days - 1  # SUNAT: excluye día salida y retorno
            viajes.append({
                "salida": inicio["salida"], "entrada": fecha, "pais": inicio["pais"],
                "dias": max(0, dias), "estado": inicio["estado"], "en_curso": False
            })
        elif tipo == "ENTRADA" and key not in buffer:
            anomalias.append(f"⚠️ {estado}: Entrada sin salida previa para {pais} ({fecha.strftime('%d/%m/%Y')})")
    
    # Viajes en curso
    hoy = date.today()
    for key, inicio in buffer.items():
        dias = (hoy - inicio["salida"]).days - 1
        viajes.append({
            "salida": inicio["salida"], "entrada": hoy, "pais": inicio["pais"],
            "dias": max(0, dias), "estado": inicio["estado"], "en_curso": True
        })
        if inicio["estado"] == "P":
            anomalias.append(f"ℹ️ Proyección en curso: {inicio['pais']} desde {inicio['salida'].strftime('%d/%m/%Y')}")
    
    return pd.DataFrame(viajes), anomalias

def contar_dias_por_estado(vdf, fecha_ref, dias=365):
    """Retorna dict {estado: total_días} para ventana móvil."""
    result = {"M": 0, "R": 0, "P": 0}
    if vdf.empty: return result
    end = fecha_ref
    start = end - timedelta(days=dias-1)
    
    for _, v in vdf.iterrows():
        eff_s = v["salida"] + timedelta(days=1)
        eff_e = v["entrada"] - timedelta(days=1)
        if eff_s > eff_e: continue
        ov_s, ov_e = max(eff_s, start), min(eff_e, end)
        if ov_s <= ov_e:
            dias_contados = (ov_e - ov_s).days + 1
            result[v["estado"]] = result.get(v["estado"], 0) + dias_contados
    return result

def calcular_grafica_mensual(vdf):
    """Retorna datos para gráfico apilado: {mes: {estado: días}} desde 2000."""
    datos = defaultdict(lambda: {"M": 0, "R": 0, "P": 0})
    if vdf.empty: return datos
    
    for _, v in vdf.iterrows():
        eff_s = v["salida"] + timedelta(days=1)
        eff_e = v["entrada"] - timedelta(days=1)
        if eff_s > eff_e: continue
        cur = eff_s
        while cur <= eff_e:
            if cur.year >= 2000:
                key = f"{cur.year}-{cur.month:02d}"
                datos[key][v["estado"]] += 1
            cur += timedelta(days=1)
    return dict(sorted(datos.items()))

# 🔹 CONEXIÓN Y LECTURA
gc = gspread.service_account_from_dict(CREDENTIALS_JSON)
sheet = gc.open_by_key(SHEET_ID).sheet1
df = pd.DataFrame(sheet.get_all_records())

# Normalizar columnas
df.columns = [c.strip().upper().replace(" ", "_").replace("/", "_") for c in df.columns]
df = df.rename(columns={
    "TIPO_DE_MOVIMIENTO": "TIPO",
    "FECHA_DE_MOVIMIENTO": "FECHA",
    "PROCEDENCIA_DESTINO": "PAIS",
    "ESTADO": "ESTADO"
})

# Parseo seguro de fechas DD/MM/YYYY
def parsear_fecha_segura(val):
    if pd.isna(val): return None
    try:
        return pd.to_datetime(str(val).strip(), dayfirst=True, errors='coerce').date()
    except: return None

df["FECHA"] = df["FECHA"].apply(parsear_fecha_segura)
df = df.dropna(subset=["FECHA", "TIPO", "ESTADO"]).sort_values("FECHA").reset_index(drop=True)
df["TIPO"] = df["TIPO"].astype(str).str.strip().str.upper()
df["ESTADO"] = df["ESTADO"].astype(str).str.strip().str.upper()
df = df[df["ESTADO"].isin(["M", "R", "P"])]  # Filtrar valores inválidos

hoy = date.today()
viajes_df, anomalias = eventos_a_viajes(df)

# 🔹 CÁLCULOS DE RESUMEN
dias_por_estado_12m = contar_dias_por_estado(viajes_df, hoy, 365)
total_12m = sum(dias_por_estado_12m.values())
c12, e12, m12 = get_semaforo(total_12m)

dias_por_estado_anio = contar_dias_por_estado(viajes_df, date(hoy.year, 12, 31), 365)
total_anio = sum(dias_por_estado_anio.values())
ca, ea, ma = get_semaforo(total_anio)

# 🔹 DATOS PARA GRÁFICO APILADO
grafica_datos = calcular_grafica_mensual(viajes_df)
chart_labels, chart_M, chart_R, chart_P = [], [], [], []
meses_es = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

for mes_key in grafica_datos:
    year, month = mes_key.split("-")
    chart_labels.append(f"{meses_es[int(month)-1]} {year}")
    chart_M.append(grafica_datos[mes_key]["M"])
    chart_R.append(grafica_datos[mes_key]["R"])
    chart_P.append(grafica_datos[mes_key]["P"])

# 🔹 PREPARAR DATOS PARA JS
viajes_json = []
if not viajes_df.empty:
    for _, row in viajes_df.iterrows():
        viajes_json.append({
            "pais": row["pais"],
            "salida_str": row["salida"].strftime("%Y-%m-%d"),
            "entrada_str": row["entrada"].strftime("%Y-%m-%d"),
            "estado": row["estado"],
            "en_curso": row["en_curso"],
            "dias": row["dias"]
        })

# Ranking por estado
ranking_data = {}
for estado in ["M", "R", "P"]:
    filtrado = viajes_df[(viajes_df["estado"] == estado) & (~viajes_df["en_curso"])]
    ranking = Counter(filtrado["pais"]).most_common(5) if not filtrado.empty else []
    ranking_data[estado] = [
        {"pais": p, "dias": d, "iso": {"españa":"es","perú":"pe","usa":"us","estados unidos":"us","mexico":"mx","méxico":"mx",
            "colombia":"co","argentina":"ar","chile":"cl","ecuador":"ec","bolivia":"bo","brasil":"br",
            "italia":"it","francia":"fr","alemania":"de","canada":"ca","japon":"jp","china":"cn",
            "portugal":"pt","rusia":"ru","turquia":"tr","panama":"pa","costa rica":"cr","inglaterra":"gb",
            "reino unido":"gb","irlanda":"ie","austria":"at","suiza":"ch","holanda":"nl","paises bajos":"nl",
            "belgica":"be","noruega":"no","suecia":"se","dinamarca":"dk","finlandia":"fi","polonia":"pl",
            "grecia":"gr","islandia":"is","israel":"il","egipto":"eg","marruecos":"ma","sudafrica":"za",
            "australia":"au","nueva zelanda":"nz","corea del sur":"kr","singapur":"sg","malasia":"my",
            "indonesia":"id","filipinas":"ph","vietnam":"vn","india":"in","sri lanka":"lk","emiratos arabes":"ae","dubai":"ae"}.get(p.lower().strip(), "xx")}
        for p, d in ranking
    ]

config = {
    "app_url": APPS_SCRIPT_URL,
    "dias_12m": total_12m, "dias_por_estado_12m": dias_por_estado_12m,
    "dias_anio": total_anio, "dias_por_estado_anio": dias_por_estado_anio,
    "anio_act": hoy.year,
    "c12": c12, "e12": e12, "m12": m12,
    "ca": ca, "ea": ea, "ma": ma,
    "anomalias": anomalias,
    "ranking_data": ranking_data,
    "viajes": viajes_json,
    "chart_labels": chart_labels,
    "chart_M": chart_M,
    "chart_R": chart_R,
    "chart_P": chart_P,
    "estados_config": ESTADOS,
    "limite_sunat": LIMITE_SUNAT
}
config_json = json.dumps(config)

# =============================================================================
# PLANTILLA HTML ACTUALIZADA
# =============================================================================
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
  .badge-green{background:#d1e7dd;color:#0f5132}.badge-orange{background:#fff3cd;color:#664d03}.badge-red{background:#f8d7da;color:#842029}
  .badge-M{background:#3b82f6;color:#fff}.badge-R{background:#22c55e;color:#fff}.badge-P{background:#f59e0b;color:#000}
  .alert{background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:10px;margin:8px 0;font-size:0.85rem;color:#664d03}
  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  @media(max-width:480px){.grid-2{grid-template-columns:1fr}}
  .chart-wrapper{position:relative;width:100%;height:260px;margin-top:8px;min-height:260px;background:#fafafa;border-radius:8px}
  canvas{display:block;width:100%!important;height:100%!important}
  .btn{display:inline-flex;align-items:center;gap:6px;background:#0d6efd;color:#fff;border:none;border-radius:8px;padding:10px 14px;font-size:0.9rem;cursor:pointer;margin:4px 2px}
  .btn:hover{opacity:0.95}.btn:active{transform:scale(0.98)}.btn-outline{background:transparent;border:1px solid #0d6efd;color:#0d6efd}
  .form-group{margin:10px 0}.form-group label{display:block;font-size:0.85rem;margin-bottom:4px;color:#495057}
  .form-group input,.form-group select{width:100%;padding:8px;border:1px solid #ced4da;border-radius:6px;font-size:0.9rem}
  .result-box{background:#f8f9fa;border-radius:8px;padding:10px;margin-top:10px;font-size:0.9rem}
  .hidden{display:none}.loading{opacity:0.6;pointer-events:none}
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
  .estado-legend{display:flex;gap:12px;justify-content:center;margin:8px 0;font-size:0.8rem}
  .estado-item{display:flex;align-items:center;gap:4px}
  .estado-dot{width:12px;height:12px;border-radius:50%}
  .chart-legend{display:flex;gap:16px;justify-content:center;margin-top:8px;font-size:0.75rem}
</style>
</head>
<body>

<div class="card status-{{c12}}">
  <h1>🇵🇪 Estado Residencia Fiscal</h1>
  <div class="metric">{{e12}}</div>
  <p style="margin:4px 0 0">{{m12}}</p>
  <p style="margin:8px 0 0"><strong>Total últimos 12m:</strong> {{dias_12m}} / {{limite_sunat}} días</p>
  <div style="font-size:0.85rem;color:#6c757d;margin-top:4px">
    🔵 M: {{dias_por_estado_12m.M}}d | 🟢 R: {{dias_por_estado_12m.R}}d | 🟡 P: {{dias_por_estado_12m.P}}d
  </div>
  {% if anomalias %}<div class="alert">⚠️ {{', '.join(anomalias[:3])}}{{'...' if len(anomalias)>3 else ''}}</div>{% endif %}
  {% if dias_12m >= limite_sunat %}
  <div class="alert" style="background:#f8d7da;border-color:#dc3545;color:#842029">
    🔴 <strong>ALERTA CRÍTICA:</strong> Has superado los {{limite_sunat}} días. Podrías perder tu domicilio fiscal en Perú.
  </div>
  {% elif dias_12m >= 150 %}
  <div class="alert" style="background:#fff3cd">
    ⚠️ <strong>Atención:</strong> Te acercas al límite ({{dias_12m}}/{{limite_sunat}} días). Planifica con cuidado.
  </div>
  {% endif %}
</div>

<div class="grid-2">
  <div class="card status-{{c12}}">
    <h3>📅 Últimos 12 meses</h3>
    <div class="metric" style="font-size:1.6rem">{{dias_12m}} días</div>
    <span class="badge badge-{{'green' if dias_12m<150 else 'orange' if dias_12m<183 else 'red'}}">{{e12}}</span>
  </div>
  <div class="card status-{{ca}}">
    <h3>🗓️ Año {{anio_act}}</h3>
    <div class="metric" style="font-size:1.6rem">{{dias_anio}} días</div>
    <span class="badge badge-{{'green' if dias_anio<150 else 'orange' if dias_anio<183 else 'red'}}">{{ea}}</span>
  </div>
</div>

<div class="card">
  <h2>📈 Días fuera por mes (histórico + proyecciones)</h2>
  <div class="estado-legend">
    <div class="estado-item"><span class="estado-dot" style="background:#3b82f6"></span> M: Migraciones</div>
    <div class="estado-item"><span class="estado-dot" style="background:#22c55e"></span> R: Registro</div>
    <div class="estado-item"><span class="estado-dot" style="background:#f59e0b"></span> P: Proyectado</div>
  </div>
  <div class="chart-wrapper"><canvas id="chart"></canvas></div>
  <div class="chart-legend">
    <span>🔵 Migraciones</span> • <span>🟢 Registro manual</span> • <span>🟡 Proyecciones</span>
  </div>
</div>

<div class="card">
  <h2>🌍 Top países por estado</h2>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px">
    {% for estado in ["M","R","P"] %}
    <div style="background:#f8f9fa;border-radius:8px;padding:8px">
      <strong class="badge badge-{{estado}}">{{estados_config[estado].label}}</strong>
      {% if ranking_data[estado] %}
        {% for r in ranking_data[estado] %}
        <div class="ranking-item" style="padding:4px 0;border-bottom:none">
          <img src="https://flagcdn.com/w40/{{r.iso}}.png" class="ranking-flag" alt="{{r.pais}}" onerror="this.style.display='none'">
          <span style="font-size:0.8rem">{{r.pais|title}}</span>
          <span style="font-size:0.75rem;background:#e9ecef;padding:2px 6px;border-radius:4px">{{r.dias}}d</span>
        </div>
        {% endfor %}
      {% else %}
        <p style="font-size:0.75rem;color:#6c757d;margin:4px 0">Sin datos</p>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</div>

<!-- FORMULARIO: AGREGAR/EDITAR PROYECCIONES (SOLO ESTADO P) -->
<div class="card">
  <button id="btn-proj" class="btn" style="background:#f59e0b;color:#000">✈️ Gestionar Proyecciones (P)</button>
  <div id="form-proj" class="hidden" style="margin-top:12px">
    <p style="font-size:0.8rem;color:#6c757d;margin-bottom:8px">
      💡 Solo puedes editar proyecciones (estado P). Hasta 3 itinerarios.
    </p>
    <div id="projections-container">
      <!-- Se generan 3 formularios idénticos -->
    </div>
    <button id="btn-add-projection" class="btn btn-outline" style="margin:8px 0">+ Agregar otro itinerario</button>
    <button id="btn-save-proj" class="btn" style="background:#22c55e">💾 Guardar proyecciones</button>
    <div id="res-proj" class="result-box hidden"></div>
  </div>
</div>

<!-- TABLA DE VIAJES CON FILTRO POR ESTADO -->
<div class="card">
  <h2>📋 Historial de viajes</h2>
  <div style="margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn btn-outline" onclick="filtrarTabla('todos')" style="padding:6px 12px;font-size:0.8rem">Todos</button>
    <button class="btn" style="padding:6px 12px;font-size:0.8rem;background:#3b82f6" onclick="filtrarTabla('M')">M</button>
    <button class="btn" style="padding:6px 12px;font-size:0.8rem;background:#22c55e" onclick="filtrarTabla('R')">R</button>
    <button class="btn" style="padding:6px 12px;font-size:0.8rem;background:#f59e0b;color:#000" onclick="filtrarTabla('P')">P</button>
  </div>
  <div class="table-responsive">
    <table id="tabla-viajes">
      <thead>
        <tr>
          <th>Salida</th><th>Retorno</th><th>País</th><th>Días</th><th>Estado</th><th>Acciones</th>
        </tr>
      </thead>
      <tbody id="tabla-body">
        <!-- Se llena con JS -->
      </tbody>
    </table>
  </div>
</div>

<div class="footer">Cálculo según Art. 7° LIR. No sustituye asesoría tributaria.</div>

<script id="app-config" type="application/json">{{ config_json | safe }}</script>
<script>
document.addEventListener('DOMContentLoaded', function() {
  try {
    const cfg = JSON.parse(document.getElementById('app-config').textContent);
    console.log('✅ Config OK | Viajes:', cfg.viajes.length, '| Chart:', cfg.chart_labels.length);

    // Utilidades
    const parseFecha = s => { const p=s.trim().split('/'); return new Date(+p[2], +p[1]-1, +p[0]); };
    const diasEntre = (f1,f2) => Math.max(0, Math.floor((parseFecha(f2)-parseFecha(f1)-864e5)/864e5));
    const formatoMes = ym => { const [y,m]=ym.split('-'); return ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][+m-1]+' '+y; };
    const toggle = id => document.getElementById(id).classList.toggle('hidden');
    const showRes = (id, html) => { const el=document.getElementById(id); el.innerHTML=html; el.classList.remove('hidden'); };
    const regex = /^\\d{1,2}\\/\\d{1,2}\\/\\d{4}$/;
    const estadosCfg = cfg.estados_config;

    // Toggle formulario de proyecciones
    document.getElementById('btn-proj').onclick = () => {
      toggle('form-proj');
      if (!document.getElementById('form-proj').classList.contains('hidden')) {
        renderProjectionForms();
        renderTablaViajes('todos');
      }
    };

    // Renderizar formularios de proyección (hasta 3)
    let projectionCount = 1;
    function renderProjectionForms() {
      const container = document.getElementById('projections-container');
      container.innerHTML = '';
      for (let i = 1; i <= Math.min(projectionCount, 3); i++) {
        container.innerHTML += `
          <div style="border:1px solid #dee2e6;border-radius:8px;padding:10px;margin-bottom:8px;background:#fff">
            <div style="font-weight:600;margin-bottom:6px">Itinerario #${i}</div>
            <div class="range-grid">
              <div class="form-group"><label>Salida</label><input type="text" id="proj-s-${i}" placeholder="DD/MM/YYYY"></div>
              <div class="form-group"><label>Retorno</label><input type="text" id="proj-r-${i}" placeholder="DD/MM/YYYY"></div>
            </div>
            <div class="form-group"><label>País</label><input type="text" id="proj-pais-${i}" placeholder="Ej: España"></div>
            <button class="btn btn-outline" onclick="removeProjection(${i})" style="padding:6px 10px;font-size:0.8rem">🗑️ Eliminar</button>
          </div>`;
      }
    }
    window.removeProjection = function(idx) {
      if (projectionCount > 1) { projectionCount--; renderProjectionForms(); }
    };
    document.getElementById('btn-add-projection').onclick = function() {
      if (projectionCount < 3) { projectionCount++; renderProjectionForms(); }
      else { alert('Máximo 3 proyecciones permitidas'); }
    };

    // Guardar proyecciones (solo estado P)
    document.getElementById('btn-save-proj').onclick = async function() {
      const btn = this;
      const res = 'res-proj';
      btn.classList.add('loading'); btn.textContent = '⏳...';
      
      let successCount = 0;
      for (let i = 1; i <= projectionCount; i++) {
        const s = document.getElementById(`proj-s-${i}`)?.value;
        const r = document.getElementById(`proj-r-${i}`)?.value;
        const pais = document.getElementById(`proj-pais-${i}`)?.value;
        if (!s || !r || !pais) continue;
        if (!regex.test(s)||!regex.test(r)) { showRes(res, '❌ Usa formato DD/MM/YYYY'); btn.classList.remove('loading'); btn.textContent = '💾 Guardar proyecciones'; return; }
        if (parseFecha(r) < parseFecha(s)) { showRes(res, '❌ Retorno debe ser posterior'); btn.classList.remove('loading'); btn.textContent = '💾 Guardar proyecciones'; return; }
        
        try {
          // Guardar SALIDA proyectada
          await fetch(cfg.app_url, { method: 'POST', mode: 'cors', headers: {'Content-Type':'text/plain'}, body: JSON.stringify({tipo:'SALIDA', fecha:s, pais:pais, estado:'P'}) });
          // Guardar ENTRADA proyectada
          await fetch(cfg.app_url, { method: 'POST', mode: 'cors', headers: {'Content-Type':'text/plain'}, body: JSON.stringify({tipo:'ENTRADA', fecha:r, pais:pais, estado:'P'}) });
          successCount++;
        } catch(e) { /* continuar con el siguiente */ }
      }
      
      if (successCount > 0) {
        showRes(res, `✅ ${successCount} proyección(es) guardada(s). Refresca la página para ver actualizaciones.`);
        // Resetear formulario
        projectionCount = 1; renderProjectionForms();
        document.getElementById('proj-s-1').value = ''; document.getElementById('proj-r-1').value = ''; document.getElementById('proj-pais-1').value = '';
      } else {
        showRes(res, '❌ No se pudieron guardar las proyecciones. Verifica la conexión.');
      }
      btn.classList.remove('loading'); btn.textContent = '💾 Guardar proyecciones';
    };

    // Renderizar tabla de viajes con filtro
    window.filtrarTabla = function(estadoFilter) {
      renderTablaViajes(estadoFilter);
    };
    
    function renderTablaViajes(filter) {
      const tbody = document.getElementById('tabla-body');
      tbody.innerHTML = '';
      const viajesFiltrados = filter === 'todos' ? cfg.viajes : cfg.viajes.filter(v => v.estado === filter);
      
      if (viajesFiltrados.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#6c757d;padding:20px">Sin viajes para mostrar</td></tr>';
        return;
      }
      
      viajesFiltrados.slice().reverse().forEach((v, idx) => {
        const acciones = v.estado === 'P' ? 
          `<button class="btn btn-outline" style="padding:4px 8px;font-size:0.75rem" onclick="editarProyeccion('${v.salida_str}','${v.entrada_str}','${v.pais}')">✏️</button>` : 
          `<span style="font-size:0.75rem;color:#6c757d">Solo lectura</span>`;
        
        tbody.innerHTML += `
          <tr>
            <td>${v.salida_str.split('-').reverse().join('/')}</td>
            <td>${v.entrada_str.split('-').reverse().join('/')}</td>
            <td>${v.pais}</td>
            <td style="text-align:right">${v.dias}</td>
            <td><span class="badge badge-${v.estado}">${v.estado}</span></td>
            <td>${acciones}</td>
          </tr>`;
      });
    }
    
    window.editarProyeccion = function(salida, entrada, pais) {
      // Pre-llenar el primer formulario de proyección
      document.getElementById('proj-s-1').value = salida.split('-').reverse().join('/');
      document.getElementById('proj-r-1').value = entrada.split('-').reverse().join('/');
      document.getElementById('proj-pais-1').value = pais;
      toggle('form-proj');
      alert('ℹ️ Para editar una proyección: elimina la existente y agrega la nueva con los datos corregidos.');
    };

    // ✅ GRÁFICO APILADO MULTICOLOR
    const ctx = document.getElementById('chart');
    if (!cfg.chart_labels || cfg.chart_labels.length === 0) {
      ctx.parentElement.innerHTML = '<p style="text-align:center;color:#6c757d;padding:40px">Sin datos históricos para graficar.</p>';
    } else {
      new Chart(ctx, {
        type: 'bar',
         {
          labels: cfg.chart_labels,
          datasets: [
            { label: 'Migraciones',  cfg.chart_M, backgroundColor: '#3b82f6', stack: 'Stack 0' },
            { label: 'Registro',  cfg.chart_R, backgroundColor: '#22c55e', stack: 'Stack 0' },
            { label: 'Proyectado',  cfg.chart_P, backgroundColor: '#f59e0b', stack: 'Stack 0' }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 0 },
          plugins: { 
            legend: { display: false }, 
            tooltip: { 
              enabled: true, 
              backgroundColor: 'rgba(0,0,0,0.85)', 
              titleFont: { size: 11 }, 
              bodyFont: { size: 10 }, 
              padding: 6,
              callbacks: {
                label: function(context) {
                  const label = context.dataset.label || '';
                  const value = context.parsed.y || 0;
                  return `${label}: ${value} días`;
                }
              }
            }
          },
          scales: {
            x: { 
              stacked: true,
              ticks: { maxRotation: 45, minRotation: 45, autoSkip: true, maxTicksLimit: 12, font: { size: 9 } }, 
              grid: { display: false }
            },
            y: { 
              stacked: true,
              beginAtZero: true, 
              ticks: { stepSize: 10, font: { size: 9 } }, 
              grid: { color: 'rgba(0,0,0,0.05)' },
              title: { display: true, text: 'Días fuera', font: { size: 10 } }
            }
          }
        }
      });
      console.log('📈 Gráfico apilado renderizado');
    }
  } catch(err) {
    console.error('❌ Error JS:', err);
  }
});
</script>
</body>
</html>"""

# =============================================================================
# GENERAR ARCHIVO FINAL
# =============================================================================
html_content = Template(html_template).render(
    dias_12m=total_12m, dias_por_estado_12m=dias_por_estado_12m,
    dias_anio=total_anio, dias_por_estado_anio=dias_por_estado_anio,
    anio_act=hoy.year,
    c12=c12, e12=e12, m12=m12,
    ca=ca, ea=ea, ma=ma,
    anomalias=anomalias,
    ranking_data=ranking_data,
    viajes=viajes_json,
    chart_labels=chart_labels,
    chart_M=chart_M,
    chart_R=chart_R,
    chart_P=chart_P,
    estados_config=ESTADOS,
    limite_sunat=LIMITE_SUNAT,
    config_json=config_json
)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
print(f"✅ index.html generado | Total días (12m): {total_12m} | Proyecciones: {dias_por_estado_12m['P']}")

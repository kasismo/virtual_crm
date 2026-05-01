import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import bcrypt
import time
import io
import json
import google.generativeai as genai
import plotly.express as px
import chardet
import csv

# ==========================================
# --- 1. CONFIGURACIÓN DE PÁGINA Y ESTADOS ---
# ==========================================
st.set_page_config(page_title="SaaS Analytics Pro", page_icon="🏢", layout="wide")

if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
if 'empresa_id' not in st.session_state:
    st.session_state['empresa_id'] = None
if 'nombre_empresa' not in st.session_state:
    st.session_state['nombre_empresa'] = None
if 'df_ventas' not in st.session_state:
    st.session_state['df_ventas'] = pd.DataFrame()
if 'mapa_ia' not in st.session_state:
    st.session_state['mapa_ia'] = {}

# ==========================================
# --- 2. MÓDULOS DE BASE DE DATOS ---
# ==========================================
def verificar_login(email, password_plana):
    try:
        motor_auth = create_engine(st.secrets["DB_AUTH_URI"])
        query = text("""
            SELECT u.password_hash, u.empresa_id, e.nombre_empresa 
            FROM usuarios u
            JOIN empresas e ON u.empresa_id = e.id
            WHERE TRIM(u.email) = :email
        """)
        with motor_auth.connect() as conexion:
            resultado = conexion.execute(query, {"email": email.strip()}).fetchone()
            
        if resultado:
            hash_bd = resultado[0].strip().encode('utf-8') 
            pass_bytes = password_plana.strip().encode('utf-8')
            if bcrypt.checkpw(pass_bytes, hash_bd):
                return True, resultado[1], resultado[2] 
        return False, None, None
    except Exception as e:
        st.error(f"Error de autenticación: {e}")
        return False, None, None

def guardar_mapeo_sql(empresa_id, mapping_json):
    """Guarda el ADN del archivo en la base de datos para no gastar IA."""
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        mapping_str = json.dumps(mapping_json)
        query = text("UPDATE empresas SET ultimo_mapeo = :mapping WHERE id = :id")
        with motor.connect() as conexion:
            conexion.execute(query, {"mapping": mapping_str, "id": empresa_id})
            conexion.commit()
    except Exception as e:
        pass # Fallo silencioso, no rompemos la app si no puede guardar

def recuperar_mapeo_sql(empresa_id):
    """Busca si esta empresa ya tiene un mapeo guardado."""
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        query = text("SELECT ultimo_mapeo FROM empresas WHERE id = :id")
        with motor.connect() as conexion:
            resultado = conexion.execute(query, {"id": empresa_id}).fetchone()
        if resultado and resultado[0]:
            return json.loads(resultado[0])
        return {}
    except:
        return {}

# ==========================================
# --- 3. MÓDULOS DE INGESTIÓN (EL REPARADOR) ---
# ==========================================
def leer_archivo_seguro(uploaded_file):
    cabecera_bytes = uploaded_file.read(4)
    uploaded_file.seek(0)
    
    if cabecera_bytes.startswith(b'PK') or cabecera_bytes.startswith(b'\xd0\xcf') or uploaded_file.name.endswith(('.xlsx', '.xls')):
        return pd.read_excel(uploaded_file)
    else:
        muestra_bytes = uploaded_file.read(50000)
        uploaded_file.seek(0)
        resultado = chardet.detect(muestra_bytes)
        cod = resultado['encoding'] or 'utf-8'
        if cod.lower() == 'ascii': cod = 'utf-8'
            
        muestra_texto = muestra_bytes.decode(cod, errors='replace')
        try: delim = csv.Sniffer().sniff(muestra_texto).delimiter
        except: delim = ',' 
            
        return pd.read_csv(uploaded_file, encoding=cod, sep=delim, on_bad_lines='skip', engine='python')

# ==========================================
# --- 4. EL CEREBRO IA ---
# ==========================================
@st.cache_data
def mapear_columnas(lista_de_columnas):
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        modelo = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"""
        Mapea qué columna sirve para cada métrica: {lista_de_columnas}
        - "fecha": (día, mes, date).
        - "valor": (sales, ventas, ingresos, total).
        - "gastos": (costos, discount).
        - "ganancia": (profit, margen, neto).
        - "categoria": (category, state, ciudad, producto).
        - "filtro": (region, pais).
        Responde ÚNICAMENTE con la estructura JSON. Ejemplo:
        {{"fecha": "Order Date", "valor": "Sales", "gastos": null, "ganancia": "Profit", "categoria": "State", "filtro": "Region"}}
        """
        respuesta = modelo.generate_content(prompt)
        txt = respuesta.text.replace('```json', '').replace('```', '').strip()
        return json.loads(txt)
    except Exception as e:
        return {}

# ==========================================
#        INTERFAZ DE USUARIO (FRONTEND)
# ==========================================

if not st.session_state['autenticado']:
    st.markdown("<h1 style='text-align: center;'>🔐 Acceso a Plataforma SaaS</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            email_input = st.text_input("Correo Corporativo")
            pass_input = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Ingresar al Panel", use_container_width=True):
                with st.spinner("Desencriptando credenciales..."):
                    time.sleep(1) 
                    exito, c_id, c_name = verificar_login(email_input, pass_input)
                    if exito:
                        st.session_state['autenticado'] = True
                        st.session_state['empresa_id'] = c_id
                        st.session_state['nombre_empresa'] = c_name
                        st.rerun() 
                    else:
                        st.error("❌ Credenciales incorrectas o usuario no encontrado.")

else:
    # SIDEBAR CORPORATIVO
    st.sidebar.title(f"🏢 {st.session_state['nombre_empresa']}")
    st.sidebar.caption(f"ID de Cliente: {st.session_state['empresa_id']}")
    st.sidebar.divider()
    
    # Botón mágico para resetear la memoria de la IA si el cliente sube un Excel con otras columnas
    if st.sidebar.button("🔄 Re-escanear estructura con IA"):
        st.session_state['mapa_ia'] = {}
        st.sidebar.success("Memoria borrada. El próximo archivo se analizará desde cero.")
    
    st.sidebar.divider()
    
    if st.sidebar.button("Cerrar Sesión", type="primary"):
        st.session_state['autenticado'] = False
        st.session_state['df_ventas'] = pd.DataFrame() 
        st.session_state['mapa_ia'] = {}
        st.rerun()

    st.title("💸 Panel de Inteligencia de Negocios")
    st.markdown("Sube tu archivo. El sistema lo auditará y estructurará usando tu memoria persistente.")
    
    # --- 1. INGESTIÓN Y AUDITORÍA ---
    archivo = st.file_uploader("Formato soportado: CSV o Excel.", type=['csv', 'xlsx', 'xls'])
    
    if archivo:
        with st.spinner("🏥 Pasando archivo por el quirófano de datos..."):
            df_crudo = leer_archivo_seguro(archivo)
            
            if not df_crudo.empty:
                df_crudo = df_crudo.replace(["", " "], pd.NA)
                total_filas_orig = len(df_crudo)
                
                df_limpio = df_crudo.drop_duplicates().reset_index(drop=True)
                duplicados_eliminados = total_filas_orig - len(df_limpio)
                
                if 'ID' in df_limpio.columns: df_limpio = df_limpio.drop('ID', axis=1)
                df_limpio.insert(0, 'ID', range(1, len(df_limpio) + 1))
                
                incomplete_rows_mask = df_limpio.isnull().any(axis=1)
                indices_incompletas = df_limpio[incomplete_rows_mask].index 
                num_incompletas = len(indices_incompletas)
                
                df_limpio = df_limpio.fillna("NO_DATO")
                st.session_state["df_ventas"] = df_limpio
                
                # --- MAGIA DE PERSISTENCIA ---
                # Si no tenemos un mapa en la sesión, lo buscamos en SQL. 
                if not st.session_state['mapa_ia']:
                    mapa_guardado = recuperar_mapeo_sql(st.session_state['empresa_id'])
                    
                    if mapa_guardado:
                        st.session_state['mapa_ia'] = mapa_guardado
                        st.toast("⚡ ADN de datos cargado desde la base de datos (Ahorro de IA).")
                    else:
                        st.toast("🧠 Analizando archivo con Inteligencia Artificial por primera vez...")
                        nuevo_mapa = mapear_columnas(list(df_limpio.columns))
                        st.session_state['mapa_ia'] = nuevo_mapa
                        guardar_mapeo_sql(st.session_state['empresa_id'], nuevo_mapa)
                
                st.success("✅ Archivo auditado y cargado en el sistema.")
                
                # PANEL DE AUDITORÍA
                st.subheader("📊 Resultados de la Auditoría")
                col1, col2, col3 = st.columns(3)
                col1.metric("Filas Originales", f"{total_filas_orig:,}")
                col2.metric("Duplicados Eliminados", f"{duplicados_eliminados:,}", delta_color="inverse")
                col3.metric("Datos Faltantes", f"{num_incompletas:,}")
                
                if num_incompletas > 0:
                    st.warning(f"⚠️ Se detectaron {num_incompletas:,} filas con datos incompletos (resaltadas en amarillo).")
                
                # DESCARGA EXCEL CON COLORES
                def resaltar_amarillo(row):
                    if row.name in indices_incompletas: return ['background-color: #ffd966; color: black'] * len(row)
                    return [''] * len(row)
                
                col_btn, _ = st.columns([1, 2])
                with col_btn:
                    buffer = io.BytesIO()
                    df_limpio.style.apply(resaltar_amarillo, axis=1).to_excel(buffer, index=False, engine='openpyxl')
                    excel_data = buffer.getvalue()
                    st.download_button("📥 Descargar Base Auditada (Excel)", data=excel_data, file_name="datos_auditados.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
                
                st.write("**Vista Previa:**")
                st.dataframe(df_limpio.head(100).style.apply(resaltar_amarillo, axis=1), use_container_width=True)

    # --- 2. PROCESAMIENTO GRÁFICO ---
    df_actual = st.session_state["df_ventas"]
    mapa_ia = st.session_state["mapa_ia"]
    
    if not df_actual.empty and mapa_ia:
        st.divider()
        col_valor = mapa_ia.get('valor')
        col_cat = mapa_ia.get('categoria')
        col_fecha = mapa_ia.get('fecha')
        col_filtro = mapa_ia.get('filtro')
        
        if col_filtro and col_filtro in df_actual.columns:
            seleccion = st.sidebar.selectbox(f"📍 Filtro: {col_filtro}", ["Todos"] + list(df_actual[col_filtro].unique()))
            if seleccion != "Todos": df_actual = df_actual[df_actual[col_filtro] == seleccion]

        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader("📈 Tendencias")
            if col_fecha and col_valor in df_actual.columns:
                df_tendencia = df_actual.copy()
                df_tendencia[col_fecha] = pd.to_datetime(df_tendencia[col_fecha], errors='coerce')
                df_tendencia[col_valor] = pd.to_numeric(df_tendencia[col_valor], errors='coerce').fillna(0)
                tendencia = df_tendencia.groupby(df_tendencia[col_fecha].dt.to_period("M").astype(str))[col_valor].sum().reset_index()
                
                tipo_g = st.radio("Formato:", ["Líneas", "Área", "Barras"], horizontal=True, key="r1")
                if tipo_g == "Líneas": fig = px.line(tendencia, x=col_fecha, y=col_valor)
                elif tipo_g == "Área": fig = px.area(tendencia, x=col_fecha, y=col_valor)
                else: fig = px.bar(tendencia, x=col_fecha, y=col_valor)
                st.plotly_chart(fig, use_container_width=True)
                
        with c2:
            st.subheader(f"🗺️ Desglose por {col_cat if col_cat else 'Categoría'}")
            if col_cat and col_valor in df_actual.columns:
                df_proporcion = df_actual.copy()
                df_proporcion[col_valor] = pd.to_numeric(df_proporcion[col_valor], errors='coerce').fillna(0)
                df_proporcion[col_cat] = df_proporcion[col_cat].astype(str).str.strip().str.upper() # FIX MAYÚSCULAS
                
                agrupado = df_proporcion.groupby(col_cat)[col_valor].sum().reset_index()
                tipo_c = st.selectbox("Formato:", ["Donut (Profesional)", "Pastel (Clásico)", "Barras"], key="s1")
                
                # FIX NÚMEROS NEGATIVOS
                if tipo_c in ["Donut (Profesional)", "Pastel (Clásico)"]:
                    agrupado_positivo = agrupado[agrupado[col_valor] > 0]
                    if agrupado_positivo.empty:
                        st.warning("⚠️ Valores negativos. Usa 'Barras'.")
                    else:
                        if tipo_c == "Donut (Profesional)": fig2 = px.pie(agrupado_positivo, names=col_cat, values=col_valor, hole=0.5)
                        else: fig2 = px.pie(agrupado_positivo, names=col_cat, values=col_valor)
                        st.plotly_chart(fig2, use_container_width=True)
                else: 
                    fig2 = px.bar(agrupado, x=col_cat, y=col_valor, color=col_cat)
                    st.plotly_chart(fig2, use_container_width=True)

import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import bcrypt
import time
import io
import json
import google.generativeai as genai
import plotly.express as px
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import chardet
import csv

# ==========================================
# --- 1. CONFIGURACIÓN DE PÁGINA Y ESTADOS ---
# ==========================================
st.set_page_config(page_title="SaaS Analytics Pro", page_icon="🏢", layout="wide")

# Inicializamos la "memoria" global de la aplicación
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
if 'empresa_id' not in st.session_state:
    st.session_state['empresa_id'] = None
if 'nombre_empresa' not in st.session_state:
    st.session_state['nombre_empresa'] = None
if 'df_ventas' not in st.session_state:
    st.session_state['df_ventas'] = pd.DataFrame()

# ==========================================
# --- 2. MÓDULO DE SEGURIDAD (LA BÓVEDA) ---
# ==========================================
def verificar_login(email, password_plana):
    """Se conecta a PostgreSQL/Supabase para validar usuarios de tu SaaS."""
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
        st.error(f"Error de conexión con el servidor de autenticación: {e}")
        return False, None, None

# ==========================================
# --- 3. MÓDULOS DE INGESTIÓN Y LIMPIEZA ---
# ==========================================
def reparar_archivo_local(uploaded_file):
    """El Reparador Integrado: Cura archivos corruptos usando análisis de bytes y sniffer."""
    # Leemos la firma real del archivo
    cabecera_bytes = uploaded_file.read(4)
    uploaded_file.seek(0)
    
    # 1. Tratamiento para Excel (Moderno o Antiguo)
    if cabecera_bytes.startswith(b'PK') or cabecera_bytes.startswith(b'\xd0\xcf') or uploaded_file.name.endswith(('.xlsx', '.xls')):
        return pd.read_excel(uploaded_file)
        
    # 2. Tratamiento Forense para CSV / Texto
    else:
        # Extraemos una muestra para analizar el ADN
        muestra_bytes = uploaded_file.read(50000)
        uploaded_file.seek(0)
        
        # Detectamos la codificación real
        resultado = chardet.detect(muestra_bytes)
        cod = resultado['encoding'] or 'utf-8'
        if cod.lower() == 'ascii': 
            cod = 'utf-8'
            
        # Olfateamos el delimitador correcto
        muestra_texto = muestra_bytes.decode(cod, errors='replace')
        try:
            delim = csv.Sniffer().sniff(muestra_texto).delimiter
        except:
            delim = ',' # Salvavidas por defecto
            
        # Cirugía: Leemos el archivo saltando las filas destructivas
        df = pd.read_csv(
            uploaded_file, 
            encoding=cod, 
            sep=delim, 
            on_bad_lines='skip',
            engine='python'
        )
        return df

# ==========================================
# --- 4. EL CEREBRO IA ---
# ==========================================
@st.cache_data
def mapear_columnas(lista_de_columnas):
    """Usa Gemini para entender semánticamente cualquier archivo."""
    try:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
        modelo = genai.GenerativeModel('gemini-2.5-flash')
        
        prompt = f"""
        Eres un analista de datos. Analiza esta lista de columnas: {lista_de_columnas}
        Mapea qué columna original sirve para cada métrica.
        - "fecha": (día, mes, date, fecha, timestamp, order date).
        - "valor": (sales, ventas, ingresos, facturacion, total).
        - "gastos": (costos, gastos, discount, egresos).
        - "ganancia": (profit, ganancia, margen, neto).
        - "categoria": (category, state, ciudad, segmento, producto).
        - "filtro": (region, pais, continente).
        
        Responde ÚNICAMENTE con la estructura JSON. No agregues comillas markdown (```json).
        Ejemplo exacto de tu respuesta:
        {{"fecha": "Order Date", "valor": "Sales", "gastos": null, "ganancia": "Profit", "categoria": "State", "filtro": "Region"}}
        """
        
        respuesta = modelo.generate_content(prompt)
        txt = respuesta.text.replace('```json', '').replace('```', '').strip()
        return json.loads(txt)
    except Exception as e:
        st.error(f"Error en la IA: {e}")
        return {}


# ==========================================
#        INTERFAZ DE USUARIO (FRONTEND)
# ==========================================

# --- PANTALLA DE LOGIN ---
if not st.session_state['autenticado']:
    st.markdown("<h1 style='text-align: center;'>🔐 Acceso a Plataforma SaaS</h1>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            email_input = st.text_input("Correo Corporativo")
            pass_input = st.text_input("Contraseña", type="password")
            submit = st.form_submit_button("Ingresar al Panel", use_container_width=True)
            
            if submit:
                with st.spinner("Desencriptando credenciales..."):
                    time.sleep(1) # Efecto visual de seguridad
                    
                    exito, c_id, c_name = verificar_login(email_input, pass_input)
                    
                    if exito:
                        st.session_state['autenticado'] = True
                        st.session_state['empresa_id'] = c_id
                        st.session_state['nombre_empresa'] = c_name
                        st.rerun() 
                    else:
                        st.error("❌ Credenciales incorrectas o usuario no encontrado.")

# --- PANTALLA PRINCIPAL (PROTEGIDA) ---
else:
    # --- Sidebar Corporativo ---
    st.sidebar.title(f"🏢 {st.session_state['nombre_empresa']}")
    st.sidebar.caption(f"ID de Cliente: {st.session_state['empresa_id']}")
    st.sidebar.divider()
    
    if st.sidebar.button("Cerrar Sesión", type="primary"):
        st.session_state['autenticado'] = False
        st.session_state['df_ventas'] = pd.DataFrame() # Limpiamos memoria de datos
        if "google_creds" in st.session_state:
            del st.session_state["google_creds"] # Borramos token de Google
        st.rerun()

    st.title("💸 Panel de Inteligencia de Negocios")
    st.markdown("Selecciona el origen de tus datos para comenzar el análisis.")
    
    # --- 1. ENRUTADOR DE INGESTIÓN ---
    fuente_datos = st.radio("Origen de datos:", ["Subir Archivo Local", "Sincronizar Google Drive"], horizontal=True)
    
    # Configuración base para el flujo OAuth de Google
    oauth_config = {
        "web": {
            "client_id": st.secrets["google_oauth"]["client_id"],
            "project_id": st.secrets["google_oauth"]["project_id"],
            "auth_uri": st.secrets["google_oauth"]["auth_uri"],
            "token_uri": st.secrets["google_oauth"]["token_uri"],
            "auth_provider_x509_cert_url": st.secrets["google_oauth"]["auth_provider_x509_cert_url"],
            "client_secret": st.secrets["google_oauth"]["client_secret"],
            "redirect_uris": st.secrets["google_oauth"]["redirect_uris"]
        }
    }
    redirect_uri = st.secrets["google_oauth"]["redirect_uris"][0]
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly', 'https://www.googleapis.com/auth/drive.readonly']
    flow = Flow.from_client_config(oauth_config, scopes=SCOPES, redirect_uri=redirect_uri)

   # 2. CAPTURA DEL CÓDIGO DE GOOGLE (Gestión de Pestaña Única)
    if "code" in st.query_params:
        # Candado: Solo intentamos canjear si NO tenemos las credenciales ya guardadas
        if "google_creds" not in st.session_state:
            codigo = st.query_params["code"]
            try:
                flow.fetch_token(code=codigo)
                st.session_state["google_creds"] = flow.credentials
                st.session_state["login_msg"] = "exito"
            except Exception as e:
                st.session_state["login_msg"] = "error"
                
        # Limpieza automática (El usuario ya no tiene que tocar la URL)
        st.query_params.clear()
        st.rerun()

    # Mostrar el resultado (Fuera del bloque code)
    if "login_msg" in st.session_state:
        if st.session_state["login_msg"] == "exito":
            st.success("✅ Cuenta de Google vinculada correctamente.")
        elif st.session_state["login_msg"] == "error":
            st.error("⚠️ Enlace expirado. Por favor, intenta conectar de nuevo.")
        del st.session_state["login_msg"]

    # Lógica según la fuente elegida
    if fuente_datos == "Subir Archivo Local":
        archivo = st.file_uploader("Sube tu CSV o Excel", type=['csv', 'xlsx'])
        if archivo:
            with st.spinner("Reparando archivo en memoria..."):
                df_curado = reparar_archivo_local(archivo)
                if not df_curado.empty:
                    st.session_state["df_ventas"] = df_curado 
                    st.success("✅ Archivo curado y cargado en el sistema.")
                
    elif fuente_datos == "Sincronizar Google Drive":
        if "google_creds" not in st.session_state:
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            
            st.info("Para analizar archivos de la nube, primero vincula tu cuenta de Google.")
            
            # Advertencia de UX para el usuario
            st.warning("⚠️ **Nota:** El inicio de sesión seguro se abrirá en una nueva pestaña. Cuando termines, continúa trabajando en la nueva pestaña.")
            
            # El botón nativo de Streamlit que abre la pestaña de forma segura
            st.link_button("🔐 Iniciar sesión con Google", auth_url, use_container_width=True)
            
        else:
            st.success("✅ Cuenta de Google vinculada.")
            nombre_sheet = st.text_input("Nombre del archivo en tu Google Workspace:")
            # ... (el resto del código sigue igual)
            
            if st.button("Conectar y Limpiar Nube") and nombre_sheet:
                with st.spinner("Accediendo a tu Drive y auditando datos..."):
                    try:
                        creds = st.session_state["google_creds"]
                        servicio_drive = build('drive', 'v3', credentials=creds)
                        
                        # Búsqueda
                        q = f"name contains '{nombre_sheet}' and mimeType='application/vnd.google-apps.spreadsheet'"
                        res = servicio_drive.files().list(q=q, fields="files(id, name)").execute()
                        archivos = res.get('files', [])
                        
                        if archivos:
                            file_id = archivos[0]['id']
                            servicio_sheets = build('sheets', 'v4', credentials=creds)
                            res_hoja = servicio_sheets.spreadsheets().values().get(
                                spreadsheetId=file_id, range='A1:Z2000'
                            ).execute()
                            valores = res_hoja.get('values', [])
                            
                            if valores:
                                df_nube = pd.DataFrame(valores[1:], columns=valores[0])
                                st.session_state["df_ventas"] = df_nube
                                st.success(f"✅ Sincronizado: {archivos[0]['name']}")
                            else:
                                st.warning("El archivo seleccionado está vacío.")
                        else:
                            st.error("No se encontró el archivo. Revisa el nombre exacto.")
                    except Exception as e:
                        st.error(f"Error de conexión a Drive: {e}")
                        if "invalid_grant" in str(e):
                            del st.session_state["google_creds"]

    # --- 2. PROCESAMIENTO IA Y GRÁFICOS ---
    df_actual = st.session_state["df_ventas"]
    
    if not df_actual.empty:
        st.divider()
        with st.spinner("🧠 El Cerebro IA está estructurando tu panel..."):
            mapa_ia = mapear_columnas(list(df_actual.columns))
            
        col_valor = mapa_ia.get('valor')
        col_cat = mapa_ia.get('categoria')
        col_fecha = mapa_ia.get('fecha')
        
        # Filtro Inteligente Lateral
        col_filtro = mapa_ia.get('filtro')
        if col_filtro and col_filtro in df_actual.columns:
            seleccion = st.sidebar.selectbox(f"📍 Filtro: {col_filtro}", ["Todos"] + list(df_actual[col_filtro].unique()))
            if seleccion != "Todos":
                df_actual = df_actual[df_actual[col_filtro] == seleccion]

        # Interfaz de Gráficos (El Graficador Pro)
        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader("📈 Tendencias")
            if col_fecha and col_valor in df_actual.columns:
                # Preparamos los datos forzando el formato correcto (Fechas a Date, Valores a Número)
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
                
                agrupado = df_proporcion.groupby(col_cat)[col_valor].sum().reset_index()
                
                tipo_c = st.selectbox("Formato:", ["Donut (Profesional)", "Pastel (Clásico)", "Barras"], key="s1")
                if tipo_c == "Donut (Profesional)": fig2 = px.pie(agrupado, names=col_cat, values=col_valor, hole=0.5)
                elif tipo_c == "Pastel (Clásico)": fig2 = px.pie(agrupado, names=col_cat, values=col_valor)
                else: fig2 = px.bar(agrupado, x=col_cat, y=col_valor, color=col_cat)
                st.plotly_chart(fig2, use_container_width=True)

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

# --- 1. CONFIGURACIÓN DE PÁGINA Y ESTADOS ---
st.set_page_config(page_title="SaaS Analytics Pro", page_icon="🏢", layout="wide")

# Inicializamos la "memoria" de la aplicación
if 'autenticado' not in st.session_state:
    st.session_state['autenticado'] = False
if 'empresa_id' not in st.session_state:
    st.session_state['empresa_id'] = None
if 'nombre_empresa' not in st.session_state:
    st.session_state['nombre_empresa'] = None

# --- 2. MÓDULO DE SEGURIDAD (LA BÓVEDA LIMPIA) ---
def verificar_login(email, password_plana):
    """Se conecta a PostgreSQL para validar usuarios de tu SaaS."""
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

# --- 3. MÓDULOS DE INGESTIÓN Y LIMPIEZA ---
def reparar_archivo_local(uploaded_file):
    """El Reparador: Cura archivos corruptos subidos a mano."""
    bytes_data = uploaded_file.getvalue()
    if uploaded_file.name.endswith(('.xlsx', '.xls')):
        return pd.read_excel(io.BytesIO(bytes_data))
        
    for enc in ['utf-8', 'latin1', 'windows-1252']:
        try:
            texto = bytes_data.decode(enc)
            return pd.read_csv(io.StringIO(texto))
        except UnicodeDecodeError:
            continue
    raise ValueError("Archivo intratable.")

def extraer_limpiar_drive(nombre_archivo):
    """El Limpiador: Conecta a Google Workspace y estandariza."""
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    credenciales = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    cliente = gspread.authorize(credenciales)
    
    hoja = cliente.open(nombre_archivo).sheet1
    df = pd.DataFrame(hoja.get_all_records())
    
    # Reglas de limpieza corporativa
    df = df.replace(["", " "], pd.NA)
    df = df.dropna(how='all') # Borra filas totalmente vacías
    df = df.fillna("NO_DATO")
    return df

# --- 4. EL CEREBRO IA ---
@st.cache_data
def mapear_columnas(lista_de_columnas):
    """Usa Gemini para entender cualquier Excel."""
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
                # Simulador de carga para UX
                with st.spinner("Desencriptando credenciales..."):
                    time.sleep(1) 
                    
                    # AQUÍ llamas a la BD. Para pruebas si no tienes la BD armada aún,
                    # puedes comentar la línea de abajo y usar un bypass temporal:
                    # exito, c_id, c_name = True, "123", "Empresa Demo S.A."
                    
                    exito, c_id, c_name = verificar_login(email_input, pass_input)
                    
                    if exito:
                        st.session_state['autenticado'] = True
                        st.session_state['empresa_id'] = c_id
                        st.session_state['nombre_empresa'] = c_name
                        st.rerun() # Recarga la app para ocultar el login
                    else:
                        st.error("❌ Credenciales incorrectas o usuario no encontrado.")

# --- PANTALLA PRINCIPAL (PROTEGIDA) ---
else:
    # Sidebar Corporativo
    st.sidebar.title(f"🏢 {st.session_state['nombre_empresa']}")
    st.sidebar.caption(f"ID de Cliente: {st.session_state['empresa_id']}")
    st.sidebar.divider()
    
    if st.sidebar.button("Cerrar Sesión", type="primary"):
        st.session_state['autenticado'] = False
        st.rerun()

    st.title("💸 Panel de Inteligencia de Negocios")
    st.markdown("Selecciona el origen de tus datos para comenzar el análisis.")
    
    # 1. ENRUTADOR DE INGESTIÓN
    fuente_datos = st.radio("Origen de datos:", ["Subir Archivo Local", "Sincronizar Google Drive"], horizontal=True)
    
    df_ventas = pd.DataFrame()
    
# 1. CONFIGURACIÓN DEL MOTOR OAUTH (Coloca esto justo antes del primer if)
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

    # 2. CAPTURA DEL CÓDIGO DE GOOGLE (Gestión automática del regreso del login)
    if "code" in st.query_params:
        try:
            flow.fetch_token(code=st.query_params["code"])
            st.session_state["google_creds"] = flow.credentials
            # Limpiamos la URL para una experiencia limpia
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Error de autorización: {e}")

    # --- 3. BLOQUE REEMPLAZADO ---
    if fuente_datos == "Subir Archivo Local":
        archivo = st.file_uploader("Sube tu CSV o Excel", type=['csv', 'xlsx'])
        if archivo:
            with st.spinner("Reparando archivo en memoria..."):
                # Tu función de limpieza local original
                df_ventas = reparar_archivo_local(archivo)
                st.session_state["df_ventas"] = df_ventas 
                st.success("✅ Archivo curado y cargado.")
                
    elif fuente_datos == "Sincronizar Google Drive":
        # Verificamos si ya hay una sesión de Google activa
        if "google_creds" not in st.session_state:
            auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
            st.info("Para sincronizar archivos de la nube, primero vincula tu cuenta.")
            st.link_button("🔐 Iniciar sesión con Google", auth_url)
        else:
            st.success("✅ Cuenta de Google vinculada.")
            nombre_sheet = st.text_input("Nombre del archivo en tu Google Workspace:")
            
            if st.button("Conectar y Limpiar Nube") and nombre_sheet:
                with st.spinner("Accediendo a tu Drive y auditando datos..."):
                    try:
                        creds = st.session_state["google_creds"]
                        # Construimos la conexión a Drive
                        servicio_drive = build('drive', 'v3', credentials=creds)
                        
                        # Buscamos el archivo por nombre
                        q = f"name contains '{nombre_sheet}' and mimeType='application/vnd.google-apps.spreadsheet'"
                        res = servicio_drive.files().list(q=q, fields="files(id, name)").execute()
                        archivos = res.get('files', [])
                        
                        if archivos:
                            file_id = archivos[0]['id']
                            # Construimos la conexión a Sheets y descargamos
                            servicio_sheets = build('sheets', 'v4', credentials=creds)
                            res_hoja = servicio_sheets.spreadsheets().values().get(
                                spreadsheetId=file_id, range='A1:Z2000'
                            ).execute()
                            valores = res_hoja.get('values', [])
                            
                            if valores:
                                # Transformamos a DataFrame
                                df_ventas = pd.DataFrame(valores[1:], columns=valores[0])
                                st.session_state["df_ventas"] = df_ventas
                                st.success(f"✅ Sincronizado: {archivos[0]['name']}")
                            else:
                                st.warning("El archivo seleccionado está vacío.")
                        else:
                            st.error("No se encontró el archivo. Revisa el nombre exacto.")
                    except Exception as e:
                        st.error(f"Error de conexión a Drive: {e}")
                        # Si el token caduca, forzamos re-login
                        if "invalid_grant" in str(e):
                            del st.session_state["google_creds"]

    # 2. PROCESAMIENTO IA Y GRÁFICOS (Solo si hay datos)
    if not df_ventas.empty:
        st.divider()
        with st.spinner("🧠 El Cerebro IA está estructurando tu panel..."):
            mapa_ia = mapear_columnas(list(df_ventas.columns))
            
        col_valor = mapa_ia.get('valor')
        col_cat = mapa_ia.get('categoria')
        col_fecha = mapa_ia.get('fecha')
        
        # Filtro Inteligente
        col_filtro = mapa_ia.get('filtro')
        if col_filtro and col_filtro in df_ventas.columns:
            seleccion = st.sidebar.selectbox(f"📍 Filtro: {col_filtro}", ["Todos"] + list(df_ventas[col_filtro].unique()))
            if seleccion != "Todos":
                df_ventas = df_ventas[df_ventas[col_filtro] == seleccion]

        # Interfaz de Gráficos (El Graficador Pro)
        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader("📈 Tendencias")
            if col_fecha and col_valor in df_ventas.columns:
                # Limpiamos fechas
                df_ventas[col_fecha] = pd.to_datetime(df_ventas[col_fecha], errors='coerce')
                tendencia = df_ventas.groupby(df_ventas[col_fecha].dt.to_period("M").astype(str))[col_valor].sum().reset_index()
                
                # Selector de Plotly
                tipo_g = st.radio("Formato:", ["Líneas", "Área", "Barras"], horizontal=True, key="r1")
                if tipo_g == "Líneas": fig = px.line(tendencia, x=col_fecha, y=col_valor)
                elif tipo_g == "Área": fig = px.area(tendencia, x=col_fecha, y=col_valor)
                else: fig = px.bar(tendencia, x=col_fecha, y=col_valor)
                st.plotly_chart(fig, use_container_width=True)
                
        with c2:
            st.subheader(f"🗺️ Desglose por {col_cat if col_cat else 'Categoría'}")
            if col_cat and col_valor in df_ventas.columns:
                agrupado = df_ventas.groupby(col_cat)[col_valor].sum().reset_index()
                
                # Selector de Plotly para proporciones
                tipo_c = st.selectbox("Formato:", ["Donut (Profesional)", "Pastel (Clásico)", "Barras"], key="s1")
                if tipo_c == "Donut (Profesional)": fig2 = px.pie(agrupado, names=col_cat, values=col_valor, hole=0.5)
                elif tipo_c == "Pastel (Clásico)": fig2 = px.pie(agrupado, names=col_cat, values=col_valor)
                else: fig2 = px.bar(agrupado, x=col_cat, y=col_valor, color=col_cat)
                st.plotly_chart(fig2, use_container_width=True)

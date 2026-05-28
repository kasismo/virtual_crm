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
import smtplib
from email.message import EmailMessage
# ==========================================
# --- 1. CONFIGURACIÓN DE PÁGINA Y ESTADOS ---
# ==========================================
st.set_page_config(page_title="SaaS Analytics Pro", page_icon="🏢", layout="wide")

if 'autenticado' not in st.session_state: st.session_state['autenticado'] = False
if 'empresa_id' not in st.session_state: st.session_state['empresa_id'] = None
if 'nombre_empresa' not in st.session_state: st.session_state['nombre_empresa'] = None
if 'df_ventas' not in st.session_state: st.session_state['df_ventas'] = pd.DataFrame()
if 'mapa_ia' not in st.session_state: st.session_state['mapa_ia'] = {}
if 'archivo_procesado' not in st.session_state: st.session_state['archivo_procesado'] = None
if 'stats_auditoria' not in st.session_state: st.session_state['stats_auditoria'] = {'orig': 0, 'dups': 0}

# ==========================================
# --- 2. MÓDULOS DE BASE DE DATOS (LA BÓVEDA) ---
# ==========================================
def verificar_login(email, password_plana):
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        query = text("""
            SELECT u.password_hash, u.empresa_id, e.nombre_empresa 
            FROM usuarios u JOIN empresas e ON u.empresa_id = e.id
            WHERE TRIM(u.email) = :email
        """)
        with motor.connect() as conexion:
            resultado = conexion.execute(query, {"email": email.strip()}).fetchone()
            
        if resultado:
            if bcrypt.checkpw(password_plana.strip().encode('utf-8'), resultado[0].strip().encode('utf-8')):
                return True, resultado[1], resultado[2] 
        return False, None, None
    except Exception as e:
        st.error(f"Error de autenticación: {e}")
        return False, None, None

def guardar_estado_saas(empresa_id, mapping_json, df):
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        mapping_str = json.dumps(mapping_json)
        datos_csv_str = df.to_csv(index=False)
        query = text("""
            UPDATE empresas 
            SET ultimo_mapeo = :mapping, datos_guardados = :datos 
            WHERE id = :id
        """)
        with motor.connect() as conexion:
            conexion.execute(query, {"mapping": mapping_str, "datos": datos_csv_str, "id": empresa_id})
            conexion.commit()
    except Exception as e:
        st.error(f"Error al guardar persistencia: {e}")

def recuperar_estado_saas(empresa_id):
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        query = text("SELECT ultimo_mapeo, datos_guardados FROM empresas WHERE id = :id")
        with motor.connect() as conexion:
            res = conexion.execute(query, {"id": empresa_id}).fetchone()
        
        mapa = json.loads(res[0]) if res and res[0] else {}
        df = pd.read_csv(io.StringIO(res[1])) if res and res[1] else pd.DataFrame()
        return mapa, df
    except Exception as e:
        return {}, pd.DataFrame()

def enviar_ticket_soporte(nombre_empresa, id_empresa, mensaje, adjunto):
    """Crea y envía un correo electrónico formateado al CTO (Tú)."""
    # Usaremos st.secrets para proteger tus credenciales
    remitente = st.secrets["email"]["usuario"] 
    password = st.secrets["email"]["password"] 
    destinatario = "fakuokey@gmail.com" # <-- Pon tu correo aquí

    # 1. Armamos la estructura del correo
    msg = EmailMessage()
    msg['Subject'] = f"🚨 Ticket de Soporte: {nombre_empresa} (ID: {id_empresa})"
    msg['From'] = remitente
    msg['To'] = destinatario
    
    # El cuerpo del mensaje
    cuerpo_correo = f"""
    Ha ingresado una nueva solicitud de soporte desde el Panel SaaS.
    
    🏢 Empresa: {nombre_empresa}
    🆔 ID de Cliente: {id_empresa}
    
    📝 Mensaje del cliente:
    -------------------------------------------
    {mensaje}
    -------------------------------------------
    """
    msg.set_content(cuerpo_correo)

    # 2. Procesamos la captura de pantalla si el cliente subió una
    if adjunto is not None:
        adjunto_bytes = adjunto.read()
        # Detectamos la extensión para adjuntarlo correctamente
        tipo = adjunto.name.split('.')[-1].lower()
        formato = 'jpeg' if tipo == 'jpg' else tipo
        msg.add_attachment(adjunto_bytes, maintype='image', subtype=formato, filename=adjunto.name)

    # 3. Nos conectamos a Google y disparamos el correo
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(remitente, password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        return False

# ==========================================
# --- 3. INGESTIÓN Y IA ---
# ==========================================
def leer_archivo_seguro(uploaded_file):
    cabecera_bytes = uploaded_file.read(4)
    uploaded_file.seek(0)
    if cabecera_bytes.startswith(b'PK') or cabecera_bytes.startswith(b'\xd0\xcf') or uploaded_file.name.endswith(('.xlsx', '.xls')):
        return pd.read_excel(uploaded_file)
    else:
        muestra_bytes = uploaded_file.read(50000)
        uploaded_file.seek(0)
        cod = chardet.detect(muestra_bytes)['encoding'] or 'utf-8'
        if cod.lower() == 'ascii': cod = 'utf-8'
        texto = muestra_bytes.decode(cod, errors='replace')
        try: delim = csv.Sniffer().sniff(texto).delimiter
        except: delim = ',' 
        return pd.read_csv(uploaded_file, encoding=cod, sep=delim, on_bad_lines='skip', engine='python')

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
                with st.spinner("Desencriptando y cargando tu ecosistema..."):
                    time.sleep(1) 
                    exito, c_id, c_name = verificar_login(email_input, pass_input)
                    if exito:
                        st.session_state['autenticado'] = True
                        st.session_state['empresa_id'] = c_id
                        st.session_state['nombre_empresa'] = c_name
                        
                        mapa_bd, df_bd = recuperar_estado_saas(c_id)
                        if not df_bd.empty:
                            st.session_state['mapa_ia'] = mapa_bd
                            st.session_state['df_ventas'] = df_bd
                            # Si venimos de la BD, las métricas de carga asumen la base ya limpia
                            st.session_state['stats_auditoria'] = {'orig': len(df_bd), 'dups': 0}
                            
                        st.rerun() 
                    else:
                        st.error("❌ Credenciales incorrectas.")

else:
    # ==========================================
    # --- SIDEBAR CORPORATIVO Y NAVEGACIÓN ---
    # ==========================================
    st.sidebar.title(f"🏢 {st.session_state['nombre_empresa']}")
    st.sidebar.caption(f"ID de Cliente: {st.session_state['empresa_id']}")
    st.sidebar.divider()
    
    # 📌 EL MENÚ PRINCIPAL (El enrutador)
    pantalla_actual = st.sidebar.radio(
        "Navegación del CRM",
        ["📊 Dashboard de Ventas", "👥 Gestión de Clientes (CRM)", "⚙️ Importar Base Histórica"]
    )
    
    st.sidebar.divider()
    
    # --- EL WIDGET DE SOPORTE (Se queda fijo en el sidebar) ---
    with st.sidebar.popover("💬 Ayuda y Soporte Técnico", use_container_width=True):
        st.markdown(f"**🤖 Asistente de Industrias Faku**\n\n¡Hola equipo de **{st.session_state['nombre_empresa']}**! ¿Tienen algún problema?")
        with st.form("form_soporte", clear_on_submit=True):
            mensaje_soporte = st.text_area("Escribe tu inconveniente con detalle:")
            captura = st.file_uploader("Adjuntar captura del error (Opcional)", type=["png", "jpg", "jpeg"])
            enviado = st.form_submit_button("📩 Enviar Reporte", use_container_width=True)
            if enviado:
                if len(mensaje_soporte) < 5:
                    st.error("Por favor, escribe un mensaje más largo.")
                else:
                    with st.spinner("Enviando reporte a la central..."):
                        exito = enviar_ticket_soporte(st.session_state['nombre_empresa'], st.session_state['empresa_id'], mensaje_soporte, captura)
                        if exito: st.success("✅ ¡Ticket enviado!")
                        else: st.error("❌ Fallo de conexión.")
                        
    # Botón de Cerrar Sesión (Al fondo del sidebar)
    st.sidebar.text("") 
    if st.sidebar.button("🚪 Cerrar Sesión", type="primary", use_container_width=True):
        for key in ['autenticado', 'df_ventas', 'mapa_ia', 'archivo_procesado', 'stats_auditoria']:
            if key in st.session_state: del st.session_state[key]
        st.rerun()


    # ==========================================
    # --- PANTALLA 1: DASHBOARD ---
    # ==========================================
    if pantalla_actual == "📊 Dashboard de Ventas":
        st.title("💸 Panel de Inteligencia de Negocios")
        
        df_actual = st.session_state.get("df_ventas", pd.DataFrame())
        mapa_ia = st.session_state.get("mapa_ia", {})
        
        if df_actual.empty:
            st.info("👋 ¡Bienvenido! Ve a la pestaña '⚙️ Importar Base Histórica' para subir tu primer archivo.")
        else:
            st.success("⚡ Sistema cargado y sincronizado desde la nube.")
            
            # --- AUDITORÍA FORENSE ---
            mask_incompletas = df_actual.astype(str).eq("NO_DATO").any(axis=1)
            indices_incompletas = df_actual[mask_incompletas].index 
            num_incompletas = len(indices_incompletas)
            
            st.subheader("📊 Auditoría Forense de Datos")
            stats = st.session_state['stats_auditoria']
            c_m1, c_m2, c_m3 = st.columns(3)
            c_m1.metric("Filas Evaluadas", f"{stats['orig']:,}")
            c_m2.metric("Duplicados Eliminados", f"{stats['dups']:,}", delta_color="inverse")
            c_m3.metric("Filas con Datos Faltantes", f"{num_incompletas:,}")
            
            if num_incompletas > 0:
                st.warning(f"⚠️ Se han detectado y resaltado en amarillo {num_incompletas:,} filas con datos incompletos.")
                
            def resaltar_amarillo(row):
                if row.name in indices_incompletas: return ['background-color: #ffd966; color: black'] * len(row)
                return [''] * len(row)
                
            col_btn, _ = st.columns([1, 2])
            with col_btn:
                buffer = io.BytesIO()
                df_actual.style.apply(resaltar_amarillo, axis=1).to_excel(buffer, index=False, engine='openpyxl')
                st.download_button(
                    "📥 Descargar Base Auditada", 
                    data=buffer.getvalue(), 
                    file_name="datos_auditados_alertas.xlsx", 
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                    use_container_width=True
                )
                
            st.dataframe(df_actual.head(100).style.apply(resaltar_amarillo, axis=1), use_container_width=True)
            st.divider()
            
            # --- GRÁFICOS INTELIGENTES ---
            col_valor = mapa_ia.get('valor')
            col_cat = mapa_ia.get('categoria')
            col_fecha = mapa_ia.get('fecha')
            col_filtro = mapa_ia.get('filtro')
            
            if col_filtro and col_filtro in df_actual.columns:
                seleccion = st.selectbox(f"📍 Filtro global: {col_filtro}", ["Todos"] + list(df_actual[col_filtro].unique()))
                if seleccion != "Todos": df_actual = df_actual[df_actual[col_filtro] == seleccion]

            c1, c2 = st.columns(2)
            with c1:
                st.subheader("📈 Tendencias")
                if (col_fecha in df_actual.columns) and (col_valor in df_actual.columns):
                    df_tendencia = df_actual.copy()
                    df_tendencia[col_fecha] = pd.to_datetime(df_tendencia[col_fecha], errors='coerce')
                    df_tendencia[col_valor] = pd.to_numeric(df_tendencia[col_valor], errors='coerce').fillna(0)
                    tendencia = df_tendencia.groupby(df_tendencia[col_fecha].dt.to_period("M").astype(str))[col_valor].sum().reset_index()
                    
                    tipo_g = st.radio("Formato:", ["Líneas", "Área", "Barras"], horizontal=True, key="r1")
                    if tipo_g == "Líneas": fig = px.line(tendencia, x=col_fecha, y=col_valor)
                    elif tipo_g == "Área": fig = px.area(tendencia, x=col_fecha, y=col_valor)
                    else: fig = px.bar(tendencia, x=col_fecha, y=col_valor)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("IA no detectó columnas de Fecha y Valor compatibles.")
                    
            with c2:
                st.subheader(f"🗺️ Desglose por {col_cat if col_cat else 'Categoría'}")
                if (col_cat in df_actual.columns) and (col_valor in df_actual.columns):
                    df_proporcion = df_actual.copy()
                    df_proporcion[col_valor] = pd.to_numeric(df_proporcion[col_valor], errors='coerce').fillna(0)
                    df_proporcion[col_cat] = df_proporcion[col_cat].astype(str).str.strip().str.upper() 
                    
                    agrupado = df_proporcion.groupby(col_cat)[col_valor].sum().reset_index()
                    tipo_c = st.selectbox("Formato:", ["Donut (Profesional)", "Pastel (Clásico)", "Barras"], key="s1")
                    
                    if tipo_c in ["Donut (Profesional)", "Pastel (Clásico)"]:
                        agrupado_positivo = agrupado[agrupado[col_valor] > 0]
                        if agrupado_positivo.empty:
                            st.warning("⚠️ Valores negativos o cero. Usa 'Barras'.")
                        else:
                            if tipo_c == "Donut (Profesional)": fig2 = px.pie(agrupado_positivo, names=col_cat, values=col_valor, hole=0.5)
                            else: fig2 = px.pie(agrupado_positivo, names=col_cat, values=col_valor)
                            st.plotly_chart(fig2, use_container_width=True)
                    else: 
                        fig2 = px.bar(agrupado, x=col_cat, y=col_valor, color=col_cat)
                        st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.warning("IA no detectó columnas de Categoría y Valor compatibles.")


    # ==========================================
    # --- PANTALLA 2: EL CRM VIP ---
    # ==========================================
    elif pantalla_actual == "👥 Gestión de Clientes (CRM)":
        st.title("👥 Panel de Clientes y Embudo")
        st.markdown("Visualiza, edita y agrega nuevos clientes a tu cartera de forma dinámica.")
        
        st.subheader("➕ Cargar Nuevo Lead")
        with st.container(border=True):
            c1, c2, c3 = st.columns(3)
            c1.text_input("Nombre del Cliente")
            c2.text_input("Correo/Teléfono")
            c3.selectbox("Estado", ["Prospecto", "Negociación", "Ganado", "Perdido"])
            st.button("Guardar Cliente (Simulado)", type="primary")
            
        st.divider()
        st.subheader("🗂️ Base de Datos en Vivo")
        st.info("Aquí aparecerá tu tabla dinámica donde podrás editar las celdas directamente.")


    # ==========================================
    # --- PANTALLA 3: IMPORTACIÓN E INGESTIÓN ---
    # ==========================================
    elif pantalla_actual == "⚙️ Importar Base Histórica":
        st.title("⚙️ Carga Inicial de Datos")
        st.markdown("Usa esta herramienta para hacer una migración masiva desde un Excel antiguo a la base de datos.")
        
        with st.container(border=True):
            archivo = st.file_uploader("Sube tu archivo .xlsx o .csv", type=['csv', 'xlsx', 'xls'])
            
            if archivo and st.session_state.get('archivo_procesado') != archivo.name:
                with st.spinner("🏥 Operando archivo y actualizando servidores..."):
                    df_crudo = leer_archivo_seguro(archivo)
                    if not df_crudo.empty:
                        df_crudo = df_crudo.replace(["", " "], pd.NA)
                        total_orig = len(df_crudo)
                        
                        df_limpio = df_crudo.drop_duplicates().reset_index(drop=True)
                        dups = total_orig - len(df_limpio)
                        
                        if 'ID' in df_limpio.columns: df_limpio = df_limpio.drop('ID', axis=1)
                        df_limpio.insert(0, 'ID', range(1, len(df_limpio) + 1))
                        df_limpio = df_limpio.fillna("NO_DATO")
                        
                        nuevo_mapa = mapear_columnas(list(df_limpio.columns))
                        guardar_estado_saas(st.session_state['empresa_id'], nuevo_mapa, df_limpio)
                        
                        st.session_state["df_ventas"] = df_limpio
                        st.session_state["mapa_ia"] = nuevo_mapa
                        st.session_state['archivo_procesado'] = archivo.name
                        st.session_state['stats_auditoria'] = {'orig': total_orig, 'dups': dups}
                        
                        st.success("✅ Sistema actualizado. Ve a la pestaña 'Dashboard de Ventas' para ver el panel.")
    
    # --- ZONA DE ACTUALIZACIÓN FLUIDA ---
    with st.expander("⚙️ Actualizar o Subir Nueva Base de Datos"):
        archivo = st.file_uploader("Sube tu archivo para sobrescribir los datos actuales.", type=['csv', 'xlsx', 'xls'])
        
        # El Candado: Solo procesamos si hay un archivo y NO lo hemos procesado antes en esta sesión
        if archivo and st.session_state.get('archivo_procesado') != archivo.name:
            with st.spinner("🏥 Operando archivo y actualizando servidores..."):
                df_crudo = leer_archivo_seguro(archivo)
                if not df_crudo.empty:
                    df_crudo = df_crudo.replace(["", " "], pd.NA)
                    total_orig = len(df_crudo)
                    
                    df_limpio = df_crudo.drop_duplicates().reset_index(drop=True)
                    dups = total_orig - len(df_limpio)
                    
                    if 'ID' in df_limpio.columns: df_limpio = df_limpio.drop('ID', axis=1)
                    df_limpio.insert(0, 'ID', range(1, len(df_limpio) + 1))
                    
                    # Marcamos faltantes antes de rellenar
                    df_limpio = df_limpio.fillna("NO_DATO")
                    
                    nuevo_mapa = mapear_columnas(list(df_limpio.columns))
                    guardar_estado_saas(st.session_state['empresa_id'], nuevo_mapa, df_limpio)
                    
                    # Actualizamos memoria viva y activamos el candado
                    st.session_state["df_ventas"] = df_limpio
                    st.session_state["mapa_ia"] = nuevo_mapa
                    st.session_state['archivo_procesado'] = archivo.name
                    st.session_state['stats_auditoria'] = {'orig': total_orig, 'dups': dups}
                    
                    st.success("✅ Sistema actualizado. Fluyendo datos al panel...")
                    # ¡NO HAY st.rerun() AQUÍ! El código fluye libremente hacia abajo.

    # --- RENDERIZADO DEL PANEL PRINCIPAL ---
    df_actual = st.session_state.get("df_ventas", pd.DataFrame())
    mapa_ia = st.session_state.get("mapa_ia", {})
    
    if df_actual.empty:
        st.info("👋 ¡Bienvenido! Despliega el menú 'Actualizar Base de Datos' de arriba para subir tu primer archivo.")
    else:
        # --- 1. PANEL DE AUDITORÍA Y LIMPIEZA (Siempre visible con datos) ---
        st.success("⚡ Sistema cargado y sincronizado desde la nube.")
        
        # Detectamos datos incompletos buscando la firma "NO_DATO"
        mask_incompletas = df_actual.astype(str).eq("NO_DATO").any(axis=1)
        indices_incompletas = df_actual[mask_incompletas].index 
        num_incompletas = len(indices_incompletas)
        
        st.subheader("📊 Auditoría Forense de Datos")
        stats = st.session_state['stats_auditoria']
        
        c_m1, c_m2, c_m3 = st.columns(3)
        c_m1.metric("Filas Evaluadas", f"{stats['orig']:,}")
        c_m2.metric("Duplicados Eliminados", f"{stats['dups']:,}", delta_color="inverse")
        c_m3.metric("Filas con Datos Faltantes", f"{num_incompletas:,}")
        
        if num_incompletas > 0:
            st.warning(f"⚠️ Se han detectado y resaltado en amarillo {num_incompletas:,} filas con datos incompletos.")
            
        def resaltar_amarillo(row):
            if row.name in indices_incompletas: return ['background-color: #ffd966; color: black'] * len(row)
            return [''] * len(row)
            
        # Botón de Descarga Excel
        col_btn, _ = st.columns([1, 2])
        with col_btn:
            buffer = io.BytesIO()
            df_actual.style.apply(resaltar_amarillo, axis=1).to_excel(buffer, index=False, engine='openpyxl')
            st.download_button(
                "📥 Descargar Base Auditada (Excel)", 
                data=buffer.getvalue(), 
                file_name="datos_auditados_alertas.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
                use_container_width=True
            )
            
        st.dataframe(df_actual.head(100).style.apply(resaltar_amarillo, axis=1), use_container_width=True)
        st.divider()
        
        # --- 2. LOS GRÁFICOS INTELIGENTES ---
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
            if (col_fecha in df_actual.columns) and (col_valor in df_actual.columns):
                df_tendencia = df_actual.copy()
                df_tendencia[col_fecha] = pd.to_datetime(df_tendencia[col_fecha], errors='coerce')
                df_tendencia[col_valor] = pd.to_numeric(df_tendencia[col_valor], errors='coerce').fillna(0)
                tendencia = df_tendencia.groupby(df_tendencia[col_fecha].dt.to_period("M").astype(str))[col_valor].sum().reset_index()
                
                tipo_g = st.radio("Formato:", ["Líneas", "Área", "Barras"], horizontal=True, key="r1")
                if tipo_g == "Líneas": fig = px.line(tendencia, x=col_fecha, y=col_valor)
                elif tipo_g == "Área": fig = px.area(tendencia, x=col_fecha, y=col_valor)
                else: fig = px.bar(tendencia, x=col_fecha, y=col_valor)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("IA no detectó columnas de Fecha y Valor compatibles.")
                
        with c2:
            st.subheader(f"🗺️ Desglose por {col_cat if col_cat else 'Categoría'}")
            if (col_cat in df_actual.columns) and (col_valor in df_actual.columns):
                df_proporcion = df_actual.copy()
                df_proporcion[col_valor] = pd.to_numeric(df_proporcion[col_valor], errors='coerce').fillna(0)
                df_proporcion[col_cat] = df_proporcion[col_cat].astype(str).str.strip().str.upper() 
                
                agrupado = df_proporcion.groupby(col_cat)[col_valor].sum().reset_index()
                tipo_c = st.selectbox("Formato:", ["Donut (Profesional)", "Pastel (Clásico)", "Barras"], key="s1")
                
                if tipo_c in ["Donut (Profesional)", "Pastel (Clásico)"]:
                    agrupado_positivo = agrupado[agrupado[col_valor] > 0]
                    if agrupado_positivo.empty:
                        st.warning("⚠️ Valores negativos o cero. Usa 'Barras'.")
                    else:
                        if tipo_c == "Donut (Profesional)": fig2 = px.pie(agrupado_positivo, names=col_cat, values=col_valor, hole=0.5)
                        else: fig2 = px.pie(agrupado_positivo, names=col_cat, values=col_valor)
                        st.plotly_chart(fig2, use_container_width=True)
                else: 
                    fig2 = px.bar(agrupado, x=col_cat, y=col_valor, color=col_cat)
                    st.plotly_chart(fig2, use_container_width=True)
            else:
                st.warning("IA no detectó columnas de Categoría y Valor compatibles.")

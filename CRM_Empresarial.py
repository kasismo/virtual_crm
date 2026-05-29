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
    remitente = st.secrets["email"]["usuario"] 
    password = st.secrets["email"]["password"] 
    destinatario = "fakuokey@gmail.com" 

    msg = EmailMessage()
    msg['Subject'] = f"🚨 Ticket de Soporte: {nombre_empresa} (ID: {id_empresa})"
    msg['From'] = remitente
    msg['To'] = destinatario
    
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

    if adjunto is not None:
        adjunto_bytes = adjunto.read()
        tipo = adjunto.name.split('.')[-1].lower()
        formato = 'jpeg' if tipo == 'jpg' else tipo
        msg.add_attachment(adjunto_bytes, maintype='image', subtype=formato, filename=adjunto.name)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(remitente, password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        return False

# --- AQUÍ VAN LAS NUEVAS HERRAMIENTAS DE CRM ---
def obtener_clientes(empresa_id):
    """Consulta la base de datos viva y devuelve los clientes en formato Pandas"""
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        query = text("SELECT id, nombre_cliente, contacto, estado, fecha_registro FROM clientes_crm WHERE empresa_id = :id ORDER BY id DESC")
        with motor.connect() as conexion:
            df_clientes = pd.read_sql(query, conexion, params={"id": empresa_id})
        return df_clientes
    except Exception as e:
        st.error(f"Error al cargar la base de clientes: {e}")
        return pd.DataFrame()

def insertar_cliente(empresa_id, nombre, contacto, estado):
    """Inyecta una nueva fila en la tabla clientes_crm de Supabase"""
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        query = text("""
            INSERT INTO clientes_crm (empresa_id, nombre_cliente, contacto, estado)
            VALUES (:emp_id, :nombre, :contacto, :estado)
        """)
        with motor.connect() as conexion:
            conexion.execute(query, {"emp_id": empresa_id, "nombre": nombre, "contacto": contacto, "estado": estado})
            conexion.commit()
        return True
    except Exception as e:
        st.error(f"Error al guardar cliente en el servidor: {e}")
        return False

def migrar_df_a_crm(empresa_id, df):
    """Recorre el DataFrame importado y crea leads individuales en clientes_crm"""
    try:
        motor = create_engine(st.secrets["DB_AUTH_URI"])
        
        # 1. Detectar columnas dinámicamente por palabras clave
        col_nombre = next((c for c in df.columns if c.lower() in ['nombre_cliente', 'customer_name', 'cliente', 'nombre']), None)
        col_contacto = next((c for c in df.columns if c.lower() in ['contacto', 'email', 'correo', 'telefono', 'phone', 'customer_id', 'order_id']), None)
        col_estado = next((c for c in df.columns if c.lower() in ['estado', 'status', 'stage']), None)
        
        # Fallback por si las columnas tienen nombres totalmente raros
        if not col_nombre: 
            col_nombre = df.select_dtypes(include=['object']).columns[0]
            
        query = text("""
            INSERT INTO clientes_crm (empresa_id, nombre_cliente, contacto, estado)
            VALUES (:emp_id, :nombre, :contacto, :estado)
        """)
        
        with motor.connect() as conexion:
            for _, fila in df.iterrows():
                # Extracción y limpieza de datos de la fila
                nombre = str(fila[col_nombre]) if col_nombre in df.columns and pd.notna(fila[col_nombre]) else "Cliente Anónimo"
                contacto = str(fila[col_contacto]) if col_contacto and pd.notna(fila[col_contacto]) else "Sin Datos"
                estado_crudo = str(fila[col_estado]).lower() if col_estado and pd.notna(fila[col_estado]) else "prospecto"
                
                # Homologación de estados de logística/ventas a estados de CRM
                if estado_crudo in ['shipped', 'delivered', 'ganado', 'processed', 'processing']:
                    estado_crm = 'Ganado'
                elif estado_crudo in ['cancelled', 'returned', 'perdido', 'cattled']:
                    estado_crm = 'Perdido'
                else:
                    estado_crm = 'Prospecto'
                
                # Ejecutamos la inserción individual
                conexion.execute(query, {
                    "emp_id": empresa_id,
                    "nombre": nombre,
                    "contacto": contacto,
                    "estado": estado_crm
                })
            conexion.commit()
        return True
    except Exception as e:
        st.error(f"Error en la migración masiva al CRM: {e}")
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
    
    # 📌 EL MENÚ PRINCIPAL
    pantalla_actual = st.sidebar.radio(
        "Navegación del CRM",
        ["📊 Dashboard de Ventas", "👥 Gestión de Clientes (CRM)", "⚙️ Importar Base Histórica"]
    )
    
    st.sidebar.divider()
    
    # --- EL WIDGET DE SOPORTE ---
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
                        if exito: st.success("✅ ¡Ticket enviado! Facundo revisará el caso.")
                        else: st.error("❌ Fallo de conexión.")
                        
    # Botón de Cerrar Sesión
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
            st.info("👋 ¡Bienvenido! Ve a la pestaña '⚙️ Importar Base Histórica' en el menú lateral para cargar tus datos.")
        else:
            st.success("⚡ Sistema cargado y sincronizado desde la nube.")
            
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
            with st.form("form_nuevo_cliente", clear_on_submit=True):
                c1, c2, c3 = st.columns(3)
                nombre_nuevo = c1.text_input("Nombre del Cliente *")
                contacto_nuevo = c2.text_input("Correo/Teléfono")
                estado_nuevo = c3.selectbox("Estado", ["Prospecto", "Negociación", "Ganado", "Perdido"])
                
                guardar = st.form_submit_button("Guardar Cliente", type="primary")
                
                if guardar:
                    if len(nombre_nuevo.strip()) < 2:
                        st.error("⚠️ El nombre del cliente es obligatorio.")
                    else:
                        with st.spinner("Inyectando registro en Supabase..."):
                            exito = insertar_cliente(st.session_state['empresa_id'], nombre_nuevo, contacto_nuevo, estado_nuevo)
                            if exito:
                                st.success(f"✅ ¡{nombre_nuevo} agregado al pipeline!")
                                time.sleep(1) 
                                st.rerun() 
            
        st.divider()
        st.subheader("🗂️ Base de Datos en Vivo")
        
        # Leemos los datos directamente de Supabase
        df_crm = obtener_clientes(st.session_state['empresa_id'])
        
        if df_crm.empty:
            st.info("Aún no tienes clientes registrados. ¡Agrega el primero en el panel de arriba!")
        else:
            st.data_editor(df_crm, use_container_width=True, hide_index=True)

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
                        
                        # 1. Guardamos la persistencia para el Dashboard histórico
                        guardar_estado_saas(st.session_state['empresa_id'], nuevo_mapa, df_limpio)
                        
                        # 🚀 2. EL NUEVO PUENTE: Migramos las filas automáticamente como leads reales del CRM
                        with st.spinner("📥 Desglosando archivo e inyectando leads en el pipeline vivo..."):
                            migrar_df_a_crm(st.session_state['empresa_id'], df_limpio)
                        
                        # 3. Guardamos en memoria viva para la sesión actual
                        st.session_state["df_ventas"] = df_limpio
                        st.session_state["mapa_ia"] = nuevo_mapa
                        st.session_state['archivo_procesado'] = archivo.name
                        st.session_state['stats_auditoria'] = {'orig': total_orig, 'dups': dups}
                        
                        st.success("✅ Sincronización completa. Los datos históricos impactaron en el Dashboard y en el pipeline del CRM.")

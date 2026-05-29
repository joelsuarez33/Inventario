import streamlit as st
import pandas as pd
from supabase import create_client, Client
import uuid

st.set_page_config(page_title="Sistema de Inventario Daimler", layout="wide", initial_sidebar_state="expanded")

# --- CREDENCIALES DE SUPABASE DESDE SECRETS ---
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except KeyError:
    st.error("Error: Las credenciales 'SUPABASE_URL' y 'SUPABASE_KEY' no están configuradas en los Secrets de Streamlit.")
    st.stop()

@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

try:
    supabase = init_supabase()
except Exception as e:
    st.error(f"Error de conexión a la base de datos: {e}")
    st.stop()

# --- CARGA DEL MAESTRO DESDE SUPABASE ---
@st.cache_data(ttl=600)
def cargar_maestro():
    try:
        res = supabase.table("maestro_inventario").select("*").execute()
        if res.data:
            df = pd.DataFrame(res.data)
            df.columns = [c.capitalize() for c in df.columns]
            # SOLUCIÓN CRÍTICA 1: Eliminar cualquier NaN del dataframe transformándolo en string vacío
            df = df.fillna("")
            return df
        return pd.DataFrame(columns=["Material", "Descripcion", "Sector", "Cantidad_teorica"])
    except Exception as e:
        st.error(f"Error al mapear el maestro_inventario: {e}")
        return pd.DataFrame(columns=["Material", "Descripcion", "Sector", "Cantidad_teorica"])

df_maestro = cargar_maestro()

# --- INTERFAZ DE USUARIO ---
modo = st.sidebar.radio("Módulo de Trabajo:", ["Operario (Carga de Conteo)", "Supervisor (Monitoreo y Descarga)"])

if modo == "Operario (Carga de Conteo)":
    st.title("📱 Captura de Inventario en Campo")
    st.markdown("---")
    
    # --- BLOQUE FIJO: Datos del Operario y Sector (Persistentes) ---
    st.subheader("👤 Datos de Control (Fijos para la sesión)")
    
    # Inicializar las variables en session_state si no existen
    if "op_nombre" not in st.session_state:
        st.session_state.op_nombre = ""
    if "op_comentarios" not in st.session_state:
        st.session_state.op_comentarios = ""
        
    c_hdr1, c_hdr2 = st.columns([1, 2])
    with c_hdr1:
        # Al asignar key="op_nombre", el valor queda fijo en memoria
        contador = st.text_input("Nombre del Operario:", key="op_nombre", placeholder="Ej: Joel Suarez").strip()
    with c_hdr2:
        comentarios_gen = st.text_input("Observaciones de Inicio / Sector (Opcional):", key="op_comentarios", placeholder="Ej: Pasillo 4").strip()
    
    st.markdown("---")
    
    # Buscador dinámico (reactivo)
    buscar = st.text_input("🔍 Buscar por Material, Descripción o Sector (Escriba para filtrar):", value="")
    
    if buscar:
        buscar_clean = buscar.strip().lower()
        filtrado = df_maestro[
            df_maestro["Material"].astype(str).str.lower().str.contains(buscar_clean) |
            df_maestro["Descripcion"].astype(str).str.lower().str.contains(buscar_clean) |
            df_maestro["Sector"].astype(str).str.lower().str.contains(buscar_clean)
        ].head(5)
        
        if not filtrado.empty:
            st.write("**Coincidencias en tiempo real:**")
            for idx, row in filtrado.iterrows():
                if st.button(f"📦 {row['Material']} | {row['Descripcion']} | Sector: {row['Sector']}", key=f"item_{idx}"):
                    st.session_state.item_seleccionado = row.to_dict()
        else:
            st.warning("No se encontraron coincidencias en el maestro.")

    # FORMULARIO VARIABLES DEL ÍTEM
    if "item_seleccionado" in st.session_state:
        item = st.session_state.item_seleccionado
        st.markdown(f"### Artículo Seleccionado: `{item['Material']}`")
        st.info(f"**Descripción:** {item['Descripcion']}  \n**Sector Teórico:** {item['Sector']}")
        
        # Eliminamos clear_on_submit=True del form para controlar el limpiado de forma manual y selectiva
        with st.form("form_transmision"):
            st.markdown("##### Datos del Conteo Físico")
            col1, col2, col3 = st.columns(3)
            with col1:
                cantidad = st.number_input("Cantidad Física Contada (Solo Enteros):", min_value=0, step=1, value=0)
            with col2:
                lote = st.text_input("Número de Lote (Mandatorio):").strip()
            with col3:
                etiqueta = st.text_input("Número de Etiqueta (Mandatorio):").strip()
                
            obs = st.text_area("Notas / Desvíos específicos del material:").strip()
            
            if st.form_submit_button("🚀 Transmitir Registro a la Nube"):
                # Validación usando la variable persistente 'contador' sacada de arriba
                if not contador:
                    st.error("Error: Debe completar el 'Nombre del Operario' arriba antes de transmitir.")
                elif not lote or not etiqueta:
                    st.error("Error: Los campos Lote y Etiqueta son obligatorios para el registro.")
                else:
                    try:
                        payload = {
                            "contador": str(contador),
                            "comentarios_generales": str(comentarios_gen),
                            "material": str(item["Material"]),
                            "descripcion": str(item["Descripcion"]),
                            "sector": str(item["Sector"]),
                            "cantidad_contada": int(cantidad),
                            "lote": str(lote),
                            "numero_etiqueta": str(etiqueta),
                            "observaciones": str(obs),
                            "tipo": "CONTEO"
                        }
                        supabase.table("conteos_inventario").insert(payload).execute()
                        st.success(f"✓ Conteo de {item['Material']} subido con éxito.")
                        
                        # Limpiar solo la selección del ítem actual para forzar nueva búsqueda
                        del st.session_state.item_seleccionado
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fallo en la base de datos: {e}")

    st.markdown("---")
    st.subheader("⚠️ Registro de Artículo No Encontrado")
    with st.form("form_no_maestro", clear_on_submit=True):
        desc_no = st.text_area("Describa el material hallado:").strip()
        foto = st.file_uploader("Captura de cámara / Evidencia:", type=["png", "jpg", "jpeg"])
        
        if st.form_submit_button("Guardar Alerta de No Encontrado"):
            if not contador or not desc_no or not foto:
                st.error("Error: Todos los campos son obligatorios.")
            else:
                try:
                    ext = foto.name.split(".")[-1]
                    uuid_name = f"{uuid.uuid4()}.{ext}"
                    supabase.storage.from_("fotos").upload(uuid_name, foto.read(), {"content-type": f"image/{ext}"})
                    foto_url = supabase.storage.from_("fotos").get_public_url(uuid_name)
                    
                    payload = {
                    "contador": str(contador), 
                    "comentarios_generales": str(comentarios_gen) if comentarios_gen else "",
                    "material": "N/A", 
                    "descripcion": "No encontrado", 
                    "sector": "N/A",
                    "cantidad_contada": 0, 
                    "lote": "N/A", 
                    "numero_etiqueta": "N/A",
                    "observaciones": str(desc_no),  # <--- CORREGIDO ACÁ
                    "tipo": "NO_ENCONTRADO", 
                    "foto_url": str(foto_url)
                }
                    supabase.table("conteos_inventario").insert(payload).execute()
                    st.success("✓ Reporte enviado a la nube.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

else:
    # --- MÓDULO SUPERVISOR ---
    st.title("📊 Consola Central (Tiempo Real)")
    try:
        records = supabase.table("conteos_inventario").select("*").order("timestamp", desc=True).execute().data
    except Exception as e:
        st.error(f"Error: {e}")
        records = []
        
    if records:
        df_realtime = pd.DataFrame(records)
        if "timestamp" in df_realtime.columns:
            df_realtime["timestamp"] = pd.to_datetime(df_realtime["timestamp"]).dt.strftime('%Y-%m-%d %H:%M:%S')
            
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Registros", len(df_realtime))
        m2.metric("Items Únicos", df_realtime["material"].nunique() if "material" in df_realtime.columns else 0)
        m3.metric("Operadores", df_realtime["contador"].nunique() if "contador" in df_realtime.columns else 0)
        m4.metric("No Encontrados", len(df_realtime[df_realtime["tipo"]=="NO_ENCONTRADO"]) if "tipo" in df_realtime.columns else 0)
        
        st.markdown("---")
        f_col1, f_col2 = st.columns(2)
        with f_col1:
            filt_user = st.multiselect("Filtrar Operario:", options=sorted(df_realtime["contador"].unique()))
        with f_col2:
            filt_tipo = st.multiselect("Filtrar Tipo:", options=df_realtime["tipo"].unique(), default=df_realtime["tipo"].unique())
            
        if filt_user: df_realtime = df_realtime[df_realtime["contador"].isin(filt_user)]
        if filt_tipo: df_realtime = df_realtime[df_realtime["tipo"].isin(filt_tipo)]
            
        order_cols = ["timestamp", "contador", "material", "descripcion", "sector", "cantidad_contada", "lote", "numero_etiqueta", "observations", "tipo", "foto_url"]
        df_final = df_realtime[[c for c in order_cols if c in df_realtime.columns]]
        
        if "cantidad_contada" in df_final.columns:
            df_final["cantidad_contada"] = df_final["cantidad_contada"].astype(int)
        
        csv_bytes = df_final.to_csv(index=False, sep=";", encoding="utf-8-sig").encode('utf-8-sig')
        st.download_button(label="🟢 Descargar Consolidado Excel (.csv)", data=csv_bytes, file_name="Inventario_Daimler.csv", mime="text/csv")
        st.dataframe(df_final, use_container_width=True, height=450)
    else:
        st.info("Sin registros almacenados aún.")
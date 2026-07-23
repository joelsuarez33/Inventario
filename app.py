import re

import pandas as pd
import streamlit as st
from supabase import Client, create_client

st.set_page_config(
    page_title="Sistema de Inventario Daimler",
    layout="wide",
    initial_sidebar_state="expanded",
)

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

# Debe ser <= "Max Rows" en Settings > API del proyecto (default: 1000).
# No requiere modificar la configuración de Supabase.
PAGE_SIZE = 1000

COLS_MAESTRO = ["Material", "Descripcion", "Sector", "Cantidad_teorica"]


# =====================================================================
# BÚSQUEDA SERVER-SIDE DEL MAESTRO
# Reemplaza la carga completa de la tabla: el filtrado lo hace Postgres
# sobre las 100k filas. Requiere índices pg_trgm (ver SQL adjunto).
# =====================================================================
def _sanitize_ilike(term: str) -> str:
    # "," y "()" son reservados en la sintaxis de or_() de PostgREST.
    # "%" se remueve para que el usuario no inyecte wildcards propios.
    return re.sub(r"[,()%]", "", term).strip()


@st.cache_data(ttl=300, show_spinner=False)
def _query_maestro(q: str, limit: int) -> list:
    # Cacheado por término 5 min: búsquedas repetidas entre los 15
    # operarios no golpean la DB. Excepciones no se cachean.
    res = (
        supabase.table("maestro_inventario")
        .select("*")
        .or_(f"material.ilike.%{q}%,descripcion.ilike.%{q}%,sector.ilike.%{q}%")
        .limit(limit)
        .execute()
    )
    return res.data or []


def buscar_material(query: str, limit: int = 5) -> pd.DataFrame:
    q = _sanitize_ilike(query)
    if len(q) < 2:
        return pd.DataFrame(columns=COLS_MAESTRO)
    try:
        rows = _query_maestro(q, limit)
    except Exception as e:
        st.error(f"Error técnico en la búsqueda: {e}")
        return pd.DataFrame(columns=COLS_MAESTRO)

    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLS_MAESTRO)
    df.columns = [c.capitalize() for c in df.columns]
    return df.fillna("")


# =====================================================================
# CARGA PAGINADA DE CONTEOS (módulo Supervisor)
# select("*") sin range() truncaba en 1000 filas: mismo bug que el
# maestro. Con 15 operarios el histórico supera 1000 rápido y la
# consola + el CSV descargaban datos incompletos.
# =====================================================================
@st.cache_data(ttl=30, show_spinner="Cargando registros...")
def cargar_conteos() -> pd.DataFrame:
    all_rows, start = [], 0
    while True:
        res = (
            supabase.table("conteos_inventario")
            .select("*")
            .order("timestamp", desc=True)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        chunk = res.data or []
        all_rows.extend(chunk)
        if len(chunk) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    df = pd.DataFrame(all_rows)
    # Inserts concurrentes durante la paginación pueden duplicar filas
    # entre páginas: dedupe por PK.
    if not df.empty and "id" in df.columns:
        df = df.drop_duplicates(subset="id")
    return df


# --- INTERFAZ DE USUARIO ---
modo = st.sidebar.radio("Módulo de Trabajo:", ["Operario (Carga de Conteo)", "Supervisor (Monitoreo y Descarga)"])

if modo == "Operario (Carga de Conteo)":
    st.title("📱 Captura de Inventario en Campo")
    st.markdown("---")

    # --- BLOQUE FIJO: Datos del Operario y Sector (Persistentes) ---
    st.subheader("👤 Datos de Control (Fijos para la sesión)")

    if "op_nombre" not in st.session_state:
        st.session_state.op_nombre = ""
    if "op_comentarios" not in st.session_state:
        st.session_state.op_comentarios = ""

    c_hdr1, c_hdr2 = st.columns([1, 2])
    with c_hdr1:
        contador = st.text_input("Nombre del Operario:", key="op_nombre", placeholder="Ej: Joel Suarez").strip()
    with c_hdr2:
        comentarios_gen = st.text_input("Sector (Obligatorio):", key="op_comentarios", placeholder="Ej: Almacén A / Pasillo 4").strip()

    st.markdown("---")

    # Buscador server-side: la query viaja a Postgres, vuelven <= 5 filas
    buscar = st.text_input("🔍 Buscar por Material, Descripción o Sector (escriba y presione Enter):", value="")

    if buscar:
        filtrado = buscar_material(buscar)
        if not filtrado.empty:
            st.write("**Coincidencias en tiempo real:**")
            for idx, row in filtrado.reset_index(drop=True).iterrows():
                if st.button(
                    f"📦 {row['Material']} | {row['Descripcion']} | Sector: {row['Sector']}",
                    key=f"item_{idx}_{row['Material']}",
                ):
                    st.session_state.item_seleccionado = row.to_dict()
        elif len(_sanitize_ilike(buscar)) < 2:
            st.info("Ingrese al menos 2 caracteres para buscar.")
        else:
            st.warning("No se encontraron coincidencias en el maestro.")

    # FORMULARIO VARIABLES DEL ÍTEM
    if "item_seleccionado" in st.session_state:
        item = st.session_state.item_seleccionado
        st.markdown(f"### Artículo Seleccionado: `{item['Material']}`")
        st.info(f"**Descripción:** {item['Descripcion']}  \n**Sector Teórico:** {item['Sector']}")

        with st.form("form_transmision"):
            st.markdown("##### Datos del Conteo Físico")
            col1, col2, col3 = st.columns(3)
            with col1:
                cantidad = st.number_input("Cantidad Física Contada (Solo Enteros):", min_value=0, step=1, value=0)
            with col2:
                lote = st.text_input("Número de Lote (Mandatorio):").strip()
            with col3:
                etiqueta = st.text_input("Número de Etiqueta (Mandatorio):").strip()
            metodo_conteo = st.selectbox(
                "Método de Conteo (Mandatorio):",
                options=["", "Manual", "Cajón cerrado", "Balanza"],
                index=0,
            )
            obs = st.text_area("Notas / Desvíos específicos del material:").strip()

            if st.form_submit_button("🚀 Transmitir Registro a la Nube"):
                if not contador:
                    st.error("Error: Debe completar el 'Nombre del Operario' arriba antes de transmitir.")
                elif not comentarios_gen:
                    st.error("Error: Debe completar el campo 'Sector' antes de transmitir.")
                elif not lote or not etiqueta:
                    st.error("Error: Los campos Lote y Etiqueta son obligatorios para el registro.")
                elif not metodo_conteo:
                    st.error("Error: Debe seleccionar un 'Método de Conteo'.")
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
                            "metodo_conteo": str(metodo_conteo),
                            "observaciones": str(obs),
                            "tipo": "CONTEO",
                        }
                        supabase.table("conteos_inventario").insert(payload).execute()
                        st.success(f"✓ Conteo de {item['Material']} subido con éxito.")

                        # Registro normal: descarta cualquier etiqueta residual
                        # tipeada en el bloque "No Encontrado" para no arrastrar
                        # valores viejos a un reporte posterior.
                        st.session_state.pop("ne_etiqueta", None)
                        del st.session_state.item_seleccionado
                        st.rerun()
                    except Exception as e:
                        st.error(f"Fallo en la base de datos: {e}")

    st.markdown("---")
    st.subheader("⚠️ Registro de Artículo No Encontrado")
    with st.form("form_no_maestro", clear_on_submit=True):
        desc_no = st.text_area("Describa el material hallado:").strip()
        # Obligatorio SOLO para este formulario. El conteo normal usa su
        # propia etiqueta dentro de form_transmision; este campo no participa.
        etiqueta_no = st.text_input("Número de Etiqueta (Mandatorio):", key="ne_etiqueta").strip()
        st.info("📸 Tomar foto para documentar y enviar luego a la coordinación de inventario.")

        if st.form_submit_button("Guardar Alerta de No Encontrado"):
            if not contador or not comentarios_gen or not desc_no or not etiqueta_no:
                st.error("Error: Operario, Sector, Descripción y Número de Etiqueta son obligatorios.")
            else:
                try:
                    payload = {
                        "contador": str(contador),
                        "comentarios_generales": str(comentarios_gen),
                        "material": "N/A",
                        "descripcion": "No encontrado",
                        "sector": "N/A",
                        "cantidad_contada": 0,
                        "lote": "N/A",
                        "numero_etiqueta": str(etiqueta_no),
                        "metodo_conteo": "N/A",
                        "observaciones": str(desc_no),
                        "tipo": "NO_ENCONTRADO",
                    }
                    supabase.table("conteos_inventario").insert(payload).execute()
                    st.success("✓ Reporte enviado a la nube.")
                except Exception as e:
                    st.error(f"Error: {e}")

else:
    # --- MÓDULO SUPERVISOR ---
    col_ttl, col_btn = st.columns([5, 1])
    with col_ttl:
        st.title("📊 Consola Central (Tiempo Real)")
    with col_btn:
        if st.button("🔄 Actualizar"):
            cargar_conteos.clear()
            st.rerun()

    try:
        df_realtime = cargar_conteos()
    except Exception as e:
        st.error(f"Error: {e}")
        df_realtime = pd.DataFrame()

    if not df_realtime.empty:
        if "timestamp" in df_realtime.columns:
            df_realtime["timestamp"] = pd.to_datetime(df_realtime["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Registros", len(df_realtime))
        m2.metric("Items Únicos", df_realtime["material"].nunique() if "material" in df_realtime.columns else 0)
        m3.metric("Operadores", df_realtime["contador"].nunique() if "contador" in df_realtime.columns else 0)
        m4.metric("No Encontrados", len(df_realtime[df_realtime["tipo"] == "NO_ENCONTRADO"]) if "tipo" in df_realtime.columns else 0)

        st.markdown("---")
        f_col1, f_col2 = st.columns(2)
        with f_col1:
            filt_user = st.multiselect("Filtrar Operario:", options=sorted(df_realtime["contador"].unique()))
        with f_col2:
            filt_tipo = st.multiselect("Filtrar Tipo:", options=df_realtime["tipo"].unique(), default=df_realtime["tipo"].unique())

        if filt_user:
            df_realtime = df_realtime[df_realtime["contador"].isin(filt_user)]
        if filt_tipo:
            df_realtime = df_realtime[df_realtime["tipo"].isin(filt_tipo)]

        order_cols = ["timestamp", "contador", "comentarios_generales", "material", "descripcion", "sector", "cantidad_contada", "lote", "numero_etiqueta", "metodo_conteo", "observaciones", "tipo", "foto_url"]
        df_final = df_realtime[[c for c in order_cols if c in df_realtime.columns]]

        if "cantidad_contada" in df_final.columns:
            df_final["cantidad_contada"] = df_final["cantidad_contada"].astype(int)

        csv_bytes = df_final.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button(label="🟢 Descargar Consolidado Excel (.csv)", data=csv_bytes, file_name="Inventario_Daimler.csv", mime="text/csv")
        st.dataframe(df_final, use_container_width=True, height=450)
    else:
        st.info("Sin registros almacenados aún.")
